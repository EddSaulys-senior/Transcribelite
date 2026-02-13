from __future__ import annotations

import asyncio
import difflib
import io
import json
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from urllib.parse import quote, urlparse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from transcribelite.config import load_config
from transcribelite.pipeline.export import export_outputs
from transcribelite.pipeline.summarize_ollama import check_ollama_health, ensure_model_available, generate_text
from transcribelite.pipeline.summarize_ollama import list_ollama_models
from transcribelite.pipeline.summarize_ollama import summarize_text
from transcribelite.search_index import add_dictation_history
from transcribelite.search_index import add_qa_history
from transcribelite.search_index import add_transcription_history
from transcribelite.search_index import delete_index_for_job
from transcribelite.search_index import index_job as index_transcript_job
from transcribelite.search_index import delete_dictation_history_item
from transcribelite.search_index import delete_transcription_history_item
from transcribelite.search_index import get_transcription_history_item
from transcribelite.search_index import list_dictation_history
from transcribelite.search_index import list_qa_history
from transcribelite.search_index import list_transcription_history
from transcribelite.search_index import search_chunks
from transcribelite.search_index import search_global_chunks

APP_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = APP_DIR / "cache" / "uploads"
OUTPUT_DIR = APP_DIR / "output"
DATA_DIR = APP_DIR / "data"
INDEX_DB_PATH = DATA_DIR / "index.db"
DICTATION_DIR = APP_DIR / "cache" / "dictation"
WEB_DIR = APP_DIR / "web"
STATIC_DIR = WEB_DIR / "static"
QA_PROMPT_PATH = APP_DIR / "prompts" / "qa_ru.txt"
POLISH_PROMPTS_DIR = APP_DIR / "prompts" / "polish"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DICTATION_DIR.mkdir(parents=True, exist_ok=True)
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
ALLOWED_POLISH_PRESETS = {"punct", "clean", "short", "task", "obsidian", "custom"}
POLISH_PRESET_FILES = {
    "punct": "punct_ru.txt",
    "clean": "clean_ru.txt",
    "short": "short_ru.txt",
    "task": "task_ru.txt",
    "obsidian": "obsidian_ru.txt",
    "custom": "custom_ru.txt",
}
POLISH_NUM_PREDICT = {
    "punct": 350,
    "clean": 350,
    "task": 350,
    "short": 220,
    "obsidian": 600,
    "custom": 350,
}
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


@dataclass
class DictationSession:
    session_id: str
    cfg: Any
    profile: str
    language: str
    summarize_enabled: bool
    source_mime: str
    webm_path: Path
    tail_wav_path: Path
    full_wav_path: Path
    running: bool
    final_text: str
    last_chunk_text: str
    model: Any
    model_device: str
    model_compute_type: str
    manual_text_override: bool
    saved_once: bool
    worker: Optional[asyncio.Task]


DICTATION_SESSIONS: Dict[str, DictationSession] = {}


@dataclass
class PullState:
    id: str
    model: str
    status: str
    message: str
    done: bool
    error: Optional[str]


OLLAMA_PULLS: Dict[str, PullState] = {}

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


def _safe_slug(value: str, max_len: int = 64) -> str:
    slug = re.sub(r"[^\w\-]+", "_", str(value or ""), flags=re.UNICODE).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("_")
    return slug or "transcription"


def _ascii_filename(value: str, max_len: int = 120) -> str:
    cleaned = str(value or "").strip().replace("\r", " ").replace("\n", " ")
    cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned).strip("._")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip("._")
    return cleaned or "download.zip"


def _build_content_disposition(filename: str) -> str:
    cleaned = str(filename or "").strip().replace("\r", " ").replace("\n", " ")
    fallback = _ascii_filename(cleaned)
    encoded = quote(cleaned, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def _build_transcription_zip(output_dir: Path) -> bytes:
    note = output_dir / "note.md"
    txt = output_dir / "transcript.txt"
    if not note.exists() or not txt.exists():
        raise FileNotFoundError("Required files are missing in output directory")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(note, arcname="note.md")
        zf.write(txt, arcname="transcript.txt")
    buf.seek(0)
    return buf.getvalue()


def _read_transcript_meta(out_dir: Path) -> dict:
    path = out_dir / "transcript.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        meta = payload.get("meta", {})
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_prompt_file(path: Path) -> str:
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"Missing prompt file: {path}")
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    raise HTTPException(status_code=500, detail=f"Unable to read prompt file: {path}")


