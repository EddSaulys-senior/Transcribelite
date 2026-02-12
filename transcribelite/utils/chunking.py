from __future__ import annotations

from typing import List


def chunk_text_words(
    text: str,
    words_per_chunk: int = 450,
    overlap: int = 60,
) -> List[str]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return []

    words = cleaned.split(" ")
    if words_per_chunk <= 0:
        words_per_chunk = 450
    if overlap < 0:
        overlap = 0
    if overlap >= words_per_chunk:
        overlap = max(0, words_per_chunk // 4)

    step = max(1, words_per_chunk - overlap)
    chunks: List[str] = []
    for start in range(0, len(words), step):
        end = min(len(words), start + words_per_chunk)
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(words):
            break
    return chunks

