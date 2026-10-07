import io
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models, service
from app.database import get_db
from app.schemas import CertificateList, CertificateOut, JobCreate, JobDetail, JobOut

router = APIRouter()


def _job_out(job: models.Job, request: Request, model=JobOut):
    done = job.succeeded + job.failed
    data = {
        "id": job.id,
        "event_name": job.event_name,
        "title": job.title,
        "issued_by": job.issued_by,
        "issue_date": job.issue_date,
        "status": job.status,
        "total": job.total,
        "succeeded": job.succeeded,
        "failed": job.failed,
        "pending": job.total - done,
        "progress_percent": round(done * 100 / job.total, 1) if job.total else 100.0,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "download_all_url": str(request.url_for("download_job_zip", job_id=job.id)) if job.succeeded else None,
    }
    return model(**data)


def _cert_out(cert: models.Certificate, request: Request) -> CertificateOut:
    out = CertificateOut.model_validate(cert)
    if cert.status == models.GENERATED:
        out.download_url = str(request.url_for("download_certificate", certificate_id=cert.id))
    return out


def _get_job_or_404(db: Session, job_id: str) -> models.Job:
    job = db.get(models.Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    payload: JobCreate, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)
):
    job = service.create_job(db, payload)
    if job.status == models.PENDING:
        background.add_task(service.process_job, job.id)
    return _job_out(job, request)


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    failed = db.scalars(
        select(models.Certificate)
        .where(models.Certificate.job_id == job_id, models.Certificate.status == models.FAILED)
        .order_by(models.Certificate.position)
    ).all()
    detail = _job_out(job, request, model=JobDetail)
    detail.failures = [_cert_out(c, request) for c in failed]
    return detail


@router.get("/jobs/{job_id}/certificates", response_model=CertificateList)
def list_certificates(
    job_id: str,
    request: Request,
    status_filter: str | None = Query(default=None, alias="status", pattern="^(pending|generated|failed)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    _get_job_or_404(db, job_id)
    query = select(models.Certificate).where(models.Certificate.job_id == job_id)
    if status_filter:
        query = query.where(models.Certificate.status == status_filter)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(models.Certificate.position).limit(limit).offset(offset)).all()
    return CertificateList(
        total=total, limit=limit, offset=offset, items=[_cert_out(c, request) for c in rows]
    )


@router.get("/certificates/{certificate_id}/download", name="download_certificate")
def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
    cert = db.get(models.Certificate, certificate_id)
    if cert is None:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if cert.status != models.GENERATED or not cert.file_path or not Path(cert.file_path).exists():
        raise HTTPException(status_code=409, detail=f"Certificate is not available (status: {cert.status})")
    return FileResponse(
        cert.file_path, media_type="application/pdf", filename=f"{cert.certificate_number}.pdf"
    )


@router.get("/jobs/{job_id}/download", name="download_job_zip")
def download_job_zip(job_id: str, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    certs = db.scalars(
        select(models.Certificate).where(
            models.Certificate.job_id == job_id, models.Certificate.status == models.GENERATED
        )
    ).all()
    if not certs:
        raise HTTPException(status_code=409, detail="No generated certificates available yet")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for cert in certs:
            safe = re.sub(r"[^A-Za-z0-9]+", "_", cert.recipient_name or "recipient").strip("_")
            zf.write(cert.file_path, arcname=f"{cert.position + 1:04d}_{safe}.pdf")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job.id[:8]}.zip"'},
    )