def _build_polish_prompt(
    preset: str,
    text: str,
    instruction: str,
    strict: bool,
) -> str:
    parts: list[str] = []
    if strict:
        parts.append(_read_prompt_file(POLISH_PROMPTS_DIR / "base_strict_ru.txt").strip())
    preset_file = POLISH_PRESET_FILES[preset]
    preset_template = _read_prompt_file(POLISH_PROMPTS_DIR / preset_file)
    if preset == "custom":
        preset_block = preset_template.format(instruction=instruction or "Улучши читаемость текста.")
    else:
        preset_block = preset_template
        if instruction:
            preset_block = (
                preset_block.rstrip()
                + "\n\nДоп. инструкция пользователя:\n"
                + instruction.strip()
            )
    parts.append(preset_block.strip())
    parts.append("Исходный текст:\n" + text.strip())
    return "\n\n".join([p for p in parts if p])


def _polish_is_markdown(preset: str, polished_text: str) -> bool:
    if preset == "obsidian":
        return True
    stripped = polished_text.lstrip()
    return stripped.startswith("#") or stripped.startswith("##")


def _save_polish_result(
    out_dir: Path,
    polished_text: str,
    preset: str,
    instruction: str,
    strict: bool,
    model: str,
    source_job_id: str,
) -> tuple[str, str]:
    is_markdown = _polish_is_markdown(preset, polished_text)
    target_name = "note_polished.md" if is_markdown else "transcript_polished.txt"
    target_path = out_dir / target_name
    target_path.write_text(polished_text, encoding="utf-8")

    created_at = datetime.now().isoformat(timespec="seconds")
    meta_path = out_dir / f"polish_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    meta_payload = {
        "preset": preset,
        "instruction": instruction,
        "strict": strict,
        "model": model,
        "created_at": created_at,
        "source_job_id": source_job_id,
        "format": "markdown" if is_markdown else "text",
        "saved_path": str(target_path),
    }
    meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target_path), ("markdown" if is_markdown else "text")


async def _read_polish_payload(request: Request) -> dict:
    ctype = request.headers.get("content-type", "").lower()
    if "application/json" in ctype:
        payload = await request.json()
        return payload if isinstance(payload, dict) else {}
    form = await request.form()
    return {k: form.get(k) for k in form.keys()}


def _set_pull_message(pull_id: str, message: str) -> None:
    state = OLLAMA_PULLS.get(pull_id)
    if not state:
        return
    state.message = message
    state.status = "running"


def _set_pull_done(pull_id: str, message: str) -> None:
    state = OLLAMA_PULLS.get(pull_id)
    if not state:
        return
    state.done = True
    state.status = "done"
    state.message = message
    state.error = None


def _set_pull_error(pull_id: str, error: str) -> None:
    state = OLLAMA_PULLS.get(pull_id)
    if not state:
        return
    state.done = True
    state.status = "error"
    state.error = error
    state.message = "failed"


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
            title_meta = str(meta.get("title", "")).strip()
            if title_meta:
                title = title_meta
            source_file = str(meta.get("source_file", "")).strip()
            if source_file and not title_meta:
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


def _resolve_profile_for_cfg(cfg: Any, profile: str) -> str:
    p = profile.strip().lower()
    if p == "auto":
        p = "balanced"
    preset = cfg.profile_presets.get(p)
    if preset is not None:
        cfg.stt.model_name = preset.model_name
        cfg.stt.compute_type = preset.compute_type
        cfg.stt.beam_size = preset.beam_size
        cfg.profile_name = p
    return p


