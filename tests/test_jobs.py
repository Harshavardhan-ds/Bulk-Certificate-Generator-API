import io
import zipfile

from pypdf import PdfReader

from app import generator, models, service
from app.schemas import JobCreate


# ---------- creating a job ----------

def test_create_job_returns_202_and_job_info(client, payload):
    res = client.post("/jobs", json=payload)
    assert res.status_code == 202
    body = res.json()
    assert body["id"]
    assert body["total"] == 3
    assert body["event_name"] == "Python Bootcamp 2026"
    assert body["title"] == "Certificate of Completion"  # default


def test_job_is_stored_as_pending_before_processing(db, payload):
    # call the service directly so the background step doesn't run yet
    job = service.create_job(db, JobCreate(**payload))
    assert job.status == models.PENDING
    assert job.total == 3
    assert job.succeeded == 0 and job.failed == 0


# ---------- validation ----------

def test_empty_recipient_list_is_rejected(client, payload):
    payload["recipients"] = []
    assert client.post("/jobs", json=payload).status_code == 422


def test_missing_job_fields_are_rejected(client, payload):
    del payload["event_name"]
    assert client.post("/jobs", json=payload).status_code == 422


def test_too_many_recipients_is_rejected(client, payload):
    payload["recipients"] = [{"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(1001)]
    assert client.post("/jobs", json=payload).status_code == 422


def test_invalid_recipients_fail_individually(client, payload):
    payload["recipients"] += [
        {"name": "No Email"},
        {"name": "  ", "email": "blank@example.com"},
        {"name": "Bad Email", "email": "not-an-email"},
        "just a string",
    ]
    job_id = client.post("/jobs", json=payload).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()

    assert job["status"] == "completed_with_errors"
    assert job["succeeded"] == 3
    assert job["failed"] == 4
    assert {f["position"] for f in job["failures"]} == {3, 4, 5, 6}
    assert all("Invalid recipient data" in f["error"] for f in job["failures"])


def test_duplicate_email_in_same_request_is_flagged(client, payload):
    payload["recipients"].append({"name": "Asha Again", "email": "ASHA@example.com"})
    job = client.get(f"/jobs/{client.post('/jobs', json=payload).json()['id']}").json()
    assert job["succeeded"] == 3
    assert job["failed"] == 1
    assert "duplicate" in job["failures"][0]["error"]


def test_job_with_only_invalid_recipients_is_failed(client, payload):
    payload["recipients"] = [{"name": "x"}, {"email": "y@example.com"}]
    job = client.get(f"/jobs/{client.post('/jobs', json=payload).json()['id']}").json()
    assert job["status"] == "failed"
    assert job["failed"] == 2
    assert job["completed_at"] is not None


# ---------- certificate generation ----------

def test_generator_writes_a_pdf_with_recipient_details(tmp_path):
    out = tmp_path / "sample.pdf"
    from datetime import date

    generator.generate_certificate_pdf(
        out,
        recipient_name="Asha Verma",
        title="Certificate of Completion",
        event_name="Python Bootcamp 2026",
        issued_by="Acme Academy",
        issue_date=date(2026, 5, 20),
        certificate_number="CERT-TEST-0001",
    )
    assert out.exists()
    assert not (tmp_path / "sample.pdf.tmp").exists()
    text = PdfReader(str(out)).pages[0].extract_text()
    for expected in ("Asha Verma", "Python Bootcamp 2026", "Acme Academy", "CERT-TEST-0001", "20 May 2026"):
        assert expected in text


def test_job_generates_one_file_per_recipient(client, payload, db):
    job_id = client.post("/jobs", json=payload).json()["id"]
    certs = db.query(models.Certificate).filter_by(job_id=job_id).all()
    assert len(certs) == 3
    for cert in certs:
        assert cert.status == models.GENERATED
        with open(cert.file_path, "rb") as f:
            assert f.read(4) == b"%PDF"


# ---------- status / progress ----------

def test_status_reports_progress_when_completed(client, payload):
    job_id = client.post("/jobs", json=payload).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "completed"
    assert job["succeeded"] == 3
    assert job["failed"] == 0
    assert job["pending"] == 0
    assert job["progress_percent"] == 100.0
    assert job["completed_at"] is not None
    assert job["download_all_url"]


def test_progress_while_job_is_still_pending(client, db, payload):
    job = service.create_job(db, JobCreate(**{**payload, "recipients": payload["recipients"] + [{"name": "bad"}]}))
    body = client.get(f"/jobs/{job.id}").json()
    assert body["status"] == "pending"
    assert body["total"] == 4
    assert body["failed"] == 1   # the invalid one is known immediately
    assert body["pending"] == 3
    assert body["progress_percent"] == 25.0


def test_unknown_job_returns_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404


# ---------- one certificate failing ----------

def test_generation_error_does_not_stop_other_certificates(client, payload, monkeypatch):
    real = generator.generate_certificate_pdf

    def flaky(path, **kwargs):
        if kwargs["recipient_name"] == "Ben Carter":
            raise RuntimeError("disk exploded")
        return real(path, **kwargs)

    monkeypatch.setattr(generator, "generate_certificate_pdf", flaky)

    job_id = client.post("/jobs", json=payload).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()

    assert job["status"] == "completed_with_errors"
    assert job["succeeded"] == 2
    assert job["failed"] == 1
    assert job["failures"][0]["recipient_name"] == "Ben Carter"
    assert "disk exploded" in job["failures"][0]["error"]

    ok = client.get(f"/jobs/{job_id}/certificates", params={"status": "generated"}).json()
    assert {c["recipient_name"] for c in ok["items"]} == {"Asha Verma", "Chitra Rao"}


# ---------- retrieving certificates ----------

def test_list_certificates_with_filter_and_paging(client, payload):
    job_id = client.post("/jobs", json=payload).json()["id"]
    res = client.get(f"/jobs/{job_id}/certificates", params={"limit": 2, "offset": 1}).json()
    assert res["total"] == 3
    assert [c["position"] for c in res["items"]] == [1, 2]
    assert client.get(f"/jobs/{job_id}/certificates", params={"status": "bogus"}).status_code == 422


def test_download_single_certificate(client, payload):
    job_id = client.post("/jobs", json=payload).json()["id"]
    item = client.get(f"/jobs/{job_id}/certificates").json()["items"][0]
    res = client.get(f"/certificates/{item['id']}/download")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")


def test_download_zip_contains_only_generated_certificates(client, payload):
    payload["recipients"].append({"name": "Broken"})
    job_id = client.post("/jobs", json=payload).json()["id"]
    res = client.get(f"/jobs/{job_id}/download")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(res.content)).namelist()
    assert names == ["0001_Asha_Verma.pdf", "0002_Ben_Carter.pdf", "0003_Chitra_Rao.pdf"]


def test_cannot_download_failed_certificate(client, payload):
    payload["recipients"] = [{"name": "No Email"}]
    job_id = client.post("/jobs", json=payload).json()["id"]
    cert = client.get(f"/jobs/{job_id}/certificates").json()["items"][0]
    assert cert["download_url"] is None
    assert client.get(f"/certificates/{cert['id']}/download").status_code == 409
    assert client.get(f"/jobs/{job_id}/download").status_code == 409


def test_download_unknown_certificate_returns_404(client):
    assert client.get("/certificates/nope/download").status_code == 404
