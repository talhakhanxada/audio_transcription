from django.core.management.base import BaseCommand
from django.db import connections
from rq import Queue, Worker
from transcriptions.queue import connection


class Command(BaseCommand):
    help = "Process transcription jobs (run one worker for the SQLite demo)."

    def handle(self, *args, **options):
        redis = connection()
        connections.close_all()
        Worker([Queue("transcriptions", connection=redis)], connection=redis).work(
            with_scheduler=True,
        )
