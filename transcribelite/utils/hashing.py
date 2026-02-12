from __future__ import annotations

import hashlib
from pathlib import Path


def file_identity_hash(file_path: Path) -> str:
    stat = file_path.stat()
    payload = f"{file_path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}".encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()[:16]

