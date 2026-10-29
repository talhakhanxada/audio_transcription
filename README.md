# Simple Django transcription pipeline

A small assignment project: upload audio, receive a job ID, poll its status, and retrieve cleaned text with timestamped segments. Django handles HTTP and stores job metadata; one RQ worker handles the slow audio work.

```text
Client -> Django -> save audio + SQLite job -> Redis queue
                                                |
                                           RQ worker
                                                |
                          FFmpeg -> WAV chunks -> Whisper
                                                |
                              save progress + transcript in SQLite
```

## Approach and key design decisions

I approached the assignment as a complete upload-to-result workflow: accept a recording, process it without blocking the client, preserve progress, and return useful structured text. The focus is a small application whose behavior and failure cases can be explained, using an existing speech model rather than training one.

| Decision | Reason | Tradeoff |
| --- | --- | --- |
| Django with plain JSON views | Keep routing, database models, migrations, and tests in one framework; four operations do not require another API framework. | API documentation and request/response schemas are maintained manually. |
| Background processing with Redis and RQ | Return a job ID quickly while slow transcription runs separately; use built-in scheduling and retries. | Redis and the worker are additional services, and database/queue writes are not atomic. |
| FFmpeg normalization before transcription | Give every downstream step the same audio format and validate the actual recording contents. | Conversion takes time and needs temporary disk space. |
| Whisper `tiny` on CPU with `int8` computation | Run a lightweight local demonstration without a paid API key or GPU requirement. | Recognition accuracy varies with language, noise, and recording quality; larger models cost more processing time and memory. |
| Five-minute chunks with saved checkpoints | Bound audio memory for inference and avoid retranscribing completed chunks after a failure. | Fixed boundaries can split speech; normalization repeats on retry and transcript data still grows in memory. |
| SQLite and shared local file storage | Keep setup and inspection simple while preserving data across container restarts. | Database write contention and local storage limit scaling across machines. |
| One worker | Keep CPU and memory usage predictable for the assignment. | Long jobs delay later jobs; concurrent uploads do not imply simultaneous transcription. |
| JSON results and status polling | Make the API easy to demonstrate with Postman and consume from other applications. | Clients must poll for completion; webhooks are a possible future extension. |
| Docker Compose | Package the API, FFmpeg, worker, queue, migrations, and persistent volumes in one repeatable setup. | Model weights still require an initial download, and persistent volumes need separate backups. |

The sections below explain how these choices answer each assignment question. Production improvements are identified separately from behavior already implemented.

## Run with Docker

Requires Docker with Docker Compose. From this directory:

```sh
docker compose up --build
```

Visit http://localhost:8000/ for the endpoint directory. The `migrate` service creates the database before the API and worker start. Whisper uses the small `tiny` model on CPU by default; its first transcription downloads model weights, so it needs internet access and takes longer. Weights are cached in a named volume. Increase `WHISPER_MODEL` to `base` or `small` for a quality/speed tradeoff.

For a quick demonstration without model inference/downloads:

```sh
TRANSCRIPTION_BACKEND=mock docker compose up --build
```

Mock mode still decodes and chunks real audio, but produces fixed demonstration text per chunk. Responses explicitly report `"backend": "mock"`. Jobs retain their original backend and chunk size when retried.

Optional settings are listed in `.env.example`; Compose automatically reads a file named `.env`. Stop with `docker compose down`. Named volumes keep the database, uploaded audio, Redis queue, and model cache across restarts.

## Try the API

Upload a file (replace the path):

```sh
curl -X POST http://localhost:8000/transcriptions/ \
  -F 'audio=@/path/to/recording.mp3'
```

Returns HTTP **202** with an ID and relative status/result URLs:

```json
{
  "id": "<job-id>",
  "status": "pending",
  "backend": "whisper",
  "status_url": "/transcriptions/<job-id>/",
  "result_url": "/transcriptions/<job-id>/result/"
}
```

The actual response also includes the filename, attempt count, duration, saved chunk count, creation timestamp, and error message.

