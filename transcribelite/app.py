from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Iterable, List

from transcribelite import __version__
from transcribelite.config import AppConfig, init_config, load_config
from transcribelite.pipeline.export import export_outputs
from transcribelite.pipeline.ingest import prepare_wav
from transcribelite.pipeline.stt_faster_whisper import transcribe_file
from transcribelite.pipeline.summarize_ollama import check_ollama_health, summarize_text
from transcribelite.utils.logging_setup import setup_logging

MEDIA_EXTS = {
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


def _find_media(path: Path, recursive: bool) -> List[Path]:
    iterator: Iterable[Path]
    if path.is_file():
        return [path]
    if recursive:
        iterator = path.rglob("*")
    else:
        iterator = path.glob("*")
    return [p for p in iterator if p.is_file() and p.suffix.lower() in MEDIA_EXTS]


def _apply_profile_preset(cfg: AppConfig, profile_name: str, set_profile_name: bool = True) -> None:
    preset = cfg.profile_presets.get(profile_name)
    if preset is None:
        raise ValueError(f"Unknown profile: {profile_name}")
    if set_profile_name:
        cfg.profile_name = profile_name
    cfg.stt.model_name = preset.model_name
    cfg.stt.compute_type = preset.compute_type
    cfg.stt.beam_size = preset.beam_size


def _wav_duration_seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate() or 1
        return float(frames) / float(rate)


def _choose_auto_profile(cfg: AppConfig, duration_s: float) -> str:
    duration_min = duration_s / 60.0
    auto_cfg = cfg.profile_auto
    if duration_min <= auto_cfg.short_max_minutes:
        return auto_cfg.short_profile
    if duration_min <= auto_cfg.medium_max_minutes:
        return auto_cfg.medium_profile
    return auto_cfg.long_profile


def _apply_overrides(cfg: AppConfig, args: argparse.Namespace) -> None:
    if getattr(args, "profile", None):
        requested = str(args.profile).strip().lower()
        if requested == "auto":
            cfg.profile_name = "auto"
        else:
            _apply_profile_preset(cfg, requested)
    if getattr(args, "device", None):
        cfg.stt.device = args.device
    if getattr(args, "compute_type", None):
        cfg.stt.compute_type = args.compute_type
    if getattr(args, "model_name", None):
        cfg.stt.model_name = args.model_name
    if getattr(args, "summary", None) is False:
        cfg.summarize.enabled = False


def run_doctor(cfg: AppConfig) -> int:
    print("== TranscribeLite Doctor ==")
    ok = True

    print(f"[python] {platform.python_version()} ({sys.executable})")

    ffmpeg_ok = False
    try:
        result = subprocess.run(
            [cfg.paths.ffmpeg_path, "-version"], capture_output=True, text=True, timeout=15
        )
        ffmpeg_ok = result.returncode == 0
    except Exception:  # noqa: BLE001
        ffmpeg_ok = False
    print(f"[ffmpeg] {'OK' if ffmpeg_ok else 'FAIL'} ({cfg.paths.ffmpeg_path})")
    ok = ok and ffmpeg_ok

    torch_ok = False
    cuda_ok = False
    try:
        import torch  # type: ignore

        torch_ok = True
        cuda_ok = bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        pass
    print(f"[torch] {'OK' if torch_ok else 'FAIL'}")
    print(f"[torch.cuda] {'OK' if cuda_ok else 'FAIL'}")

    fw_ok = False
    try:
        import faster_whisper  # noqa: F401

        fw_ok = True
    except Exception:  # noqa: BLE001
        fw_ok = False
    print(f"[faster-whisper] {'OK' if fw_ok else 'FAIL'}")
    ok = ok and fw_ok

    ollama_ok, reason = check_ollama_health(cfg)
    print(f"[ollama] {'OK' if ollama_ok else 'WARN'} ({reason})")
    return 0 if ok else 1


def run_transcribe(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, init_if_missing=True)
    _apply_overrides(cfg, args)
    logger = setup_logging(cfg.paths.logs_dir / "transcribelite.log")
    logger.info("Using config: %s", cfg.config_path)
    logger.info("Profile: %s", cfg.profile_name)

    target = Path(args.input).resolve()
    files = _find_media(target, args.recursive)
    if not files:
        print(f"No supported media files found: {target}")
        return 1

    logger.info("Found %s media file(s)", len(files))
    base_model_name = cfg.stt.model_name
    base_compute_type = cfg.stt.compute_type
    base_beam_size = cfg.stt.beam_size
    cli_has_stt_override = bool(args.model_name or args.compute_type)
    auto_mode = cfg.profile_name == "auto"

    for idx, media in enumerate(files, start=1):
        print(f"[{idx}/{len(files)}] Processing: {media.name}")
        t0 = time.perf_counter()
        try:
            t_ingest = time.perf_counter()
            wav = prepare_wav(media, cfg.paths)
            print(f"  ingest: {time.perf_counter() - t_ingest:.1f}s")
            if auto_mode and not cli_has_stt_override:
                duration_s = _wav_duration_seconds(wav)
                selected = _choose_auto_profile(cfg, duration_s)
                _apply_profile_preset(cfg, selected, set_profile_name=False)
                logger.info(
                    "Auto profile selected for %s: %s (duration %.1fs)",
                    media.name,
                    selected,
                    duration_s,
                )
            else:
                cfg.stt.model_name = base_model_name
                cfg.stt.compute_type = base_compute_type
                cfg.stt.beam_size = base_beam_size

            t_stt = time.perf_counter()
            stt_result = transcribe_file(wav, cfg)
            print(f"  stt: {time.perf_counter() - t_stt:.1f}s")
            used_device = stt_result["meta"].get("device")
            if cfg.stt.device.lower() == "cuda" and used_device != "cuda":
                logger.warning("CUDA requested but unavailable; switched to CPU/int8")

            summary = None
            summary_error = None
            if cfg.summarize.enabled:
                t_sum = time.perf_counter()
                summary, summary_error = summarize_text(stt_result["text"], cfg)
                print(f"  summarize: {time.perf_counter() - t_sum:.1f}s")
                if summary_error:
                    logger.warning("Summary skipped for %s: %s", media.name, summary_error)
            else:
                summary_error = "summary disabled in config"

            t_export = time.perf_counter()
            out_dir = export_outputs(
                cfg=cfg,
                source_path=media,
                transcript_text=stt_result["text"],
                segments=stt_result["segments"],
                stt_meta=stt_result["meta"],
                summary=summary,
                summary_error=summary_error,
            )
            print(f"  export: {time.perf_counter() - t_export:.1f}s")
            print(f"  done: {time.perf_counter() - t0:.1f}s -> {out_dir}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed processing %s", media)
            print(f"  failed: {exc}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="transcribelite")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    parser.add_argument("--version", action="version", version=f"transcribelite {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_transcribe = sub.add_parser("transcribe", help="Transcribe media file or folder")
    p_transcribe.add_argument("input", help="File or folder path")
    p_transcribe.add_argument("--recursive", action="store_true", help="Scan folder recursively")
    p_transcribe.add_argument(
        "--profile",
        choices=["auto", "fast", "balanced", "quality"],
        help="Use preset for speed/quality (overrides STT settings for this run)",
    )
    p_transcribe.add_argument("--device", choices=["cuda", "cpu"], help="Override STT device")
    p_transcribe.add_argument("--compute-type", help="Override STT compute_type")
    p_transcribe.add_argument("--model-name", help="Override STT model_name")
    p_transcribe.add_argument(
        "--no-summary", dest="summary", action="store_false", help="Disable summarization"
    )
    p_transcribe.set_defaults(summary=None)

    sub.add_parser("doctor", help="Run environment checks")

    p_config = sub.add_parser("config", help="Config actions")
    p_config.add_argument("--init", action="store_true", help="Create config.ini if missing")
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "config":
        path = Path(args.config).resolve()
        if args.init:
            if not path.exists():
                init_config(path)
                print(f"Created config: {path}")
            else:
                print(f"Config already exists: {path}")
            return 0
        parser.error("config command requires --init")

    cfg = load_config(args.config, init_if_missing=True)

    if args.command == "doctor":
        return run_doctor(cfg)
    if args.command == "transcribe":
        return run_transcribe(args)
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
