import logging
from datetime import date, datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import generator, models
from app.config import CERTIFICATE_DIR
from app.database import SessionLocal
from app.schemas import JobCreate, Recipient

log = logging.getLogger(__name__)


def _describe_errors(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "recipient"
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts)


def create_job(db: Session, payload: JobCreate) -> models.Job:
    """Validates every recipient and stores the job.

    Valid recipients become 'pending' certificates. Invalid ones are stored
    straight away as 'failed' with the reason, so the client can see them in
    the job status instead of having the whole request rejected.
    """
    job = models.Job(
        event_name=payload.event_name.strip(),
        title=payload.title.strip(),
        issued_by=payload.issued_by.strip(),
        issue_date=payload.issue_date or date.today(),
        total=len(payload.recipients),
    )
    db.add(job)
    db.flush()  # need job.id for certificate numbers

    seen_emails: set[str] = set()
    for position, raw in enumerate(payload.recipients):
        cert = models.Certificate(
            job_id=job.id,
            position=position,
            raw_input=raw if isinstance(raw, dict) else {"value": str(raw)},
            certificate_number=f"CERT-{job.id[:8].upper()}-{position + 1:04d}",
        )
        if isinstance(raw, dict):
            cert.recipient_name = str(raw.get("name"))[:200] if raw.get("name") is not None else None
            cert.recipient_email = str(raw.get("email"))[:320] if raw.get("email") is not None else None

        try:
            recipient = Recipient.model_validate(raw)
        except ValidationError as exc:
            cert.status = models.FAILED
            cert.error = f"Invalid recipient data - {_describe_errors(exc)}"
            job.failed += 1
        else:
            email = recipient.email.lower()
            cert.recipient_name = recipient.name
            cert.recipient_email = recipient.email
            if email in seen_emails:
                cert.status = models.FAILED
                cert.error = "Invalid recipient data - duplicate email in this request"
                job.failed += 1
            else:
                seen_emails.add(email)
                cert.status = models.PENDING
        db.add(cert)

    if job.failed == job.total:
        job.status = models.FAILED
        job.completed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(job)
    return job


def _finish(job: models.Job) -> None:
    if job.failed == job.total:
        job.status = models.FAILED
    elif job.failed > 0:
        job.status = models.COMPLETED_WITH_ERRORS
    else:
        job.status = models.COMPLETED
    job.completed_at = datetime.now(timezone.utc)


def process_job(job_id: str) -> None:
    """Generates all pending certificates of a job.

    Runs in the background with its own DB session. Every certificate is
    committed on its own, so progress is visible while the job runs and an
    error in one certificate only affects that one.
    """
    db = SessionLocal()
    try:
        job = db.get(models.Job, job_id)
        if job is None or job.status in (models.COMPLETED, models.COMPLETED_WITH_ERRORS, models.FAILED):
            return

        job.status = models.PROCESSING
        db.commit()

        pending = db.scalars(
            select(models.Certificate)
            .where(models.Certificate.job_id == job_id, models.Certificate.status == models.PENDING)
            .order_by(models.Certificate.position)
        ).all()

        for cert in pending:
            path = CERTIFICATE_DIR / job_id / f"{cert.id}.pdf"
            try:
                generator.generate_certificate_pdf(
                    path,
                    recipient_name=cert.recipient_name,
                    title=job.title,
                    event_name=job.event_name,
                    issued_by=job.issued_by,
                    issue_date=job.issue_date,
                    certificate_number=cert.certificate_number,
                )
            except Exception as exc:  # one bad certificate must not stop the rest
                log.exception("certificate %s failed", cert.id)
                db.rollback()
                cert.status = models.FAILED
                cert.error = f"Generation failed - {exc}"
                job.failed += 1
            else:
                cert.status = models.GENERATED
                cert.file_path = str(path)
                job.succeeded += 1
            db.commit()

        _finish(job)
        db.commit()
    except Exception:
        # something outside the per-certificate loop broke (e.g. DB down)
        log.exception("job %s crashed", job_id)
        db.rollback()
        job = db.get(models.Job, job_id)
        if job is not None:
            job.status = models.FAILED
            db.commit()
    finally:
        db.close()
