from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

from transcribelite.config import AppConfig


def _resolve_device_and_compute(preferred_device: str, preferred_compute: str) -> Tuple[str, str]:
    device = preferred_device.lower()
    compute_type = preferred_compute
    if device != "cuda":
        return "cpu", "int8" if compute_type == "float16" else compute_type

    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda", compute_type
    except Exception:  # noqa: BLE001
        pass
    return "cpu", "int8"


def transcribe_file(wav_path: Path, cfg: AppConfig) -> Dict[str, object]:
    from faster_whisper import WhisperModel  # lazy import for doctor/fallback clarity

    def _run(device: str, compute_type: str) -> Tuple[List[Dict[str, object]], str, object]:
        model = WhisperModel(
            cfg.stt.model_name,
            device=device,
            compute_type=compute_type,
            download_root=str(cfg.paths.models_dir),
        )
        language = None if cfg.stt.language.lower() == "auto" else cfg.stt.language
        segments_iter, info = model.transcribe(
            str(wav_path),
            language=language,
            task=cfg.stt.task,
            beam_size=cfg.stt.beam_size,
            vad_filter=cfg.stt.vad_filter,
        )
        segments: List[Dict[str, object]] = []
        text_parts: List[str] = []
        for seg in segments_iter:
            segment_text = seg.text.strip()
            if segment_text:
                text_parts.append(segment_text)
            segments.append({"start": float(seg.start), "end": float(seg.end), "text": segment_text})
        return segments, " ".join(text_parts).strip(), info

    device, compute_type = _resolve_device_and_compute(cfg.stt.device, cfg.stt.compute_type)
    attempts = [(device, compute_type)]
    if device == "cuda" and compute_type != "float32":
        attempts.append(("cuda", "float32"))
    attempts.append(("cpu", "int8"))

    last_exc: Exception | None = None
    for attempt_device, attempt_compute in attempts:
        try:
            segments, text, info = _run(attempt_device, attempt_compute)
            device, compute_type = attempt_device, attempt_compute
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    else:
        raise RuntimeError("Unable to transcribe with CUDA or CPU fallback") from last_exc

    return {
        "text": text,
        "segments": segments,
        "meta": {
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "stt_engine": cfg.stt.engine,
            "model_name": cfg.stt.model_name,
            "device": device,
            "compute_type": compute_type,
        },
    }
