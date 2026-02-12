from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from urllib.parse import urlparse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from transcribelite.config import load_config
from transcribelite.pipeline.summarize_ollama import check_ollama_health, generate_text
from transcribelite.search_index import index_job as index_transcript_job
from transcribelite.search_index import search_chunks

APP_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = APP_DIR / "cache" / "uploads"
OUTPUT_DIR = APP_DIR / "output"
DATA_DIR = APP_DIR / "data"
INDEX_DB_PATH = DATA_DIR / "index.db"
WEB_DIR = APP_DIR / "web"
STATIC_DIR = WEB_DIR / "static"
QA_PROMPT_PATH = APP_DIR / "prompts" / "qa_ru.txt"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
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
MAX_REMOTE_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1 GB
MAX_REMOTE_DURATION_SECONDS = 3 * 60 * 60  # 3 hours
MAX_JOBS = 100

STAGE_RE = re.compile(r"^\s*(ingest|stt|summarize|export)\s*:\s*([0-9.]+)s", re.I)
DONE_RE = re.compile(r"done:\s*([0-9.]+)s\s*->\s*(.+)$", re.I)
SECTION_RE = re.compile(r"(?is)^##\s*([^\n]+)\s*$")


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


def _validate_profile(profile: str) -> str:
    normalized = profile.strip().lower()
    if normalized not in ALLOWED_PROFILES:
        raise HTTPException(status_code=400, detail=f"Invalid profile: {profile}")
    return normalized


