import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [migrations.CreateModel(
        name="Transcription",
        fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("audio", models.FileField(upload_to="audio/%Y/%m/%d/")),
            ("original_name", models.CharField(max_length=255)),
            ("status", models.CharField(choices=[("pending", "Pending"), ("processing", "Processing"), ("completed", "Completed"), ("failed", "Failed")], default="pending", max_length=16)),
            ("queue_id", models.CharField(blank=True, max_length=64)),
            ("backend", models.CharField(max_length=16)),
            ("attempts", models.PositiveIntegerField(default=0)),
            ("duration_seconds", models.FloatField(null=True)),
            ("chunk_seconds", models.PositiveIntegerField(default=300)),
            ("chunks_done", models.PositiveIntegerField(default=0)),
            ("segments", models.JSONField(default=list)),
            ("text", models.TextField(blank=True)),
            ("word_count", models.PositiveIntegerField(default=0)),
            ("error", models.TextField(blank=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
        ],
    )]
