from __future__ import annotations

import subprocess
from pathlib import Path

from transcribelite.config import PathsConfig
from transcribelite.utils.hashing import file_identity_hash


def prepare_wav(input_path: Path, paths_cfg: PathsConfig) -> Path:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    file_hash = file_identity_hash(input_path)
    wav_path = paths_cfg.cache_dir / f"{file_hash}.wav"
    if wav_path.exists():
        return wav_path

    cmd = [
        paths_cfg.ffmpeg_path,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_path),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed for {input_path}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return wav_path

