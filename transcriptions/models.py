import uuid
from django.db import models


class Transcription(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        PROCESSING = "processing"
        COMPLETED = "completed"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    audio = models.FileField(upload_to="audio/%Y/%m/%d/")
    original_name = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    queue_id = models.CharField(max_length=64, blank=True)
    backend = models.CharField(max_length=16)
    attempts = models.PositiveIntegerField(default=0)
    duration_seconds = models.FloatField(null=True)
    chunk_seconds = models.PositiveIntegerField(default=300)
    chunks_done = models.PositiveIntegerField(default=0)
    segments = models.JSONField(default=list)
    text = models.TextField(blank=True)
    word_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
