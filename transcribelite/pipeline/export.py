from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from transcribelite import __version__
from transcribelite.config import AppConfig
from transcribelite.pipeline.summarize_ollama import check_ollama_health, generate_text


def _safe_name(name: str) -> str:
    allowed = []
    for ch in name:
        if ch.isalnum() or ch in ("-", "_", "."):
            allowed.append(ch)
        else:
            allowed.append("_")
    return "".join(allowed).strip("_") or "input"


def _format_time(seconds: float) -> str:
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _render_note(
    cfg: AppConfig,
    source_path: Path,
    title: str,
    transcript_text: str,
    segments: List[Dict[str, object]],
    stt_meta: Dict[str, object],
    summary: Optional[str],
    summary_error: Optional[str],
    created_at: str,
) -> str:
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return ""

    template_path = cfg.paths.base_dir / "prompts" / "note_template.md"
    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")
    else:
        template = (
            "# {title}\n\n"
            "- Date: {date}\n"
            "- Source: {source_file}\n"
            "- STT model: {stt_model}\n"
            "- Device: {device}\n"
            "- Compute type: {compute_type}\n\n"
            "## Summary\n{summary_block}\n\n"
            "## Action items\n{action_items}\n\n"
            "## Transcript\n{transcript_block}\n"
        )

    if summary:
        summary_block = summary
    else:
        reason = summary_error or "summary skipped"
        summary_block = f"summary skipped: {reason}"

    lines = []
    if cfg.export.include_timestamps:
        for seg in segments:
            lines.append(
                f"[{_format_time(float(seg['start']))} - {_format_time(float(seg['end']))}] {seg['text']}"
            )
        transcript_block = "\n".join(lines).strip()
    else:
        transcript_block = transcript_text

    duration_s = 0.0
    if segments:
        duration_s = float(segments[-1].get("end", 0.0))

    action_items_block = "- n/a"
    decisions_block = "- n/a"
    risks_block = "- n/a"
    tags_block = "- n/a"
    if summary:
        action_match = re.search(
            r"(?is)##\s*Action items\s*(.+?)(?:\n##\s+|\Z)", summary
        )
        decisions_match = re.search(r"(?is)##\s*Decisions\s*(.+?)(?:\n##\s+|\Z)", summary)
        risks_match = re.search(
            r"(?is)##\s*Risks.*?\s*(.+?)(?:\n##\s+|\Z)", summary
        )
        tags_match = re.search(r"(?is)##\s*Tags\s*(.+?)(?:\n##\s+|\Z)", summary)
        if action_match:
            action_items_block = action_match.group(1).strip()
        if decisions_match:
            decisions_block = decisions_match.group(1).strip()
        if risks_match:
            risks_block = risks_match.group(1).strip()
        if tags_match:
            tags_block = tags_match.group(1).strip()

    context = _SafeDict(
        title=title or f"TranscribeLite note: {source_path.name}",
        date=created_at,
        source_file=str(source_path),
        source=str(source_path),
        stt_model=stt_meta.get("model_name", "unknown"),
        device=stt_meta.get("device", "unknown"),
        compute_type=stt_meta.get("compute_type", "unknown"),
        language=stt_meta.get("language", "unknown"),
        duration=f"{duration_s:.1f}s",
        summary_block=summary_block,
        action_items="- n/a",
        action_items_block=action_items_block,
        decisions_block=decisions_block,
        risks_block=risks_block,
        tags_block=tags_block,
        transcript_block=transcript_block,
    )
    rendered = template.format_map(context)
    if "{transcript_block}" in rendered and "{summary_block}" in rendered:
        fallback_template = (
            "# {title}\n\n"
            "- Date: {date}\n"
            "- Source: {source_file}\n"
            "- STT model: {stt_model}\n"
            "- Device: {device}\n"
            "- Compute type: {compute_type}\n\n"
            "## Summary\n{summary_block}\n\n"
            "## Action items\n{action_items_block}\n\n"
            "## Transcript\n{transcript_block}\n"
        )
        return fallback_template.format_map(context)
    return rendered


