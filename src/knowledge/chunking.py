"""Split a research document into passages for the vector store.

Canonical text stays on the document row. A passage is a retrieval slice, not
a new source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass

TARGET_CHARS = 1000
OVERLAP_CHARS = 150


@dataclass(frozen=True)
class Passage:
    """One slice of a research document."""

    topic: str
    content: str


def chunk_document(
    body: str, *, target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS
) -> list[Passage]:
    """Break ``body`` on headings and blank lines.

    Adjacent short paragraphs under the same heading are kept together until
    they reach about ``target`` characters. A single block longer than that is
    split with ``overlap`` characters carried into the next slice.
    """
    topic = "general"
    pending: list[str] = []
    pending_len = 0
    passages: list[Passage] = []

    def flush() -> None:
        nonlocal pending_len
        text = "\n\n".join(pending).strip()
        pending.clear()
        pending_len = 0
        if text:
            passages.extend(_split_long(topic, text, target=target, overlap=overlap))

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            flush()
            topic = line.lstrip("#").strip() or "general"
            continue
        if not line:
            flush()
            continue
        if pending and pending_len + len(line) > target:
            flush()
        pending.append(line)
        pending_len += len(line) + 2
    flush()
    return passages


def _split_long(topic: str, text: str, *, target: int, overlap: int) -> list[Passage]:
    if len(text) <= target:
        return [Passage(topic=topic, content=text)]
    slices: list[Passage] = []
    start = 0
    while start < len(text):
        end = min(start + target, len(text))
        piece = text[start:end].strip()
        if piece:
            slices.append(Passage(topic=topic, content=piece))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return slices
