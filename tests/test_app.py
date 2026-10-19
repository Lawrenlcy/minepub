import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, run_worker=False), base_url="http://127.0.0.1") as client:
        yield client


def pdf(encrypted=False):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if encrypted:
        writer.encrypt("password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def upload(client, content=None, **params):
    return client.post("/api/jobs", params={"filename": "测试.pdf", **params},
                       content=pdf() if content is None else content,
                       headers={"content-type": "application/pdf"})


def test_upload_status_cancel_delete(client):
    response = upload(client)
    assert response.status_code == 202
    job = response.json()
    assert job["pages"] == 1
    assert job["status"] == "queued"
    base = "/api/jobs/" + job["id"]
    assert client.get(base).json()["download_url"] is None
    assert client.get(base + "/download").status_code == 409
    assert client.delete(base).status_code == 409
    assert client.post(base + "/cancel").json()["status"] == "cancelled"
    assert client.delete(base).status_code == 204
    assert client.get(base).status_code == 404


@pytest.mark.parametrize("content,params,expected", [
    (b"not PDF", {}, 422), (b"", {}, 422), (b"%PDF-broken", {}, 422),
    (None, {"filename": "bad.html"}, 415), (None, {"tier": "anything"}, 422),
    (None, {"mode": "anything"}, 422), (None, {"output_format": "mobi"}, 422), (pdf(True), {}, 422),
])
def test_invalid_inputs(client, content, params, expected):
    assert upload(client, content, **params).status_code == expected
    assert not [p for p in client.app.state.jobs.root.iterdir() if p.is_dir()]


def test_size_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("MINEPUB_MAX_UPLOAD_MB", "1")
    with TestClient(create_app(tmp_path, run_worker=False), base_url="http://127.0.0.1") as client:
        assert upload(client, b"x" * (1048576 + 1)).status_code == 413
        # No Content-Length: streaming uploads must also be limited.
        assert upload(client, iter([b"x" * 700000, b"x" * 700000])).status_code == 413


def test_queue_limit(client):
    for _ in range(10):
        assert upload(client).status_code == 202
    assert upload(client).status_code == 429


def test_local_access_and_traversal(client):
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 403
    assert client.post("/api/jobs", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.get("/api/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/jobs/not-an-id").status_code == 404
    job = upload(client, filename="../../测试.pdf").json()
    assert job["filename"] == "测试.pdf"
    assert (client.app.state.jobs.root / job["id"] / "input.pdf").exists()


def test_download_and_expiry(client):
    job = upload(client).json()
    store = client.app.state.jobs
    output = store.root / job["id"] / "output.epub"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
    store.update(job["id"], status="completed")
    response = client.get(f'/api/jobs/{job["id"]}/download')
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/epub+zip"
    assert "filename*=utf-8" in response.headers["content-disposition"]
    with store.db() as db:
        db.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time() - 90000, job["id"]))
    store.cleanup()
    assert not output.exists()
    assert client.get(f'/api/jobs/{job["id"]}').status_code == 404


def test_restart_marks_interrupted_but_keeps_queue(client, monkeypatch):
    interrupted = upload(client).json()
    queued = upload(client).json()
    store = client.app.state.jobs
    store.update(interrupted["id"], status="parsing")
    monkeypatch.setattr(store, "loop", lambda: None)
    store.start()
    store.stop()
    assert store.get(interrupted["id"])["status"] == "failed"
    assert "重启" in store.get(interrupted["id"])["error"]
    assert store.get(queued["id"])["status"] == "queued"


def test_azw3_capability_and_existing_epub_retry(client, monkeypatch):
    monkeypatch.setattr("app.main.find_converter", lambda: None)
    assert not client.get('/api/health').json()['azw3']['ready']
    assert upload(client, output_format='both').status_code == 503
    job = upload(client).json()
    store = client.app.state.jobs
    base = '/api/jobs/' + job['id']
    assert client.post(base + '/azw3').status_code == 409
    store.update(job['id'], status='completed')
    assert client.post(base + '/azw3').status_code == 409
    epub = store.root / job['id'] / 'output.epub'
    epub.write_bytes(b'existing EPUB')
    assert client.post(base + '/azw3').status_code == 503
    monkeypatch.setattr('app.main.find_converter', lambda: 'ebook-convert')
    result = client.post(base + '/azw3').json()
    assert result['status'] == 'queued' and result['kind'] == 'azw3'
    assert result['output_format'] == 'both'
    assert client.post(base + '/azw3').status_code == 409
    store.update(job['id'], status='converting_azw3')
    assert client.post(base + '/cancel').json()['status'] == 'cancelled'
    assert client.get(base + '/download').content == b'existing EPUB'
    assert client.post(base + '/azw3').status_code == 202
    store.update(job['id'], status='failed')
    assert client.get(base).json()['downloads']['epub']
    assert client.post(base + '/azw3').json()['status'] == 'queued'
    azw3 = epub.with_suffix('.azw3')
    azw3.write_bytes(b'completed AZW3')
    store.update(job['id'], status='completed')
    assert client.post(base + '/azw3').json()['status'] == 'completed'
    response = client.get(base + '/download?format=azw3')
    assert response.content == b'completed AZW3'
    assert response.headers['content-type'] == 'application/vnd.amazon.mobi8-ebook'
    assert client.get(base + '/download?format=../../input.pdf').status_code == 422


def test_both_output_and_conversion_queue_limit(client, monkeypatch):
    monkeypatch.setattr('app.main.find_converter', lambda: 'ebook-convert')
    job = upload(client, output_format='both').json()
    assert job['output_format'] == 'both'
    store = client.app.state.jobs
    store.update(job['id'], status='converting_azw3')
    for _ in range(9):
        assert upload(client).status_code == 202
    assert upload(client).status_code == 429