def _build_cfg_for_dictation(profile: str, language: str, summarize_enabled: bool) -> Any:
    cfg = load_config("config.ini", init_if_missing=True)
    _resolve_profile_for_cfg(cfg, profile)
    cfg.stt.task = "transcribe"
    cfg.stt.vad_filter = True
    cfg.stt.language = "auto" if language.strip().lower() == "auto" else language.strip().lower()
    cfg.summarize.enabled = bool(summarize_enabled)
    return cfg


def _resolve_device_and_compute(preferred_device: str, preferred_compute: str) -> tuple[str, str]:
    device = preferred_device.lower()
    compute_type = preferred_compute
    if device != "cuda":
        return "cpu", "int8" if compute_type == "float16" else compute_type
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda", compute_type
    except Exception:
        pass
    return "cpu", "int8"


def _init_dictation_model(cfg: Any) -> tuple[Any, str, str]:
    from faster_whisper import WhisperModel

    device, compute_type = _resolve_device_and_compute(cfg.stt.device, cfg.stt.compute_type)
    attempts = [(device, compute_type)]
    if device == "cuda" and compute_type != "float32":
        attempts.append(("cuda", "float32"))
    attempts.append(("cpu", "int8"))

    last_exc: Optional[Exception] = None
    for d, c in attempts:
        try:
            model = WhisperModel(
                cfg.stt.model_name,
                device=d,
                compute_type=c,
                download_root=str(cfg.paths.models_dir),
            )
            return model, d, c
        except Exception as exc:
            last_exc = exc
    raise RuntimeError("Unable to initialize dictation model") from last_exc


def _ffmpeg_decode_tail(ffmpeg_path: str, input_path: Path, output_wav: Path, tail_seconds: int = 10) -> None:
    cmd_tail = [
        ffmpeg_path,
        "-y",
        "-sseof",
        f"-{tail_seconds}",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_wav),
    ]
    try:
        subprocess.run(cmd_tail, capture_output=True, text=True, check=True)
        return
    except Exception:
        pass

    # Fallback for growing/incomplete webm where -sseof can fail intermittently.
    cmd_full = [
        ffmpeg_path,
        "-y",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_wav),
    ]
    subprocess.run(cmd_full, capture_output=True, text=True, check=True)


def _ffmpeg_decode_full(ffmpeg_path: str, input_path: Path, output_wav: Path) -> None:
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_wav),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def _transcribe_with_session_model(session: DictationSession, wav_path: Path) -> tuple[list[dict[str, object]], str, Any]:
    language = None if session.cfg.stt.language.lower() == "auto" else session.cfg.stt.language
    segments_iter, info = session.model.transcribe(
        str(wav_path),
        language=language,
        task="transcribe",
        beam_size=session.cfg.stt.beam_size,
        vad_filter=session.cfg.stt.vad_filter,
    )
    segments: list[dict[str, object]] = []
    text_parts: list[str] = []
    for seg in segments_iter:
        part = seg.text.strip()
        if part:
            text_parts.append(part)
        segments.append({"start": float(seg.start), "end": float(seg.end), "text": part})
    return segments, " ".join(text_parts).strip(), info


def _merge_by_overlap_words(base_text: str, new_text: str, max_overlap_words: int = 80) -> str:
    base_words = base_text.split()
    new_words = new_text.split()
    if not new_words:
        return base_text
    if not base_words:
        return new_text

    # Strong anti-dup check: if new chunk is almost equal to the tail, skip it.
    new_norm = _normalize_for_compare(new_text)
    tail_text = " ".join(base_words[-max(25, len(new_words) + 12) :])
    tail_norm = _normalize_for_compare(tail_text)
    if new_norm and tail_norm:
        ratio = difflib.SequenceMatcher(None, tail_norm, new_norm).ratio()
        if ratio >= 0.88:
            return base_text

    # If the chunk already exists near the end (not only at suffix), skip.
    if new_norm:
        base_tail_norm = _normalize_for_compare(" ".join(base_words[-max(120, len(new_words) * 2) :]))
        if new_norm and new_norm in base_tail_norm:
            return base_text

    # Exact overlap on normalized words to append only delta.
    base_norm_words = _normalize_for_compare(base_text).split()
    new_norm_words = _normalize_for_compare(new_text).split()
    if not base_norm_words or not new_norm_words:
        return (base_text + " " + new_text).strip()

    max_k = min(max_overlap_words, len(base_words), len(new_words))
    overlap = 0
    for k in range(max_k, 0, -1):
        if base_norm_words[-k:] == new_norm_words[:k]:
            overlap = k
            break

    delta_words = new_words[overlap:]
    if not delta_words:
        return base_text
    return (base_text + " " + " ".join(delta_words)).strip()