def _is_valid_url(candidate: str) -> bool:
    try:
        parsed = urlparse(candidate)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _create_job(profile: str, filename: str, job_id: Optional[str] = None) -> Job:
    if not job_id:
        job_id = uuid.uuid4().hex[:12]
    job = Job(
        id=job_id,
        filename=filename,
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
    return job


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


def _extract_markdown_section(text: str, section_name: str) -> str:
    if not text:
        return ""
    section_name = section_name.strip().lower()
    lines = text.splitlines()
    start_idx = -1
    for i, line in enumerate(lines):
        match = SECTION_RE.match(line.strip())
        if match and match.group(1).strip().lower() == section_name:
            start_idx = i + 1
            break
    if start_idx < 0:
        return ""
    end_idx = len(lines)
    for j in range(start_idx, len(lines)):
        if SECTION_RE.match(lines[j].strip()):
            end_idx = j
            break
    return "\n".join(lines[start_idx:end_idx]).strip()


def _extract_bullets(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            item = stripped[2:].strip()
            if item:
                items.append(item)
    return items


def _build_sources_text(chunks: list, max_chars_per_source: int = 800) -> str:
    lines = []
    for idx, hit in enumerate(chunks, start=1):
        snippet = hit.text.strip().replace("\n", " ")
        if len(snippet) > max_chars_per_source:
            snippet = snippet[:max_chars_per_source].rstrip() + "..."
        lines.append(f"[{idx}] {snippet}")
    return "\n\n".join(lines)


def _short_source_text(text: str, max_chars: int = 360) -> str:
    cleaned = text.strip().replace("\n", " ")
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


def _index_completed_job(job_id: str, out_dir: Path) -> None:
    txt_path = out_dir / "transcript.txt"
    json_path = out_dir / "transcript.json"
    if not txt_path.exists():
        return
    transcript_text = txt_path.read_text(encoding="utf-8").strip()
    if not transcript_text:
        return

    title = out_dir.name
    created_at = datetime.now().isoformat(timespec="seconds")
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            meta = payload.get("meta", {})
            source_file = str(meta.get("source_file", "")).strip()
            if source_file:
                title = Path(source_file).name
            created = str(meta.get("created_at", "")).strip()
            if created:
                created_at = created
        except Exception:
            pass

    index_transcript_job(
        db_path=INDEX_DB_PATH,
        job_id=job_id,
        title=title,
        transcript_text=transcript_text,
        output_dir=str(out_dir),
        created_at=created_at,
    )


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
    profile = _validate_profile(profile)

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

    job = _create_job(profile=profile, filename=safe_name, job_id=job_id)

    asyncio.create_task(run_transcribe_job(job_id, upload_path))
    return JSONResponse(asdict(job))


@app.post("/api/jobs/from-url")
async def create_job_from_url(
    url: str = Form(...),
    profile: str = Form("balanced"),
) -> JSONResponse:
    profile = _validate_profile(profile)
    url = url.strip()
    if not _is_valid_url(url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    job = _create_job(profile=profile, filename=url)
    asyncio.create_task(run_download_and_transcribe_job(job.id, url))
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
    json_path = out_dir / "transcript.json"
    note = note_path.read_text(encoding="utf-8") if note_path.exists() else ""
    transcript = txt_path.read_text(encoding="utf-8") if txt_path.exists() else ""
    payload: dict = {}
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    summary_md = str(payload.get("summary") or "").strip() if isinstance(payload, dict) else ""
    summary_status = str(meta.get("summary_status", "unknown"))
    summary_error = str(meta.get("summary_error") or "").strip()

    summary_section = _extract_markdown_section(summary_md, "summary")
    if not summary_section and summary_md:
        summary_section = summary_md.strip()
    summary_points = _extract_bullets(summary_section)
    summary_text = summary_section if not summary_points else ""

    action_section = _extract_markdown_section(summary_md, "action items")
    action_items = _extract_bullets(action_section)

    transcript_excerpt = transcript[:6000].strip()

    preview_payload = {
        "note_md": note,
        "transcript": transcript,
        "summary_status": summary_status,
        "summary_error": summary_error,
        "summary_points": summary_points,
        "summary_text": summary_text,
        "action_items": action_items,
        "transcript_excerpt": transcript_excerpt,
        "meta": {
            "created_at": meta.get("created_at", ""),
            "source_file": meta.get("source_file", ""),
            "stt_model": meta.get("model_name", ""),
            "device": meta.get("device", ""),
            "compute_type": meta.get("compute_type", ""),
            "language": meta.get("language", ""),
        },
    }
    return JSONResponse(preview_payload)


@app.post("/api/ask")
async def ask_recording(
    job_id: str = Form(...),
    question: str = Form(...),
    limit: int = Form(6),
) -> JSONResponse:
    question = question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is empty")

    job = JOBS.get(job_id)
    if not job or not job.output_dir:
        raise HTTPException(status_code=404, detail="Job not found or not ready")

    sources = search_chunks(INDEX_DB_PATH, question=question, job_id=job_id, limit=limit)
    if not sources:
        return JSONResponse({"answer": "В записи этого нет.", "sources": []})

    source_blocks = _build_sources_text(sources)
    if not QA_PROMPT_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Missing prompt template: {QA_PROMPT_PATH}")
    template = QA_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = template.format(question=question, sources=source_blocks)

    cfg = load_config("config.ini", init_if_missing=True)
    healthy, reason = check_ollama_health(cfg)
    if not healthy:
        raise HTTPException(status_code=503, detail=f"Ollama недоступна: {reason}")

    try:
        answer = await asyncio.to_thread(
            generate_text,
            cfg,
            prompt,
            min(cfg.summarize.timeout_s, 90),
            256,
            0.2,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Ollama недоступна: {exc}") from exc

    response_sources = [
        {"number": i + 1, "chunk_id": hit.chunk_id, "text": _short_source_text(hit.text)}
        for i, hit in enumerate(sources)
    ]
    return JSONResponse({"answer": answer, "sources": response_sources})


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

        stage_weights = {"download": 0.10, "ingest": 0.08, "stt": 0.62, "summarize": 0.15, "export": 0.05}
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
        if job.output_dir:
            out_dir = Path(job.output_dir)
            try:
                await asyncio.to_thread(_index_completed_job, job_id, out_dir)
            except Exception:
                pass

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


def _download_media_via_ytdlp(url: str, target_prefix: Path) -> Path:
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:
        raise RuntimeError("yt-dlp is not installed") from exc

    info_opts = {"quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            raise RuntimeError("Unsupported media URL")
        duration = info.get("duration")
        title = str(info.get("title") or url)
        if isinstance(duration, (int, float)) and duration > MAX_REMOTE_DURATION_SECONDS:
            raise RuntimeError("Remote media is too long (over 3 hours)")

    outtmpl = str(target_prefix) + ".%(ext)s"
    dl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": MAX_REMOTE_DOWNLOAD_BYTES,
    }
    with yt_dlp.YoutubeDL(dl_opts) as ydl:
        ydl.download([url])

    candidates = sorted(target_prefix.parent.glob(target_prefix.name + ".*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("Download finished but file was not found")
    downloaded = candidates[0]
    if downloaded.stat().st_size > MAX_REMOTE_DOWNLOAD_BYTES:
        downloaded.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file exceeds size limit")

    safe_title = re.sub(r"[^a-zA-Z0-9._-]+", "_", title).strip("_")
    if safe_title:
        desired = downloaded.with_name(f"{target_prefix.name}_{safe_title}{downloaded.suffix}")
        if desired != downloaded:
            downloaded.rename(desired)
            downloaded = desired
    return downloaded


async def run_download_and_transcribe_job(job_id: str, url: str) -> None:
    job = JOBS[job_id]
    job.status = "running"
    job.stage = "download"
    job.progress = 0.03
    job.message = "Downloading media..."

    target_prefix = UPLOADS_DIR / f"{job_id}_url"
    try:
        downloaded = await asyncio.to_thread(_download_media_via_ytdlp, url, target_prefix)
        job.filename = downloaded.name
        job.stage = "ingest"
        job.progress = 0.05
        job.message = "Download complete. Starting transcription..."
        await run_transcribe_job(job_id, downloaded)
    except Exception as exc:
        job.status = "error"
        job.stage = "error"
        job.progress = 1.0
        job.error = str(exc)
        job.message = "Error"
