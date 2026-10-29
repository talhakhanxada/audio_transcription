import io
import importlib.util
import shutil
import subprocess
import tempfile
import uuid
import wave
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from redis.exceptions import ConnectionError
from rq.exceptions import NoSuchJobError

from .audio import InvalidAudio, chunks, duration, normalize
from .models import Transcription
from .tasks import on_failure, transcribe


def wav_bytes(seconds=3, rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * seconds * rate)
    return buffer.getvalue()


class PipelineTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = override_settings(MEDIA_ROOT=self.directory.name, TRANSCRIPTION_BACKEND="mock", CHUNK_SECONDS=2)
        settings.enable()
        self.addCleanup(settings.disable)

    def audio(self, name="speech.wav"):
        return SimpleUploadedFile(name, wav_bytes(), content_type="audio/wav")

    def job(self, **fields):
        return Transcription.objects.create(
            audio=self.audio(), original_name="speech.wav", backend="mock", chunk_seconds=2, **fields,
        )

    @patch("transcriptions.views.enqueue")
    def test_upload_returns_job_and_queues_it(self, enqueue):
        response = self.client.post("/transcriptions/", {"audio": self.audio("speech.MP3")})
        self.assertEqual(response.status_code, 202)
        job = Transcription.objects.get(pk=response.json()["id"])
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.chunk_seconds, 2)
        self.assertTrue(Path(job.audio.path).exists())
        enqueue.assert_called_once()

    def test_invalid_uploads(self):
        for data in ({}, {"audio": SimpleUploadedFile("empty.wav", b"")}, {"audio": self.audio("file.txt")}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post("/transcriptions/", data).status_code, 400)
        with override_settings(MAX_UPLOAD_BYTES=2):
            self.assertEqual(self.client.post("/transcriptions/", {"audio": self.audio()}).status_code, 413)
        self.assertFalse(Transcription.objects.exists())

    @patch("transcriptions.views.enqueue", side_effect=ConnectionError("offline"))
    def test_queue_failure_keeps_audio_for_retry(self, enqueue):
        response = self.client.post("/transcriptions/", {"audio": self.audio()})
        self.assertEqual(response.status_code, 503)
        job = Transcription.objects.get()
        self.assertEqual(job.status, "failed")
        self.assertTrue(Path(job.audio.path).exists())
        with patch("transcriptions.views.enqueue"):
            self.assertEqual(self.client.post(f"/transcriptions/{job.id}/retry/").status_code, 202)
            self.assertEqual(self.client.post(f"/transcriptions/{job.id}/retry/").status_code, 409)

    @patch("transcriptions.tasks.normalize", side_effect=lambda source, target: shutil.copyfile(source, target))
    def test_pipeline_chunks_timestamps_and_result(self, normalize):
        job = self.job()
        transcribe(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.chunks_done, 2)
        self.assertEqual(job.duration_seconds, 3)
        self.assertEqual([s["start"] for s in job.segments], [0, 2])
        self.assertEqual(job.segments[-1]["end"], 3)
        response = self.client.get(f"/transcriptions/{job.id}/result/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["word_count"], 14)
        self.assertEqual(response.json()["backend"], "mock")
        transcribe(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.attempts, 1)

    @patch("transcriptions.tasks.normalize", side_effect=lambda source, target: shutil.copyfile(source, target))
    def test_resume_after_failure_does_not_duplicate_completed_chunks(self, normalize):
        job = self.job()
        with patch("transcriptions.tasks.recognize", side_effect=[
            [{"start": 0, "end": 2, "text": "  first   chunk "}], RuntimeError("model unavailable"),
        ]):
            with self.assertRaises(RuntimeError):
                transcribe(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.chunks_done, 1)
        with patch("transcriptions.tasks.recognize", return_value=[{"start": 0, "end": 1, "text": "second chunk"}]) as recognize:
            transcribe(str(job.id))
            recognize.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.text, "first chunk second chunk")
        self.assertEqual(job.attempts, 2)
        self.assertEqual(len(job.segments), 2)

    @patch("transcriptions.tasks.normalize", side_effect=InvalidAudio("Invalid file"))
    def test_corrupt_audio_fails_without_automatic_retry(self, normalize):
        job = self.job()
        transcribe(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error, "Invalid file")

    def test_failure_callback_sets_retry_and_terminal_states(self):
        job = self.job()
        rq_job = SimpleNamespace(args=[str(job.id)], retries_left=1)
        on_failure(rq_job, None, RuntimeError, RuntimeError("test failure"), None)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        rq_job.retries_left = 0
        on_failure(rq_job, None, RuntimeError, RuntimeError("test failure"), None)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    def test_not_found_unready_and_methods(self):
        self.assertEqual(self.client.get(f"/transcriptions/{uuid.uuid4()}/").status_code, 404)
        job = self.job()
        self.assertEqual(self.client.get(f"/transcriptions/{job.id}/result/").status_code, 409)
        self.assertEqual(self.client.get("/transcriptions/").status_code, 405)

    @patch("transcriptions.views.Job.fetch", return_value=SimpleNamespace(is_failed=True))
    def test_abandoned_job_can_be_retried(self, fetch):
        job = self.job(status="processing", queue_id="abandoned")
        response = self.client.get(f"/transcriptions/{job.id}/")
        self.assertEqual(response.json()["status"], "failed")
        with patch("transcriptions.views.enqueue"):
            self.assertEqual(self.client.post(f"/transcriptions/{job.id}/retry/").status_code, 202)

    @patch("transcriptions.views.Job.fetch", side_effect=NoSuchJobError)
    def test_missing_recent_queue_job_is_not_failed_during_enqueue(self, fetch):
        job = self.job(queue_id="not-yet-enqueued")
        self.assertEqual(self.client.get(f"/transcriptions/{job.id}/").json()["status"], "pending")

    def test_crash_before_enqueue_is_recoverable(self):
        job = self.job()
        Transcription.objects.filter(pk=job.id).update(updated_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(self.client.get(f"/transcriptions/{job.id}/").json()["status"], "failed")

    @patch("transcriptions.tasks.normalize", side_effect=lambda source, target: shutil.copyfile(source, target))
    def test_duration_limit_is_a_permanent_failure(self, normalize):
        job = self.job()
        with override_settings(MAX_AUDIO_SECONDS=1):
            transcribe(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.chunks_done, 0)

    def test_chunk_memory_is_bounded_and_final_chunk_is_short(self):
        source = Path(self.directory.name) / "source.wav"
        target = Path(self.directory.name) / "chunk.wav"
        source.write_bytes(wav_bytes(5))
        results = [(index, offset, duration(path)) for index, offset, path in chunks(source, target, 2)]
        self.assertEqual(results, [(0, 0, 2), (1, 2, 2), (2, 4, 1)])

    def test_whisper_audio_decoder_dependency_compatibility(self):
        if importlib.util.find_spec("faster_whisper") is None:
            self.skipTest("faster-whisper is not installed")
        from faster_whisper.audio import decode_audio

        decoded = decode_audio(io.BytesIO(wav_bytes(1)))
        self.assertEqual(decoded.shape, (16000,))

    def test_ffmpeg_decodes_real_formats_and_rejects_corrupt_audio(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg is not installed")
        source = Path(self.directory.name) / "source.wav"
        target = Path(self.directory.name) / "normalized.wav"
        source.write_bytes(wav_bytes(1, rate=8000))
        for extension in ("wav", "mp3", "flac", "m4a", "ogg"):
            with self.subTest(extension=extension):
                encoded = source
                if extension != "wav":
                    encoded = source.with_suffix("." + extension)
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), str(encoded)], check=True)
                normalize(encoded, target)
                with wave.open(str(target)) as output:
                    self.assertEqual(output.getframerate(), 16000)
                    self.assertEqual(output.getnchannels(), 1)
        source.write_bytes(b"not audio")
        with self.assertRaises(InvalidAudio):
            normalize(source, target)