```sh
curl 'http://localhost:8000/transcriptions/<job-id>/'
curl 'http://localhost:8000/transcriptions/<job-id>/result/'
curl -X POST 'http://localhost:8000/transcriptions/<job-id>/retry/'
```

| Endpoint | Behavior |
| --- | --- |
| `POST /transcriptions/` | Multipart field `audio`; saves and queues a job; 202 on success |
| `GET /transcriptions/<id>/` | Status, progress, and error; 404 for unknown IDs |
| `GET /transcriptions/<id>/result/` | Text, word count, duration, backend, and timestamped segments; 409 until completed |
| `POST /transcriptions/<id>/retry/` | Resumes a failed job; 409 if it is already pending, processing, or completed |

Statuses: `pending -> processing -> completed`, or `failed` on a permanent error/exhausted retries. Automatic retries return to `pending`. An empty successful transcript is valid for audio with no detected speech.

## Explaining the engineering decisions

**How are different audio formats handled?**

The upload endpoint accepts WAV, MP3, M4A, MP4 (audio track), AAC, FLAC, OGG, and WebM, case-insensitively. FFmpeg validates the actual contents and converts the first audio track to mono, 16 kHz, 16-bit PCM WAV. Everything after that handles one consistent format. A renamed or corrupt file fails decoding with a readable error. Files are streamed to disk by Django above 1 MiB; the application rejects files above 100 MiB after upload. A reverse proxy should enforce the body limit before transfer in a deployment.

**How are long files handled?**

Transcription runs outside the HTTP request. The worker writes normalized audio to a temporary disk file, then reads one five-minute chunk at a time. Only the current chunk is passed to Whisper, bounding audio memory use. Timestamps are offset back to the original recording. The job saves its accumulated segments and chunk counter after each chunk, so a retry skips completed chunks. Temporary WAV files are removed when processing exits normally or raises a handled exception; an abruptly killed process can leave files in the worker container's temporary directory.

The demo accepts up to six hours of decoded audio; conversion stops after six hours plus one second so overlong input can be rejected. FFmpeg has a ten-minute timeout and each queue attempt a six-hour timeout. Fixed chunk boundaries can split words and lose context. Silence-aware splitting or overlap with deduplication would improve quality; they are deliberately left out of this small implementation. Normalization repeats on retries, but model inference resumes at the last saved chunk. The transcript JSON is kept in memory, so memory is bounded for audio, not constant for all output.

**How are concurrent uploads handled?**

Django accepts uploads while the worker processes one job at a time. Redis buffers the queue, so transcription does not block upload requests. Gunicorn runs one process with four threads. SQLite transactions are short and have a 30-second lock timeout. This is appropriate for modest assignment/demo traffic. For sustained concurrent writes, use PostgreSQL before adding API processes/workers. More workers consume jobs from the same queue, with concurrency chosen to fit CPU/GPU and memory.

Every upload creates a new job; the API currently has no upload idempotency key or queue-capacity limit. Poll with the status GET endpoint rather than repeatedly submitting the upload POST. A large backlog can leave a healthy job pending with zero attempts. Rate limits, queue admission limits, and idempotency keys would help control this in production.

**How are audio and transcripts stored?**

Original files live under `/app/data/media`; UUID job IDs and Django's file storage avoid relying on filenames as identifiers. SQLite stores status, errors, attempts, progress, transcript text, word count, and JSON segments. The API and worker share the `app-data` Docker volume. Redis has append-only persistence enabled and a separate volume. Production would use object storage for audio, PostgreSQL for metadata/transcripts, plus backups and a retention/deletion policy.

Original recordings are retained so failed jobs can be retried without another upload. Normalized WAVs and chunk files are temporary. The combined text supports direct display, while the timestamped JSON segments support captions or playback navigation. There is no automatic retention cleanup in the demo. Docker volumes preserve files across normal restarts, but they are not backups; deleting the volumes deletes the stored data.

**How do retries and recovery work?**

