import uuid
from django.conf import settings
from redis import Redis
from rq import Queue, Retry


def connection():
    return Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=5)


def enqueue(job):
    from .tasks import transcribe, on_failure

    job.queue_id = uuid.uuid4().hex
    job.save(update_fields=["queue_id", "updated_at"])
    Queue("transcriptions", connection=connection()).enqueue(
        transcribe, str(job.id), job_id=job.queue_id,
        job_timeout="6h", retry=Retry(max=2, interval=[10, 30]),
        on_failure=on_failure, result_ttl=86400, failure_ttl=604800,
    )
