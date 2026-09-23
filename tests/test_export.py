"""TDD: vault export as a zip."""
import io
import zipfile
from fastapi.testclient import TestClient
from pathlib import Path
from app.main import create_app


def test_export_zip_contains_notes(tmp_path):
    app = create_app(vault_root=tmp_path)
    c = TestClient(app)
    c.post("/api/notes", json={
        "id": "e1", "collection": "inbox", "title": "hello",
        "body": "world", "signifier": "note", "status": "open",
    })
    c.post("/api/notes", json={
        "id": "h1", "collection": "home", "title": "fix sink",
        "body": "drip", "signifier": "task", "status": "open",
    })
    r = c.get("/api/export.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "inbox/e1.md" in names
    assert "home/h1.md" in names
    body = z.read("inbox/e1.md").decode()
    assert "hello" in body
    # Index file should NOT be in the export
    assert not any(".index.sqlite" in n for n in names)


def test_export_zip_empty_vault(tmp_path):
    app = create_app(vault_root=tmp_path)
    c = TestClient(app)
    r = c.get("/api/export.zip")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    # No .md files, just an empty zip
    md_files = [n for n in z.namelist() if n.endswith(".md")]
    assert md_files == []