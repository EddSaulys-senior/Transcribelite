from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, Tuple

import requests
from transcribelite.config import AppConfig
from transcribelite.utils.http import request_json


def _split_text(text: str, max_chars: int) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    current = []
    current_len = 0
    for part in text.split("\n"):
        addition = len(part) + 1
        if current and current_len + addition > max_chars:
            chunks.append("\n".join(current).strip())
            current = [part]
            current_len = len(part)
        else:
            current.append(part)
            current_len += addition
    if current:
        chunks.append("\n".join(current).strip())
    return [c for c in chunks if c]


def _render_prompt(template_text: str, transcript: str) -> str:
    if "{transcript}" in template_text:
        return template_text.format(transcript=transcript)
    return f"{template_text.strip()}\n\nТранскрипт:\n{transcript}"


def check_ollama_health(cfg: AppConfig) -> Tuple[bool, str]:
    try:
        request_json("GET", f"{cfg.summarize.ollama_url}/api/tags", timeout_s=10, retries=0)
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def generate_text(
    cfg: AppConfig,
    prompt: str,
    timeout_s: Optional[int] = None,
    num_predict: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> str:
    options = {}
    if num_predict is not None:
        options["num_predict"] = int(num_predict)
    if temperature is not None:
        options["temperature"] = float(temperature)
    if top_p is not None:
        options["top_p"] = float(top_p)

    payload = {
        "model": cfg.summarize.model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }
    data = request_json(
        "POST",
        f"{cfg.summarize.ollama_url}/api/generate",
        timeout_s=timeout_s or cfg.summarize.timeout_s,
        retries=1,
        json=payload,
    )
    response = data.get("response")
    if not isinstance(response, str):
        raise RuntimeError("Ollama returned empty response")
    return response.strip()


def list_ollama_models(cfg: AppConfig) -> list[str]:
    data = request_json("GET", f"{cfg.summarize.ollama_url}/api/tags", timeout_s=15, retries=0)
    models = data.get("models")
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        name = str(model.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def ensure_model_available(
    cfg: AppConfig,
    model: str,
    timeout_s: int = 120,
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    required = model.strip()
    if not required:
        raise RuntimeError("Model name is empty")

    names = list_ollama_models(cfg)
    if required in names:
        if on_progress:
            on_progress("model already available")
        return

    if on_progress:
        on_progress(f"downloading model: {required}")

    url = f"{cfg.summarize.ollama_url}/api/pull"
    payload = {"name": required, "stream": True}
    with requests.post(url, json=payload, timeout=timeout_s, stream=True) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            status = ""
            try:
                row = request_json_line(raw_line)
                status = str(row.get("status") or "").strip()
                total = row.get("total")
                completed = row.get("completed")
                if status and total and completed and isinstance(total, int) and total > 0:
                    pct = int((completed / total) * 100)
                    status = f"{status} ({pct}%)"
            except Exception:
                status = str(raw_line).strip()
            if on_progress and status:
                on_progress(status)


def request_json_line(raw_line: str) -> dict:
    import json

    data = json.loads(raw_line)
    if not isinstance(data, dict):
        return {}
    return data


def summarize_text(transcript: str, cfg: AppConfig) -> Tuple[Optional[str], Optional[str]]:
    if not cfg.summarize.enabled:
        return None, "summary disabled in config"
    if not transcript.strip():
        return None, "empty transcript"

    healthy, reason = check_ollama_health(cfg)
    if not healthy:
        return None, f"ollama unavailable: {reason}"

    template_path: Path = cfg.summarize.prompt_template
    if not template_path.exists():
        return None, f"prompt template missing: {template_path}"
    template = template_path.read_text(encoding="utf-8")

    try:
        chunks = _split_text(transcript, cfg.summarize.max_chars)
        chunk_summaries: List[str] = []
        for chunk in chunks:
            prompt = _render_prompt(template, chunk)
            chunk_summaries.append(generate_text(cfg, prompt))
        if len(chunk_summaries) == 1:
            return chunk_summaries[0], None

        final_prompt = _render_prompt(
            template,
            "\n\n".join(
                f"Summary chunk {i + 1}:\n{summary}" for i, summary in enumerate(chunk_summaries)
            ),
        )
        return generate_text(cfg, final_prompt), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
