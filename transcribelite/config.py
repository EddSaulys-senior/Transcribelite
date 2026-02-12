from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from transcribelite.utils.paths import ensure_dirs, resolve_path


DEFAULT_CONFIG = {
    "paths": {
        "base_dir": ".",
        "models_dir": "models",
        "cache_dir": "cache",
        "output_dir": "output",
        "logs_dir": "logs",
        "wheels_dir": "wheels",
        "ffmpeg_path": "ffmpeg",
    },
    "stt": {
        "engine": "faster_whisper",
        "model_name": "large-v3",
        "device": "cuda",
        "compute_type": "float16",
        "beam_size": "5",
        "vad_filter": "true",
        "language": "auto",
        "task": "transcribe",
    },
    "profile": {"active": "balanced"},
    "profile_fast": {
        "model_name": "small",
        "compute_type": "int8_float32",
        "beam_size": "1",
    },
    "profile_balanced": {
        "model_name": "medium",
        "compute_type": "int8_float32",
        "beam_size": "4",
    },
    "profile_quality": {
        "model_name": "large-v3",
        "compute_type": "float32",
        "beam_size": "5",
    },
    "profile_auto": {
        "short_max_minutes": "2",
        "medium_max_minutes": "12",
        "short_profile": "quality",
        "medium_profile": "balanced",
        "long_profile": "fast",
    },
    "summarize": {
        "enabled": "true",
        "ollama_url": "http://127.0.0.1:11434",
        "model": "llama3.1:8b",
        "prompt_template": "prompts/meeting_ru.txt",
        "timeout_s": "120",
        "max_chars": "18000",
        "stream": "false",
    },
    "export": {
        "save_txt": "true",
        "save_json": "true",
        "save_md": "true",
        "include_timestamps": "true",
        "filename_mode": "timestamp",
    },
}


@dataclass
class PathsConfig:
    base_dir: Path
    models_dir: Path
    cache_dir: Path
    output_dir: Path
    logs_dir: Path
    wheels_dir: Path
    ffmpeg_path: str


@dataclass
class SttConfig:
    engine: str
    model_name: str
    device: str
    compute_type: str
    beam_size: int
    vad_filter: bool
    language: str
    task: str


@dataclass
class SummarizeConfig:
    enabled: bool
    ollama_url: str
    model: str
    prompt_template: Path
    timeout_s: int
    max_chars: int
    stream: bool


@dataclass
class ExportConfig:
    save_txt: bool
    save_json: bool
    save_md: bool
    include_timestamps: bool
    filename_mode: str


@dataclass
class ProfilePreset:
    model_name: str
    compute_type: str
    beam_size: int


@dataclass
class ProfileAutoConfig:
    short_max_minutes: float
    medium_max_minutes: float
    short_profile: str
    medium_profile: str
    long_profile: str


@dataclass
class AppConfig:
    config_path: Path
    profile_name: str
    paths: PathsConfig
    stt: SttConfig
    profile_presets: Dict[str, ProfilePreset]
    profile_auto: ProfileAutoConfig
    summarize: SummarizeConfig
    export: ExportConfig


def _apply_profile_overrides(parser: ConfigParser) -> str:
    active = parser.get("profile", "active", fallback="custom").strip().lower()
    if active in ("", "custom", "auto"):
        return "custom"

    section = f"profile_{active}"
    if not parser.has_section(section):
        return active

    for key, value in parser.items(section):
        if key in ("model_name", "compute_type", "beam_size", "device", "language", "task", "vad_filter"):
            parser.set("stt", key, value)
        elif key.startswith("summarize_"):
            parser.set("summarize", key.removeprefix("summarize_"), value)
    return active


def _load_profile_presets(parser: ConfigParser) -> Dict[str, ProfilePreset]:
    return {
        "fast": ProfilePreset(
            model_name=parser.get("profile_fast", "model_name"),
            compute_type=parser.get("profile_fast", "compute_type"),
            beam_size=parser.getint("profile_fast", "beam_size"),
        ),
        "balanced": ProfilePreset(
            model_name=parser.get("profile_balanced", "model_name"),
            compute_type=parser.get("profile_balanced", "compute_type"),
            beam_size=parser.getint("profile_balanced", "beam_size"),
        ),
        "quality": ProfilePreset(
            model_name=parser.get("profile_quality", "model_name"),
            compute_type=parser.get("profile_quality", "compute_type"),
            beam_size=parser.getint("profile_quality", "beam_size"),
        ),
    }