- Invalid/empty/overlong audio fails immediately without automatic retries.
- Other processing errors get two automatic retries after 10 and 30 seconds, for three total attempts. The RQ worker runs its scheduler to enable these delays. A failure callback updates Django's job status; technical details go to worker logs.
- When Redis is unavailable during upload, the API returns 503 with the saved job ID. The file stays available; retry that same job once the queue is healthy.
- Failed jobs can be retried through the API. A conditional database update prevents concurrent manual retry requests from queuing the same failed job twice. Completed jobs are skipped if delivered again.
- RQ tracks active jobs and detects abandoned executions during worker maintenance. Status/retry requests reconcile jobs that RQ marks failed. Missing queue records are considered lost after five minutes. Retry then resumes from the saved chunk. Detection after a hard worker crash is eventual; it is not instantaneous.

SQLite and Redis do not share a transaction: there is a small save/enqueue crash window. The missing-job check makes it recoverable through manual retry; a production system could use a transactional outbox to guarantee eventual enqueue. This demo does not promise exactly-once execution under every distributed failure. Avoid running duplicate copies of the same job manually.

**How is this exposed as an API?**

The API uses an asynchronous request-and-poll workflow. A client submits multipart form-data with an `audio` file, receives HTTP 202 and a UUID when the job is queued, and polls the status endpoint. Completion is separate from upload acceptance: the result endpoint returns HTTP 200 with transcript text and timestamped segments only after processing finishes. HTTP 409 means the result is not ready or a job is ineligible for retry. The retry endpoint reuses a failed job's existing file and checkpoints.

Missing, empty, or unsupported uploads return 400; oversized uploads return 413; unknown job IDs return 404; unsupported HTTP methods return 405. Queue unavailability returns 503 with the saved job ID so it can be retried later. A corrupt recording with an accepted extension may initially receive 202 and later fail during decoding; clients must check job status rather than treating 202 as transcription success.

Polling was chosen because it is simple to implement and demonstrate in Postman. Each job is independently addressable and result retrieval does not trigger another transcription. Authentication, job ownership checks, versioned API routes, generated API schemas, idempotent upload requests, and completion webhooks are possible production additions; they are not implemented here.

**How is the result processed for downstream use?**

Each segment's whitespace is normalized, empty text is removed, and segments are combined into a transcript. The result endpoint exposes structured JSON with full-recording timestamps and a simple whitespace-based word count. There is no extra summarization model or service.

**Why this stack?**

Django provides the API, ORM, migrations, validation, and test runner. Plain Django views keep the small API easy to follow without another web framework. Redis + RQ provide a small background queue with retries. FFmpeg handles format conversion; faster-whisper runs an open-source speech model locally without a paid API key. faster-whisper and PyAV are pinned to a compatible pair because newer PyAV versions removed the decoder's `metadata_errors` argument.

The API is an unauthenticated local demo, bound to localhost by Compose. A public deployment needs authentication/authorization, HTTPS, upload rate/body limits, and a proper secret key. The root endpoint is an API directory; no separate frontend is needed for this assignment.

## Tests and local development

Inside Docker, with the services running:

```sh
docker compose exec web python manage.py test
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose logs -f worker
```

Tests cover upload validation, queue failure, manual retry rules, transcript output, chunk timestamps, checkpoint recovery, failure callbacks, abandoned jobs, real FFmpeg conversion, and faster-whisper/PyAV decoder compatibility where the dependencies are available. They mock model inference and Redis calls; they require neither model weights nor a live Redis server. The format test is skipped if FFmpeg is unavailable, and the decoder compatibility test is skipped if faster-whisper is not installed. These tests check processing and recovery behavior without relying on recognition accuracy; live speech transcription should also be demonstrated with a real recording.

For development without Docker, install Python 3.12+, FFmpeg, and Redis, then:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
redis-server
```

In two other terminals, activate the virtual environment and run:

```sh
python manage.py runserver
# Separate terminal:
python manage.py worker
```

The local worker command uses RQ's Unix process model; Docker is the recommended path on macOS/Windows. Set `TRANSCRIPTION_BACKEND=mock` for the Django process before uploading when demonstrating mock jobs.

References: [RQ retries](https://python-rq.org/docs/exceptions/), [RQ workers](https://python-rq.org/docs/workers/), [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
