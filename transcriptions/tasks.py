import logging
import tempfile
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db.models import F
from .audio import InvalidAudio, chunks, duration, normalize
from .models import Transcription

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def whisper_model():
    from faster_whisper import WhisperModel
    return WhisperModel(settings.WHISPER_MODEL, device="cpu", compute_type="int8")


def recognize(path, backend):
    if backend == "mock":
        return [{"start": 0.0, "end": duration(path),
                 "text": "This is a mock transcript for demonstration."}]
    if backend != "whisper":
        raise ValueError("TRANSCRIPTION_BACKEND must be whisper or mock")
    segments, _ = whisper_model().transcribe(str(path), beam_size=1, vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text} for s in segments]


def transcribe(job_id):
    job = Transcription.objects.get(pk=job_id)
    if job.status == Transcription.Status.COMPLETED:
        return
    job.status = Transcription.Status.PROCESSING
    job.attempts = F("attempts") + 1
    job.error = ""
    job.save(update_fields=["status", "attempts", "error", "updated_at"])
    try:
        with tempfile.TemporaryDirectory() as directory:
            normalized = Path(directory) / "normalized.wav"
            normalize(job.audio.path, normalized)
            job.duration_seconds = duration(normalized)
            if not 0 < job.duration_seconds <= settings.MAX_AUDIO_SECONDS:
                raise InvalidAudio("Audio must be non-empty and no longer than 6 hours.")
            job.save(update_fields=["duration_seconds", "updated_at"])
            for index, offset, path in chunks(
                normalized, Path(directory) / "chunk.wav",
                job.chunk_seconds, skip=job.chunks_done,
            ):
                for segment in recognize(path, job.backend):
                    text = " ".join(segment["text"].split())
                    if text:
                        job.segments.append({
                            "start": round(offset + segment["start"], 3),
                            "end": round(offset + segment["end"], 3), "text": text,
                        })
                job.chunks_done = index + 1
                job.save(update_fields=["segments", "chunks_done", "updated_at"])
        job.text = " ".join(segment["text"] for segment in job.segments)
        job.word_count = len(job.text.split())
        job.status = Transcription.Status.COMPLETED
        job.save(update_fields=["text", "word_count", "status", "updated_at"])
    except InvalidAudio as exc:
        job.status = Transcription.Status.FAILED
        job.error = str(exc)
        job.save(update_fields=["status", "error", "updated_at"])
    # Other failures propagate to RQ, which applies bounded retries.


def on_failure(rq_job, connection, exc_type, exc_value, traceback):
    from django.utils import timezone

    logger.error("Transcription %s failed: %s", rq_job.args[0], exc_value)
    retrying = bool(rq_job.retries_left)
    Transcription.objects.filter(pk=rq_job.args[0]).exclude(status="completed").update(
        status="pending" if retrying else "failed",
        error="Temporary processing failure; retry scheduled." if retrying
        else "Processing failed. Check worker logs and retry the job.",
        updated_at=timezone.now(),
    )
