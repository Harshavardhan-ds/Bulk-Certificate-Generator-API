# Bulk Certificate Generator

A small FastAPI backend that takes a list of recipients in one request and generates a PDF certificate for each of them from a single built-in template. Generation runs in the background and the client can poll the job to see progress, failures, and download the results.

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2, SQLite, ReportLab, pytest.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

The API is at http://127.0.0.1:8000 and the interactive docs (Swagger) at http://127.0.0.1:8000/docs.

Optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certificates.db` | Any SQLAlchemy URL (Postgres works too) |
| `CERTIFICATE_DIR` | `./generated` | Where PDFs are stored |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Upper limit per request |

## Run the tests

```bash
pytest
```

Tests use a temporary SQLite DB and a temporary output folder, so they don't touch your real data.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/jobs` | Submit a bulk generation request (returns `202`) |
| GET | `/jobs/{job_id}` | Status, progress counters and the list of failures |
| GET | `/jobs/{job_id}/certificates` | List certificates (`?status=generated\|failed\|pending&limit=&offset=`) |
| GET | `/certificates/{certificate_id}/download` | Download one PDF |
| GET | `/jobs/{job_id}/download` | Download every generated PDF of the job as a ZIP |

### Submit a request

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "Python Bootcamp 2026",
    "issued_by": "Acme Academy",
    "issue_date": "2026-05-20",
    "recipients": [
      {"name": "Asha Verma", "email": "asha@example.com"},
      {"name": "Ben Carter", "email": "ben@example.com"},
      {"name": "Broken Row"}
    ]
  }'
```

`title` is optional (defaults to "Certificate of Completion") and so is `issue_date` (defaults to today).

Response:

```json
{
  "id": "5b0e...",
  "status": "pending",
  "total": 3, "succeeded": 0, "failed": 1, "pending": 2,
  "progress_percent": 33.3,
  ...
}
```

### Check progress

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

Job statuses: `pending` -> `processing` -> `completed` | `completed_with_errors` | `failed`
(`failed` means *every* recipient failed). The response includes `succeeded`, `failed`, `pending`, `progress_percent` and a `failures` array with the position of each failed recipient in the original list, the data that was sent, and the reason.

### Retrieve certificates

```bash
# list them (each generated one has a download_url)
curl "http://127.0.0.1:8000/jobs/<job_id>/certificates?status=generated"

# one PDF
curl -o cert.pdf http://127.0.0.1:8000/certificates/<certificate_id>/download

# everything in a zip
curl -o certs.zip http://127.0.0.1:8000/jobs/<job_id>/download
```

## Design decisions

**Background processing instead of synchronous generation.** A request can contain up to 1000 recipients, and holding an HTTP connection open while all of them render is fragile (timeouts, retries creating duplicates). `POST /jobs` therefore validates, stores the job and returns `202` immediately; the PDFs are produced afterwards using FastAPI's `BackgroundTasks`. I picked this over Celery/RQ because it needs no extra infrastructure (no broker, no worker process) and fits the size of this project. The trade-off is that a job in progress is lost if the server restarts, and there is no retry/scaling story. The processing code lives in `service.process_job(job_id)` and only needs a job id, so moving it to a real queue later is a small change: enqueue that function instead of `background.add_task`.

**Validation happens at two levels.**
- Request level (Pydantic): missing `event_name`/`issued_by`, an empty list, or more than `MAX_RECIPIENTS_PER_JOB` recipients -> `422`, nothing is stored.
- Recipient level: each recipient (`name` required and non-blank, valid `email`, no duplicate email within the request) is validated on its own. A bad row doesn't reject the request; it's saved as a `failed` certificate with a readable error, so the client sees exactly which rows were wrong while the valid ones still get generated. Recipients are typed as a loose list in the request schema on purpose, so that this per-row handling is possible.

**Failure isolation.** Each certificate is generated inside its own `try/except` and committed individually. An exception only marks that certificate as `failed` (with the error message) and the loop moves on. Committing per certificate also makes the progress counters live while the job is running. PDFs are written to a `.tmp` file and renamed, so a half-written file is never mistaken for a finished certificate.

**Data model.** `jobs` (settings + counters + status) and `certificates` (one row per recipient: position, input, certificate number, status, error, file path). Counters are stored on the job so the status endpoint is a single cheap lookup, even for big jobs.

**Template.** One landscape A4 template drawn with ReportLab (no external image/font files to manage). Long names/event titles shrink to fit. Each certificate gets a number like `CERT-5B0E1A2C-0007`.

**Storage.** PDFs are saved on local disk under `generated/<job_id>/<certificate_id>.pdf` and the DB stores the path. For production this would be object storage (S3 etc.).

## Known limitations / what I'd do next

- In-process background tasks: unfinished jobs aren't resumed after a restart. A startup task that re-queues `pending` certificates, or a real queue (Celery/RQ/arq), would fix that.
- No authentication or rate limiting.
- The template uses the built-in Helvetica font, which covers Latin characters only. Names in other scripts need a bundled TTF font registered with ReportLab.
- The ZIP is built in memory, which is fine for ~1000 small PDFs but should be streamed for much larger jobs.
- No schema migrations (tables are created at startup); Alembic would be the next step.
- Possible extras: a "retry failed certificates" endpoint and emailing the certificates to recipients.
