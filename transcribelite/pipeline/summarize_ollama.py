from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from transcribelite.config import AppConfig


class OllamaError(RuntimeError):
    pass


class OllamaAuthError(OllamaError):
    pass


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


def _is_cloud_model(model_name: str) -> bool:
    return str(model_name or "").strip().lower().endswith("-cloud")


def resolve_ollama_target(
    model_name: str,
    mode: str,
    url_local: str,
    url_cloud: str,
) -> tuple[str, bool]:
    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode == "local":
        return url_local.rstrip("/"), False
    if normalized_mode == "cloud":
        return url_cloud.rstrip("/"), True
    if _is_cloud_model(model_name):
        return url_cloud.rstrip("/"), True
    return url_local.rstrip("/"), False


def _api_key_env_name(cfg: AppConfig) -> str:
    return (cfg.summarize.ollama_api_key_env or "OLLAMA_API_KEY").strip() or "OLLAMA_API_KEY"


def _get_cloud_api_key(cfg: AppConfig) -> str:
    env_name = _api_key_env_name(cfg)
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise OllamaAuthError(f"нужен ключ {env_name}")
    return key


def get_auth_headers(cfg: AppConfig, is_cloud: bool) -> dict[str, str]:
    if not is_cloud:
        return {}
    key = _get_cloud_api_key(cfg)
    return {"Authorization": f"Bearer {key}"}


def _request_json(
    method: str,
    url: str,
    timeout_s: int,
    headers: Optional[dict[str, str]] = None,
    json_payload: Optional[dict[str, Any]] = None,
    retries: int = 0,
    auth_error_message: str = "нужен ключ OLLAMA_API_KEY",
) -> dict[str, Any]:
    last_exc: Optional[Exception] = None
    for _ in range(retries + 1):
        try:
            response = requests.request(
                method,
                url,
                timeout=timeout_s,
                headers=headers or None,
                json=json_payload,
            )
            if response.status_code == 401:
                raise OllamaAuthError(auth_error_message)
            if response.status_code >= 400:
                message = ""
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        message = str(payload.get("error") or "").strip()
                except Exception:
                    message = response.text.strip()
                raise OllamaError(message or f"Ollama HTTP {response.status_code}")
            if not response.text.strip():
                return {}
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        except OllamaError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    raise OllamaError("ollama request failed") from last_exc


def _list_tags_from_target(
    cfg: AppConfig,
    base_url: str,
    is_cloud: bool,
    timeout_s: int = 15,
) -> list[str]:
    headers = get_auth_headers(cfg, is_cloud)
    auth_error_message = f"нужен ключ {_api_key_env_name(cfg)}"
    data = _request_json(
        "GET",
        f"{base_url}/api/tags",
        timeout_s=timeout_s,
        headers=headers,
        retries=0,
        auth_error_message=auth_error_message,
    )
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


def check_ollama_health(cfg: AppConfig, model_name: Optional[str] = None) -> Tuple[bool, str]:
    target_model = model_name or cfg.summarize.model
    base_url, is_cloud = resolve_ollama_target(
        target_model,
        cfg.summarize.ollama_mode,
        cfg.summarize.ollama_url_local,
        cfg.summarize.ollama_url_cloud,
    )
    try:
        _list_tags_from_target(cfg, base_url, is_cloud, timeout_s=10)
        return True, "ok"
    except OllamaAuthError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _is_model_not_found_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "not found" in text and "model" in text


def generate_text(
    cfg: AppConfig,
    prompt: str,
    timeout_s: Optional[int] = None,
    num_predict: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    model_override: Optional[str] = None,
) -> str:
    model_name = (model_override or cfg.summarize.model).strip()
    options: dict[str, Any] = {}
    if num_predict is not None:
        options["num_predict"] = int(num_predict)
    if temperature is not None:
        options["temperature"] = float(temperature)
    if top_p is not None:
        options["top_p"] = float(top_p)

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }

    mode = cfg.summarize.ollama_mode
    base_url, is_cloud = resolve_ollama_target(
        model_name,
        mode,
        cfg.summarize.ollama_url_local,
        cfg.summarize.ollama_url_cloud,
    )

    def _run(target_url: str, cloud: bool) -> str:
        headers = get_auth_headers(cfg, cloud)
        auth_error_message = f"нужен ключ {_api_key_env_name(cfg)}"
        data = _request_json(
            "POST",
            f"{target_url}/api/generate",
            timeout_s=timeout_s or cfg.summarize.timeout_s,
            headers=headers,
            json_payload=payload,
            retries=1,
            auth_error_message=auth_error_message,
        )
        response = data.get("response")
        if not isinstance(response, str):
            raise OllamaError("Ollama returned empty response")
        return response.strip()

    try:
        return _run(base_url, is_cloud)
    except OllamaAuthError as exc:
        raise OllamaError(str(exc)) from exc
    except OllamaError as exc:
        should_fallback = (
            mode == "auto"
            and not is_cloud
            and _is_model_not_found_error(exc)
        )
        if not should_fallback:
            raise
        cloud_url = cfg.summarize.ollama_url_cloud.rstrip("/")
        return _run(cloud_url, True)


def list_ollama_models_detailed(cfg: AppConfig) -> list[dict[str, Any]]:
    mode = cfg.summarize.ollama_mode
    items: dict[str, dict[str, Any]] = {}

    def add(names: list[str], source: str, is_cloud_source: bool) -> None:
        for name in names:
            existing = items.get(name)
            cloud_flag = is_cloud_source or _is_cloud_model(name)
            if existing:
                existing["is_cloud"] = bool(existing["is_cloud"] or cloud_flag)
                if source not in existing["sources"]:
                    existing["sources"].append(source)
                continue
            items[name] = {
                "name": name,
                "is_cloud": cloud_flag,
                "sources": [source],
            }

    if mode in {"local", "auto"}:
        try:
            local_names = _list_tags_from_target(cfg, cfg.summarize.ollama_url_local.rstrip("/"), False)
            add(local_names, "local", False)
        except Exception:
            pass

    if mode in {"cloud", "auto"}:
        try:
            cloud_names = _list_tags_from_target(cfg, cfg.summarize.ollama_url_cloud.rstrip("/"), True)
            add(cloud_names, "cloud", True)
        except OllamaAuthError:
            # In auto mode we still return local models without failing.
            if mode == "cloud":
                raise
        except Exception:
            if mode == "cloud":
                raise

    names = sorted(items.keys(), key=lambda s: s.lower())
    return [items[name] for name in names]


def list_ollama_models(cfg: AppConfig) -> list[str]:
    return [str(item.get("name")) for item in list_ollama_models_detailed(cfg)]


def ensure_model_available(
    cfg: AppConfig,
    model: str,
    timeout_s: int = 120,
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    required = model.strip()
    if not required:
        raise OllamaError("Model name is empty")

    base_url, is_cloud = resolve_ollama_target(
        required,
        cfg.summarize.ollama_mode,
        cfg.summarize.ollama_url_local,
        cfg.summarize.ollama_url_cloud,
    )

    names = _list_tags_from_target(cfg, base_url, is_cloud, timeout_s=15)
    if required in names:
        if on_progress:
            on_progress("model already available")
        return

    if is_cloud:
        raise OllamaError(f"cloud model is not available: {required}")

    if on_progress:
        on_progress(f"downloading model: {required}")

    url = f"{base_url}/api/pull"
    payload = {"name": required, "stream": True}
    with requests.post(url, json=payload, timeout=timeout_s, stream=True) as response:
        if response.status_code >= 400:
            message = response.text.strip() or f"Ollama HTTP {response.status_code}"
            raise OllamaError(message)
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
