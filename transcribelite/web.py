from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = APP_DIR / "cache" / "uploads"
OUTPUT_DIR = APP_DIR / "output"
WEB_DIR = APP_DIR / "web"
STATIC_DIR = WEB_DIR / "static"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".webm",
}
ALLOWED_PROFILES = {"auto", "fast", "balanced", "quality"}
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GB
MAX_JOBS = 100

STAGE_RE = re.compile(r"^\s*(ingest|stt|summarize|export)\s*:\s*([0-9.]+)s", re.I)
DONE_RE = re.compile(r"done:\s*([0-9.]+)s\s*->\s*(.+)$", re.I)


@dataclass
class Job:
    id: str
    filename: str
    profile: str
    status: str
    stage: str
    progress: float
    message: str
    output_dir: Optional[str]
    error: Optional[str]


JOBS: Dict[str, Job] = {}
JOB_ORDER: list[str] = []

app = FastAPI(title="TranscribeLite Web", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _resolve_python_executable() -> str:
    venv_python = APP_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _trim_jobs() -> None:
    if len(JOB_ORDER) <= MAX_JOBS:
        return
    for old_id in JOB_ORDER[MAX_JOBS:]:
        JOBS.pop(old_id, None)
    del JOB_ORDER[MAX_JOBS:]


def _find_output_for_input(input_path: Path, started_at: float) -> Optional[str]:
    resolved_input = str(input_path.resolve())
    candidates = sorted(OUTPUT_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for out_dir in candidates:
        try:
            if out_dir.stat().st_mtime < started_at:
                continue
            meta_file = out_dir / "transcript.json"
            if not meta_file.exists():
                continue
            payload = json.loads(meta_file.read_text(encoding="utf-8"))
            source = str(payload.get("meta", {}).get("source_file", "")).strip()
            if source and str(Path(source).resolve()) == resolved_input:
                return str(out_dir)
        except Exception:
            continue
    return None


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = WEB_DIR / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h3>Missing web/index.html</h3>", status_code=500)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/jobs")
def list_jobs() -> JSONResponse:
    return JSONResponse([asdict(JOBS[jid]) for jid in JOB_ORDER[:20]])


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> JSONResponse:
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(asdict(job))


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    profile: str = Form("balanced"),
) -> JSONResponse:
    profile = profile.strip().lower()
    if profile not in ALLOWED_PROFILES:
        raise HTTPException(status_code=400, detail=f"Invalid profile: {profile}")

    safe_name = Path(file.filename or "").name
    if not safe_name:
        raise HTTPException(status_code=400, detail="File name is empty")
    if Path(safe_name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported media extension")

    job_id = uuid.uuid4().hex[:12]
    upload_path = UPLOADS_DIR / f"{job_id}_{safe_name}"
    bytes_written = 0

    try:
        with upload_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Uploaded file is too large")
                f.write(chunk)
    except HTTPException:
        if upload_path.exists():
            upload_path.unlink(missing_ok=True)
        raise

    if bytes_written == 0:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    job = Job(
        id=job_id,
        filename=safe_name,
        profile=profile,
        status="queued",
        stage="queued",
        progress=0.0,
        message="Queued",
        output_dir=None,
        error=None,
    )
    JOBS[job_id] = job
    JOB_ORDER.insert(0, job_id)
    _trim_jobs()

    asyncio.create_task(run_transcribe_job(job_id, upload_path))
    return JSONResponse(asdict(job))


@app.get("/api/jobs/{job_id}/download/{which}")
def download_file(job_id: str, which: str):
    job = JOBS.get(job_id)
    if not job or not job.output_dir:
        return JSONResponse({"error": "not ready"}, status_code=404)

    out_dir = Path(job.output_dir)
    mapping = {
        "note": out_dir / "note.md",
        "txt": out_dir / "transcript.txt",
        "json": out_dir / "transcript.json",
    }
    path = mapping.get(which)
    if not path or not path.exists():
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(str(path), filename=path.name)


@app.get("/api/jobs/{job_id}/preview")
def preview(job_id: str) -> JSONResponse:
    job = JOBS.get(job_id)
    if not job or not job.output_dir:
        return JSONResponse({"error": "not ready"}, status_code=404)

    out_dir = Path(job.output_dir)
    note_path = out_dir / "note.md"
    txt_path = out_dir / "transcript.txt"
    note = note_path.read_text(encoding="utf-8") if note_path.exists() else ""
    transcript = txt_path.read_text(encoding="utf-8") if txt_path.exists() else ""
    return JSONResponse({"note_md": note, "transcript": transcript})


async def run_transcribe_job(job_id: str, input_path: Path) -> None:
    job = JOBS[job_id]
    started_at = datetime.now().timestamp()
    job.status = "running"
    job.stage = "ingest"
    job.progress = 0.05
    job.message = "Starting..."

    cmd = [
        _resolve_python_executable(),
        "-m",
        "transcribelite.app",
        "--config",
        "config.ini",
        "transcribe",
        str(input_path),
        "--profile",
        job.profile,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(APP_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        stage_weights = {"ingest": 0.10, "stt": 0.65, "summarize": 0.20, "export": 0.05}
        stage_done: set[str] = set()

        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="ignore").rstrip()
            if line:
                job.message = line

            stage_match = STAGE_RE.match(line)
            if stage_match:
                stage = stage_match.group(1).lower()
                stage_done.add(stage)
                progress = sum(weight for stage_name, weight in stage_weights.items() if stage_name in stage_done)
                job.stage = stage
                job.progress = min(0.95, progress + 0.02)

            done_match = DONE_RE.search(line)
            if done_match:
                out_path = done_match.group(2).strip()
                job.output_dir = out_path

        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(f"CLI failed with code {rc}")

        if not job.output_dir:
            job.output_dir = _find_output_for_input(input_path, started_at)

        job.status = "done"
        job.stage = "done"
        job.progress = 1.0
        job.message = "Done"
    except Exception as exc:
        job.status = "error"
        job.stage = "error"
        job.progress = 1.0
        job.error = str(exc)
        job.message = "Error"
    finally:
        try:
            input_path.unlink(missing_ok=True)
        except Exception:
            pass
