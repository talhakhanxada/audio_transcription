from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from redis.exceptions import RedisError
from rq.exceptions import NoSuchJobError
from rq.job import Job

from .models import Transcription
from .queue import connection, enqueue

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".webm"}


def find_job(job_id):
    return Transcription.objects.filter(pk=job_id).first()


def serialize(job):
    return {
        "id": str(job.id), "filename": job.original_name, "status": job.status,
        "backend": job.backend, "attempts": job.attempts,
        "duration_seconds": job.duration_seconds, "chunks_done": job.chunks_done,
        "error": job.error, "created_at": job.created_at.isoformat(),
        "status_url": f"/transcriptions/{job.id}/",
        "result_url": f"/transcriptions/{job.id}/result/",
    }


def schedule(job):
    try:
        enqueue(job)
    except RedisError:
        job.status = "failed"
        job.error = "Queue unavailable. Retry this job when Redis is running."
        job.save(update_fields=["status", "error", "updated_at"])
        return JsonResponse(serialize(job), status=503)
    return JsonResponse(serialize(job), status=202)


@require_POST
def upload(request):
    audio = request.FILES.get("audio")
    if audio is None or audio.size == 0:
        return JsonResponse({"error": "Provide a non-empty file in the audio field."}, status=400)
    if Path(audio.name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        return JsonResponse({"error": "Unsupported format.", "supported": sorted(SUPPORTED_EXTENSIONS)}, status=400)
    if audio.size > settings.MAX_UPLOAD_BYTES:
        return JsonResponse({"error": "Maximum upload size is 100 MiB."}, status=413)
    job = Transcription.objects.create(
        audio=audio, original_name=audio.name[:255], backend=settings.TRANSCRIPTION_BACKEND,
        chunk_seconds=settings.CHUNK_SECONDS,
    )
    return schedule(job)


def refresh_queue_state(job):
    """Reconcile jobs abandoned by a terminated worker or lost Redis data."""
    if job.status not in {"pending", "processing"}:
        return
    old = (timezone.now() - job.updated_at).total_seconds() > 300
    try:
        failed = Job.fetch(job.queue_id, connection=connection()).is_failed if job.queue_id else old
    except NoSuchJobError:
        # Allow time for enqueue to complete before considering a missing job lost.
        failed = old
    except RedisError:
        return
    if failed:
        job.status = "failed"
        job.error = "Queue job failed or was lost. You can retry from the last saved chunk."
        job.save(update_fields=["status", "error", "updated_at"])


@require_GET
def detail(request, job_id):
    job = find_job(job_id)
    if job is None:
        return JsonResponse({"error": "Job not found."}, status=404)
    refresh_queue_state(job)
    return JsonResponse(serialize(job))


@require_GET
def result(request, job_id):
    job = find_job(job_id)
    if job is None:
        return JsonResponse({"error": "Job not found."}, status=404)
    if job.status != "completed":
        return JsonResponse({"error": "Transcript is not ready.", "status": job.status}, status=409)
    return JsonResponse({"id": str(job.id), "backend": job.backend,
                         "text": job.text, "word_count": job.word_count,
                         "duration_seconds": job.duration_seconds, "segments": job.segments})


@require_POST
def retry(request, job_id):
    job = find_job(job_id)
    if job is None:
        return JsonResponse({"error": "Job not found."}, status=404)
    refresh_queue_state(job)
    # Conditional update ensures two concurrent retry requests cannot both enqueue.
    claimed = Transcription.objects.filter(pk=job_id, status="failed").update(
        status="pending", error="", updated_at=timezone.now(),
    )
    if not claimed:
        return JsonResponse({"error": "Only failed jobs can be retried."}, status=409)
    job.refresh_from_db()
    return schedule(job)