def _clean_title(raw: str) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    text = text.strip("\"'`")
    text = re.sub(r"[.?!,:;]+$", "", text).strip()
    words = text.split()
    if len(words) > 10:
        words = words[:10]
    cleaned = " ".join(words).strip()
    return cleaned or "Без названия"


def _first_words_title(text: str, min_words: int = 6, max_words: int = 10) -> str:
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    if not words:
        return "Без названия"
    size = min(max_words, max(min_words, len(words)))
    return _clean_title(" ".join(words[:size]))


def _title_source_text(summary: Optional[str], transcript_text: str) -> str:
    if summary and summary.strip():
        summary_clean = re.sub(r"(?m)^\s*#+\s*", "", summary).strip()
        summary_clean = re.sub(r"\s+", " ", summary_clean).strip()
        if summary_clean:
            return summary_clean[:500]
    body = re.sub(r"\s+", " ", transcript_text).strip()
    return body[:400]


def make_title(cfg: AppConfig, source_text: str) -> str:
    source_text = re.sub(r"\s+", " ", source_text).strip()
    if not source_text:
        return "Без названия"

    prompt_template_path = cfg.paths.base_dir / "prompts" / "title_ru.txt"
    prompt_template = ""
    if prompt_template_path.exists():
        try:
            prompt_template = prompt_template_path.read_text(encoding="utf-8").strip()
        except Exception:
            prompt_template = ""
    if not prompt_template:
        prompt_template = (
            "Сделай заголовок 4-8 слов, без кавычек, без точки, только по смыслу.\n"
            "Не выдумывай фактов.\n\n"
            "Текст:\n{text}\n"
        )

    healthy, _ = check_ollama_health(cfg)
    if healthy:
        try:
            prompt = prompt_template.format(text=source_text)
            response = generate_text(
                cfg,
                prompt,
                timeout_s=min(cfg.summarize.timeout_s, 45),
                num_predict=32,
                temperature=0.2,
                top_p=0.9,
            )
            return _clean_title(response)
        except Exception:
            pass
    return _first_words_title(source_text)


def export_outputs(
    cfg: AppConfig,
    source_path: Path,
    transcript_text: str,
    segments: List[Dict[str, object]],
    stt_meta: Dict[str, object],
    summary: Optional[str],
    summary_error: Optional[str],
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"{timestamp}_{_safe_name(source_path.stem)}"
    out_dir = cfg.paths.output_dir / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now().isoformat(timespec="seconds")
    title_source = _title_source_text(summary, transcript_text)
    title = make_title(cfg, title_source)

    if cfg.export.save_txt:
        (out_dir / "transcript.txt").write_text(transcript_text, encoding="utf-8")

    if cfg.export.save_json:
        payload = {
            "meta": {
                "created_at": created_at,
                "source_file": str(source_path),
                "title": title,
                "app_version": __version__,
                "profile": cfg.profile_name,
                "requested_stt": {
                    "model_name": cfg.stt.model_name,
                    "device": cfg.stt.device,
                    "compute_type": cfg.stt.compute_type,
                    "beam_size": cfg.stt.beam_size,
                    "language": cfg.stt.language,
                },
                **stt_meta,
                "summary_status": "ok" if summary else "skipped",
                "summary_error": summary_error,
            },
            "text": transcript_text,
            "segments": segments,
            "summary": summary,
        }
        (out_dir / "transcript.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if cfg.export.save_md:
        note_text = _render_note(
            cfg=cfg,
            source_path=source_path,
            title=title,
            transcript_text=transcript_text,
            segments=segments,
            stt_meta=stt_meta,
            summary=summary,
            summary_error=summary_error,
            created_at=created_at,
        )
        (out_dir / "note.md").write_text(note_text, encoding="utf-8")

    return out_dir
