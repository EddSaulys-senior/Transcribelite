from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from transcribelite.utils.chunking import chunk_text_words


@dataclass
class ChunkHit:
    chunk_id: int
    chunk_index: int
    text: str


@dataclass
class GlobalChunkHit:
    chunk_id: int
    job_id: str
    chunk_index: int
    text: str
    title: str
    created_at: str
    output_dir: str


@dataclass
class QaHistoryItem:
    id: int
    job_id: str
    question: str
    answer: str
    created_at: str


@dataclass
class DictationHistoryItem:
    id: int
    job_id: str
    output_dir: str
    text_preview: str
    created_at: str


@dataclass
class TranscriptionHistoryItem:
    id: int
    job_id: str
    source_name: str
    title: str
    output_dir: str
    created_at: str


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            job_id TEXT PRIMARY KEY,
            output_dir TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(
            job_id UNINDEXED,
            chunk_index UNINDEXED,
            text,
            tokenize='unicode61'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS qa_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dictation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            output_dir TEXT NOT NULL,
            text_preview TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transcription_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            output_dir TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(transcription_history)").fetchall()}
    if "title" not in cols:
        conn.execute("ALTER TABLE transcription_history ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        conn.commit()
    return conn


def index_job(
    db_path: Path,
    job_id: str,
    title: str,
    transcript_text: str,
    output_dir: str,
    created_at: str,
    words_per_chunk: int = 450,
    overlap: int = 60,
) -> int:
    chunks = chunk_text_words(
        transcript_text,
        words_per_chunk=words_per_chunk,
        overlap=overlap,
    )
    with open_db(db_path) as conn:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM chunks_fts WHERE job_id = ?", (job_id,))
        conn.execute(
            """
            INSERT INTO meta(job_id, output_dir, title, created_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                output_dir=excluded.output_dir,
                title=excluded.title,
                created_at=excluded.created_at
            """,
            (job_id, output_dir, title, created_at),
        )
        for i, chunk in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunks_fts(job_id, chunk_index, text) VALUES(?, ?, ?)",
                (job_id, i, chunk),
            )
        conn.commit()
    return len(chunks)


def _extract_query_tokens(text: str) -> list[str]:
    tokens = [t for t in re.findall(r"\w+", text.lower(), flags=re.UNICODE) if len(t) > 1]
    unique_tokens: list[str] = []
    seen = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique_tokens.append(token)
        if len(unique_tokens) >= 12:
            break
    return unique_tokens


def search_chunks(db_path: Path, question: str, job_id: str, limit: int = 6) -> List[ChunkHit]:
    limit = max(1, min(int(limit), 12))
    tokens = _extract_query_tokens(question)
    if not tokens:
        return []

    collected: list[ChunkHit] = []
    seen_ids: set[int] = set()

    with open_db(db_path) as conn:
        for token in tokens:
            token_query = f"\"{token}\"*"
            try:
                rows = conn.execute(
                    """
                    SELECT rowid, CAST(chunk_index AS INTEGER), text
                    FROM chunks_fts
                    WHERE job_id = ? AND chunks_fts MATCH ?
                    ORDER BY bm25(chunks_fts)
                    LIMIT ?
                    """,
                    (job_id, token_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                continue

            for row in rows:
                chunk_id = int(row[0])
                if chunk_id in seen_ids:
                    continue
                seen_ids.add(chunk_id)
                collected.append(
                    ChunkHit(
                        chunk_id=chunk_id,
                        chunk_index=int(row[1]),
                        text=str(row[2]),
                    )
                )
                if len(collected) >= limit:
                    return collected
    return collected


def search_global_chunks(db_path: Path, question: str, limit: int = 12) -> List[GlobalChunkHit]:
    limit = max(1, min(int(limit), 30))
    tokens = _extract_query_tokens(question)
    if not tokens:
        return []

    collected: list[GlobalChunkHit] = []
    seen_ids: set[int] = set()

    with open_db(db_path) as conn:
        for token in tokens:
            token_query = f"\"{token}\"*"
            try:
                rows = conn.execute(
                    """
                    SELECT
                        f.rowid,
                        f.job_id,
                        CAST(f.chunk_index AS INTEGER),
                        f.text,
                        m.title,
                        m.created_at,
                        m.output_dir
                    FROM chunks_fts AS f
                    LEFT JOIN meta AS m ON m.job_id = f.job_id
                    WHERE chunks_fts MATCH ?
                    ORDER BY bm25(chunks_fts)
                    LIMIT ?
                    """,
                    (token_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                continue

            for row in rows:
                chunk_id = int(row[0])
                if chunk_id in seen_ids:
                    continue
                seen_ids.add(chunk_id)
                collected.append(
                    GlobalChunkHit(
                        chunk_id=chunk_id,
                        job_id=str(row[1]),
                        chunk_index=int(row[2]),
                        text=str(row[3]),
                        title=str(row[4] or ""),
                        created_at=str(row[5] or ""),
                        output_dir=str(row[6] or ""),
                    )
                )
                if len(collected) >= limit:
                    return collected
    return collected


def add_qa_history(
    db_path: Path,
    job_id: str,
    question: str,
    answer: str,
    created_at: str,
) -> int:
    with open_db(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO qa_history(job_id, question, answer, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (job_id, question, answer, created_at),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def list_qa_history(db_path: Path, limit: int = 50) -> List[QaHistoryItem]:
    limit = max(1, min(int(limit), 200))
    with open_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, job_id, question, answer, created_at
            FROM qa_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        QaHistoryItem(
            id=int(row[0]),
            job_id=str(row[1]),
            question=str(row[2]),
            answer=str(row[3]),
            created_at=str(row[4]),
        )
        for row in rows
    ]


def add_dictation_history(
    db_path: Path,
    job_id: str,
    output_dir: str,
    text_preview: str,
    created_at: str,
) -> int:
    with open_db(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO dictation_history(job_id, output_dir, text_preview, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (job_id, output_dir, text_preview, created_at),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def list_dictation_history(db_path: Path, limit: int = 50) -> List[DictationHistoryItem]:
    limit = max(1, min(int(limit), 200))
    with open_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, job_id, output_dir, text_preview, created_at
            FROM dictation_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        DictationHistoryItem(
            id=int(row[0]),
            job_id=str(row[1]),
            output_dir=str(row[2]),
            text_preview=str(row[3]),
            created_at=str(row[4]),
        )
        for row in rows
    ]


def delete_dictation_history_item(db_path: Path, item_id: int) -> bool:
    with open_db(db_path) as conn:
        cur = conn.execute("DELETE FROM dictation_history WHERE id = ?", (int(item_id),))
        conn.commit()
        return (cur.rowcount or 0) > 0


def add_transcription_history(
    db_path: Path,
    job_id: str,
    source_name: str,
    title: str,
    output_dir: str,
    created_at: str,
) -> int:
    with open_db(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO transcription_history(job_id, source_name, title, output_dir, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (job_id, source_name, title, output_dir, created_at),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def list_transcription_history(db_path: Path, limit: int = 50) -> List[TranscriptionHistoryItem]:
    limit = max(1, min(int(limit), 300))
    with open_db(db_path) as conn:
        cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(transcription_history)").fetchall()}
        if "title" in cols:
            rows = conn.execute(
                """
                SELECT id, job_id, source_name, title, output_dir, created_at
                FROM transcription_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                TranscriptionHistoryItem(
                    id=int(row[0]),
                    job_id=str(row[1]),
                    source_name=str(row[2]),
                    title=str(row[3] or ""),
                    output_dir=str(row[4]),
                    created_at=str(row[5]),
                )
                for row in rows
            ]
        rows = conn.execute(
            """
            SELECT id, job_id, source_name, output_dir, created_at
            FROM transcription_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        TranscriptionHistoryItem(
            id=int(row[0]),
            job_id=str(row[1]),
            source_name=str(row[2]),
            title=str(row[2]),
            output_dir=str(row[3]),
            created_at=str(row[4]),
        )
        for row in rows
    ]


def get_transcription_history_item(db_path: Path, item_id: int) -> Optional[TranscriptionHistoryItem]:
    with open_db(db_path) as conn:
        cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(transcription_history)").fetchall()}
        if "title" in cols:
            row = conn.execute(
                """
                SELECT id, job_id, source_name, title, output_dir, created_at
                FROM transcription_history
                WHERE id = ?
                """,
                (int(item_id),),
            ).fetchone()
            if not row:
                return None
            return TranscriptionHistoryItem(
                id=int(row[0]),
                job_id=str(row[1]),
                source_name=str(row[2]),
                title=str(row[3] or ""),
                output_dir=str(row[4]),
                created_at=str(row[5]),
            )
        row = conn.execute(
            """
            SELECT id, job_id, source_name, output_dir, created_at
            FROM transcription_history
            WHERE id = ?
            """,
            (int(item_id),),
        ).fetchone()
        if not row:
            return None
        return TranscriptionHistoryItem(
            id=int(row[0]),
            job_id=str(row[1]),
            source_name=str(row[2]),
            title=str(row[2]),
            output_dir=str(row[3]),
            created_at=str(row[4]),
        )


def delete_transcription_history_item(db_path: Path, item_id: int) -> bool:
    with open_db(db_path) as conn:
        cur = conn.execute("DELETE FROM transcription_history WHERE id = ?", (int(item_id),))
        conn.commit()
        return (cur.rowcount or 0) > 0


def delete_index_for_job(db_path: Path, job_id: str) -> None:
    with open_db(db_path) as conn:
        conn.execute("DELETE FROM chunks_fts WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM meta WHERE job_id = ?", (job_id,))
        conn.commit()