def _build_default_parser() -> ConfigParser:
    parser = ConfigParser()
    parser.read_dict(DEFAULT_CONFIG)
    return parser


def init_config(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    parser = _build_default_parser()
    with config_path.open("w", encoding="utf-8") as f:
        parser.write(f)


def load_config(config_path: str | None = None, init_if_missing: bool = True) -> AppConfig:
    cfg_path = Path(config_path or "config.ini").resolve()
    if not cfg_path.exists():
        if not init_if_missing:
            raise FileNotFoundError(f"Config not found: {cfg_path}")
        init_config(cfg_path)

    parser = _build_default_parser()
    parser.read(cfg_path, encoding="utf-8")
    requested_profile = parser.get("profile", "active", fallback="custom").strip().lower()
    active_profile = _apply_profile_overrides(parser)

    cfg_dir = cfg_path.parent
    base_dir = resolve_path(cfg_dir, parser.get("paths", "base_dir"))
    paths = PathsConfig(
        base_dir=base_dir,
        models_dir=resolve_path(base_dir, parser.get("paths", "models_dir")),
        cache_dir=resolve_path(base_dir, parser.get("paths", "cache_dir")),
        output_dir=resolve_path(base_dir, parser.get("paths", "output_dir")),
        logs_dir=resolve_path(base_dir, parser.get("paths", "logs_dir")),
        wheels_dir=resolve_path(base_dir, parser.get("paths", "wheels_dir")),
        ffmpeg_path=parser.get("paths", "ffmpeg_path"),
    )
    ensure_dirs(
        [paths.models_dir, paths.cache_dir, paths.output_dir, paths.logs_dir, paths.wheels_dir]
    )

    summarize_prompt = resolve_path(base_dir, parser.get("summarize", "prompt_template"))
    summarize = SummarizeConfig(
        enabled=parser.getboolean("summarize", "enabled"),
        ollama_url=parser.get("summarize", "ollama_url").rstrip("/"),
        model=parser.get("summarize", "model"),
        prompt_template=summarize_prompt,
        timeout_s=parser.getint("summarize", "timeout_s"),
        max_chars=parser.getint("summarize", "max_chars"),
        stream=parser.getboolean("summarize", "stream"),
    )
    profile_presets = _load_profile_presets(parser)
    profile_auto = ProfileAutoConfig(
        short_max_minutes=parser.getfloat("profile_auto", "short_max_minutes"),
        medium_max_minutes=parser.getfloat("profile_auto", "medium_max_minutes"),
        short_profile=parser.get("profile_auto", "short_profile").strip().lower(),
        medium_profile=parser.get("profile_auto", "medium_profile").strip().lower(),
        long_profile=parser.get("profile_auto", "long_profile").strip().lower(),
    )
    if requested_profile == "auto":
        active_profile = "auto"

    return AppConfig(
        config_path=cfg_path,
        profile_name=active_profile,
        paths=paths,
        stt=SttConfig(
            engine=parser.get("stt", "engine"),
            model_name=parser.get("stt", "model_name"),
            device=parser.get("stt", "device"),
            compute_type=parser.get("stt", "compute_type"),
            beam_size=parser.getint("stt", "beam_size"),
            vad_filter=parser.getboolean("stt", "vad_filter"),
            language=parser.get("stt", "language"),
            task=parser.get("stt", "task"),
        ),
        profile_presets=profile_presets,
        profile_auto=profile_auto,
        summarize=summarize,
        export=ExportConfig(
            save_txt=parser.getboolean("export", "save_txt"),
            save_json=parser.getboolean("export", "save_json"),
            save_md=parser.getboolean("export", "save_md"),
            include_timestamps=parser.getboolean("export", "include_timestamps"),
            filename_mode=parser.get("export", "filename_mode"),
        ),
    )