def _normalize_for_compare(text: str) -> str:
    normalized = re.sub(r"[^\w\s]+", "", text.lower(), flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _build_dictation_preview(text: str, max_chars: int = 420) -> str:
    compact = re.sub(r"\s+", " ", text.strip())
    if not compact:
        return ""
    # Insert soft line breaks for readability in history cards.
    words = compact.split()
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for w in words:
        add = len(w) + (1 if current else 0)
        if current and current_len + add > 85:
            lines.append(" ".join(current))
            current = [w]
            current_len = len(w)
        else:
            current.append(w)
            current_len += add
        if sum(len(line) for line in lines) + len(" ".join(current)) >= max_chars:
            break
    if current:
        lines.append(" ".join(current))
    preview = "\n".join(lines).strip()
    if len(compact) > len(preview):
        preview = preview.rstrip() + "..."
    return preview


async def _dictation_worker(websocket: WebSocket, session_id: str) -> None:
    while True:
        await asyncio.sleep(1.1)
        session = DICTATION_SESSIONS.get(session_id)
        if not session or not session.running:
            return
        await _dictation_step(websocket, session)


async def _dictation_step(websocket: WebSocket, session: DictationSession) -> None:
    if not session.webm_path.exists() or session.webm_path.stat().st_size < 2048:
        return
    try:
        t0 = datetime.now().timestamp()
        await asyncio.to_thread(
            _ffmpeg_decode_tail,
            session.cfg.paths.ffmpeg_path,
            session.webm_path,
            session.tail_wav_path,
            10,
        )
        _, chunk_text, _ = await asyncio.to_thread(_transcribe_with_session_model, session, session.tail_wav_path)
        if chunk_text:
            if _normalize_for_compare(chunk_text) == _normalize_for_compare(session.last_chunk_text):
                return
            session.last_chunk_text = chunk_text
            session.final_text = _merge_by_overlap_words(session.final_text, chunk_text)
            await websocket.send_json({"type": "partial", "text": chunk_text})
            await websocket.send_json({"type": "final", "text": session.final_text})
        dt = max(0.001, datetime.now().timestamp() - t0)
        await websocket.send_json({"type": "stats", "rtf": round(dt / 10.0, 3), "seconds": 10})
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": f"dictation step failed: {exc}"})


async def _finalize_dictation_save(session: DictationSession, session_id: str) -> tuple[str, str]:
    await asyncio.to_thread(
        _ffmpeg_decode_full,
        session.cfg.paths.ffmpeg_path,
        session.webm_path,
        session.full_wav_path,
    )
    segments, text_full, info = await asyncio.to_thread(
        _transcribe_with_session_model,
        session,
        session.full_wav_path,
    )
    if session.manual_text_override and session.final_text.strip():
        final_text = session.final_text.strip()
    else:
        final_text = text_full.strip() if text_full.strip() else session.final_text.strip()
    session.final_text = final_text

    stt_meta = {
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "stt_engine": session.cfg.stt.engine,
        "model_name": session.cfg.stt.model_name,
        "device": session.model_device,
        "compute_type": session.model_compute_type,
    }

    summary = None
    summary_error = None
    if session.summarize_enabled:
        summary, summary_error = summarize_text(final_text, session.cfg)
    else:
        summary_error = "summary disabled in dictation"

    source_name = f"dictation_{session_id}"
    source_path = session.full_wav_path.with_name(source_name + ".wav")
    out_dir = export_outputs(
        cfg=session.cfg,
        source_path=source_path,
        transcript_text=final_text,
        segments=segments,
        stt_meta=stt_meta,
        summary=summary,
        summary_error=summary_error,
    )

    job_id = f"dict_{session_id[:12]}"
    job = _create_job(profile=session.profile, filename=source_name + ".wav", job_id=job_id)
    job.status = "done"
    job.stage = "done"
    job.progress = 1.0
    job.message = "Done"
    job.output_dir = str(out_dir)
    try:
        _index_completed_job(job_id, out_dir)
    except Exception:
        pass
    try:
        preview = _build_dictation_preview(final_text)
        add_dictation_history(
            db_path=INDEX_DB_PATH,
            job_id=job_id,
            output_dir=str(out_dir),
            text_preview=preview,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception:
        pass
    return job_id, str(out_dir)


async def _save_dictation_session(
    websocket: WebSocket,
    session: DictationSession,
    session_id: str,
) -> None:
    if session.saved_once:
        await websocket.send_json({"type": "status", "message": "already saved"})
        return
    await _dictation_step(websocket, session)
    if not session.final_text.strip():
        await websocket.send_json({"type": "error", "message": "nothing to save"})
        return
    try:
        job_id, output_dir = await _finalize_dictation_save(session, session_id)
        session.saved_once = True
        await websocket.send_json({"type": "saved", "job_id": job_id, "output_dir": output_dir})
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": f"save failed: {exc}"})


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
    if which == "polished":
        md = out_dir / "note_polished.md"
        txt = out_dir / "transcript_polished.txt"
        path = md if md.exists() else txt
    if which == "polish_meta":
        metas = sorted(out_dir.glob("polish_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        path = metas[0] if metas else None
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
            "title": meta.get("title", ""),
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
    created_at = datetime.now().isoformat(timespec="seconds")
    try:
        add_qa_history(
            db_path=INDEX_DB_PATH,
            job_id=job_id,
            question=question,
            answer=answer,
            created_at=created_at,
        )
    except Exception:
        pass

    return JSONResponse({"job_id": job_id, "answer": answer, "sources": response_sources})


@app.get("/api/search")
def search_history(
    q: str = Query(..., min_length=2),
    limit: int = Query(12, ge=1, le=30),
) -> JSONResponse:
    hits = search_global_chunks(INDEX_DB_PATH, question=q, limit=limit)
    items = [
        {
            "job_id": hit.job_id,
            "title": hit.title,
            "created_at": hit.created_at,
            "chunk_id": hit.chunk_id,
            "chunk_index": hit.chunk_index,
            "snippet": _short_source_text(hit.text, max_chars=280),
            "output_dir": hit.output_dir,
        }
        for hit in hits
    ]
    return JSONResponse({"query": q, "items": items})


@app.get("/api/qa/history")
def get_qa_history(limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
    history = list_qa_history(INDEX_DB_PATH, limit=limit)
    items = [
        {
            "id": item.id,
            "job_id": item.job_id,
            "question": item.question,
            "answer": item.answer,
            "created_at": item.created_at,
        }
        for item in history
    ]
    return JSONResponse({"items": items})


@app.get("/api/dictation/history")
def get_dictation_history(limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
    history = list_dictation_history(INDEX_DB_PATH, limit=limit)
    items = [
        {
            "id": item.id,
            "job_id": item.job_id,
            "output_dir": item.output_dir,
            "text_preview": item.text_preview,
            "created_at": item.created_at,
        }
        for item in history
    ]
    return JSONResponse({"items": items})


@app.get("/api/transcription/history")
def get_transcription_history(limit: int = Query(50, ge=1, le=300)) -> JSONResponse:
    history = list_transcription_history(INDEX_DB_PATH, limit=limit)
    items = [
        {
            "id": item.id,
            "job_id": item.job_id,
            "source_name": item.source_name,
            "title": item.title,
            "output_dir": item.output_dir,
            "created_at": item.created_at,
        }
        for item in history
    ]
    return JSONResponse({"items": items})


@app.get("/api/transcription/history/{job_id}/zip")
def download_transcription_zip(job_id: str):
    history = list_transcription_history(INDEX_DB_PATH, limit=300)
    hit = next((x for x in history if x.job_id == job_id), None)
    if hit is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    out_dir = Path(hit.output_dir)
    try:
        payload = _build_transcription_zip(out_dir)
    except FileNotFoundError:
        return JSONResponse({"error": "note.md or transcript.txt not found"}, status_code=404)

    meta = _read_transcript_meta(out_dir)
    title = str(meta.get("title") or hit.title or hit.source_name or "transcription").strip()
    ts_raw = str(meta.get("created_at") or hit.created_at or "").strip()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if ts_raw:
        ts = re.sub(r"[^0-9]", "", ts_raw)[:14] or ts
    filename = f"{_safe_slug(title)}_{ts}.zip"
    headers = {"Content-Disposition": _build_content_disposition(filename)}
    return StreamingResponse(io.BytesIO(payload), media_type="application/zip", headers=headers)


@app.delete("/api/transcription/history/{item_id}")
def delete_transcription_history(item_id: int) -> JSONResponse:
    item = get_transcription_history_item(INDEX_DB_PATH, int(item_id))
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    out_dir = Path(item.output_dir)
    try:
        out_resolved = out_dir.resolve()
        base_resolved = OUTPUT_DIR.resolve()
        if out_resolved != base_resolved and base_resolved in out_resolved.parents:
            shutil.rmtree(out_resolved, ignore_errors=False)
        elif out_resolved.exists():
            return JSONResponse({"error": "refuse to delete outside output dir"}, status_code=400)
    except FileNotFoundError:
        pass
    except Exception as exc:
        return JSONResponse({"error": f"failed to delete files: {exc}"}, status_code=500)

    try:
        delete_index_for_job(INDEX_DB_PATH, item.job_id)
    except Exception:
        pass
    deleted = delete_transcription_history_item(INDEX_DB_PATH, int(item_id))
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"deleted": True, "id": int(item_id), "job_id": item.job_id})


@app.delete("/api/dictation/history/{item_id}")
def delete_dictation_history(item_id: int) -> JSONResponse:
    deleted = delete_dictation_history_item(INDEX_DB_PATH, item_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"deleted": True, "id": item_id})


@app.get("/api/ollama/models")
def get_ollama_models() -> JSONResponse:
    cfg = load_config("config.ini", init_if_missing=True)
    healthy, reason = check_ollama_health(cfg)
    if not healthy:
        return JSONResponse(
            {"ok": False, "error": f"Ollama недоступна: {reason}", "models": [], "default_model": cfg.summarize.model},
            status_code=503,
        )
    try:
        models = list_ollama_models(cfg)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"Не удалось получить список моделей: {exc}", "models": [], "default_model": cfg.summarize.model},
            status_code=503,
        )
    return JSONResponse({"ok": True, "models": models, "default_model": cfg.summarize.model})


@app.post("/api/ollama/pull/start")
async def start_ollama_pull(request: Request) -> JSONResponse:
    payload = await _read_polish_payload(request)
    model = str(payload.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Model is required")

    pull_id = uuid.uuid4().hex[:12]
    state = PullState(
        id=pull_id,
        model=model,
        status="running",
        message="starting",
        done=False,
        error=None,
    )
    OLLAMA_PULLS[pull_id] = state

    async def _runner() -> None:
        cfg = load_config("config.ini", init_if_missing=True)
        cfg.summarize.model = model
        try:
            await asyncio.to_thread(
                ensure_model_available,
                cfg,
                model,
                900,
                lambda msg: _set_pull_message(pull_id, str(msg)),
            )
            _set_pull_done(pull_id, "done")
        except Exception as exc:
            _set_pull_error(pull_id, str(exc))

    asyncio.create_task(_runner())
    return JSONResponse({"ok": True, "pull_id": pull_id, "model": model})


@app.get("/api/ollama/pull/{pull_id}")
def get_ollama_pull_status(pull_id: str) -> JSONResponse:
    state = OLLAMA_PULLS.get(pull_id)
    if not state:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(asdict(state))


@app.post("/api/polish")
async def polish_text(request: Request) -> JSONResponse:
    payload = await _read_polish_payload(request)
    job_id = str(payload.get("job_id") or "").strip()
    source_text = str(payload.get("text") or "").strip()
    preset = str(payload.get("preset") or "punct").strip().lower()
    instruction = str(payload.get("instruction") or "").strip()
    strict = _to_bool(payload.get("strict"), True)
    save_as_file = _to_bool(payload.get("save_as_file"), False)
    model_override = str(payload.get("ollama_model") or "").strip()

    if preset not in ALLOWED_POLISH_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unsupported preset: {preset}")
    if not source_text:
        raise HTTPException(status_code=400, detail="Text is empty")
    if preset == "custom" and not instruction:
        raise HTTPException(status_code=400, detail="Instruction is required for custom preset")

    cfg = load_config("config.ini", init_if_missing=True)
    if model_override:
        cfg.summarize.model = model_override
    healthy, reason = check_ollama_health(cfg)
    if not healthy:
        return JSONResponse(
            {"ok": False, "error": f"Ollama недоступна: {reason}", "polished_text": "", "saved_path": None, "format": "text"},
            status_code=503,
        )

    prompt = _build_polish_prompt(preset=preset, text=source_text, instruction=instruction, strict=strict)
    num_predict = POLISH_NUM_PREDICT.get(preset, 350)

    try:
        polished_text = await asyncio.to_thread(
            generate_text,
            cfg,
            prompt,
            min(cfg.summarize.timeout_s, 180),
            num_predict,
            0.2,
            0.9,
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"Ollama недоступна: {exc}", "polished_text": "", "saved_path": None, "format": "text"},
            status_code=503,
        )

    saved_path: Optional[str] = None
    out_format = "markdown" if _polish_is_markdown(preset, polished_text) else "text"
    if save_as_file and job_id:
        job = JOBS.get(job_id)
        if not job or not job.output_dir:
            raise HTTPException(status_code=404, detail="Job not found or not ready")
        out_dir = Path(job.output_dir)
        saved_path, out_format = _save_polish_result(
            out_dir=out_dir,
            polished_text=polished_text,
            preset=preset,
            instruction=instruction,
            strict=strict,
            model=cfg.summarize.model,
            source_job_id=job_id,
        )

    return JSONResponse(
        {
            "ok": True,
            "polished_text": polished_text,
            "saved_path": saved_path,
            "format": out_format,
            "model": cfg.summarize.model,
            "download_url": f"/api/jobs/{job_id}/download/polished" if saved_path and job_id else None,
        }
    )


@app.websocket("/ws/dictation")
async def ws_dictation(websocket: WebSocket) -> None:
    await websocket.accept()
    session_id = uuid.uuid4().hex[:16]
    DICTATION_SESSIONS.pop(session_id, None)
    session: Optional[DictationSession] = None

    async def stop_worker() -> None:
        if session and session.worker:
            session.running = False
            session.worker.cancel()
            try:
                await session.worker
            except BaseException:
                pass
            session.worker = None

    try:
        await websocket.send_json({"type": "status", "message": "connected", "session_id": session_id})
        while True:
            message = await websocket.receive()
            msg_type = message.get("type")

            if msg_type == "websocket.disconnect":
                break

            if message.get("bytes") is not None:
                if session and session.running:
                    with session.webm_path.open("ab") as f:
                        f.write(message["bytes"])
                continue

            text = message.get("text")
            if not text:
                continue

            try:
                payload = json.loads(text)
            except Exception:
                await websocket.send_json({"type": "error", "message": "invalid json command"})
                continue

            command = str(payload.get("type", "")).strip().lower()

            if command == "start":
                if session and session.running:
                    await websocket.send_json({"type": "status", "message": "already running"})
                    continue

                base_cfg = load_config("config.ini", init_if_missing=True)
                default_profile = base_cfg.dictation.profile
                default_language = base_cfg.dictation.language
                default_summarize = base_cfg.dictation.summarize
                profile = str(payload.get("profile", default_profile)).strip().lower()
                language = str(payload.get("language", default_language)).strip().lower()
                summarize_enabled = bool(payload.get("summarize", default_summarize))
                source_mime = str(payload.get("mime_type", "")).strip()

                cfg = _build_cfg_for_dictation(profile=profile, language=language, summarize_enabled=summarize_enabled)
                model, model_device, model_compute_type = await asyncio.to_thread(_init_dictation_model, cfg)

                base = DICTATION_DIR / session_id
                webm_path = base.with_suffix(".webm")
                tail_wav_path = base.with_name(base.name + "_tail.wav")
                full_wav_path = base.with_name(base.name + "_full.wav")
                webm_path.write_bytes(b"")

                session = DictationSession(
                    session_id=session_id,
                    cfg=cfg,
                    profile=profile,
                    language=language,
                    summarize_enabled=summarize_enabled,
                    source_mime=source_mime,
                    webm_path=webm_path,
                    tail_wav_path=tail_wav_path,
                    full_wav_path=full_wav_path,
                    running=True,
                    final_text="",
                    last_chunk_text="",
                    model=model,
                    model_device=model_device,
                    model_compute_type=model_compute_type,
                    manual_text_override=False,
                    saved_once=False,
                    worker=None,
                )
                session.worker = asyncio.create_task(_dictation_worker(websocket, session_id))
                DICTATION_SESSIONS[session_id] = session
                await websocket.send_json(
                    {
                        "type": "started",
                        "session_id": session_id,
                        "profile": profile,
                        "language": language,
                        "device": model_device,
                        "model": cfg.stt.model_name,
                    }
                )
                await websocket.send_json({"type": "state", "state": "recording"})
                continue

            if command == "stop":
                await stop_worker()
                if session:
                    await _dictation_step(websocket, session)
                    await websocket.send_json({"type": "final", "text": session.final_text})
                    await websocket.send_json({"type": "stopped"})
                    await websocket.send_json({"type": "state", "state": "stopped"})
                    if session.cfg.dictation.auto_save:
                        await _save_dictation_session(websocket, session, session_id)
                continue

            if command == "flush":
                if session:
                    await _dictation_step(websocket, session)
                continue

            if command == "clear":
                if session:
                    session.final_text = ""
                    session.manual_text_override = False
                    session.saved_once = False
                    if session.webm_path.exists():
                        session.webm_path.write_bytes(b"")
                    await websocket.send_json({"type": "final", "text": ""})
                continue

            if command == "set_text":
                if not session:
                    await websocket.send_json({"type": "error", "message": "dictation not started"})
                    continue
                incoming = str(payload.get("text", "")).strip()
                session.final_text = incoming
                session.manual_text_override = bool(incoming)
                await websocket.send_json({"type": "final", "text": session.final_text})
                continue

            if command == "save":
                if not session:
                    await websocket.send_json({"type": "error", "message": "dictation not started"})
                    continue
                incoming = str(payload.get("text_override", "")).strip()
                if incoming:
                    session.final_text = incoming
                    session.manual_text_override = True
                await stop_worker()
                await _save_dictation_session(websocket, session, session_id)
                continue

            await websocket.send_json({"type": "error", "message": f"unknown command: {command}"})
    except WebSocketDisconnect:
        pass
    finally:
        await stop_worker()
        if session:
            for path in (session.tail_wav_path, session.full_wav_path):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
        DICTATION_SESSIONS.pop(session_id, None)


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
            try:
                created_at = datetime.now().isoformat(timespec="seconds")
                meta = _read_transcript_meta(out_dir)
                title = str(meta.get("title") or Path(job.filename).stem).strip()
                await asyncio.to_thread(
                    add_transcription_history,
                    INDEX_DB_PATH,
                    job_id,
                    Path(job.filename).name,
                    title,
                    str(out_dir),
                    created_at,
                )
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

