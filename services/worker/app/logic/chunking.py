import re
from dataclasses import dataclass

# Flush a chunk once the buffer reaches roughly this many characters (or the
# page ends) — a soft target, not a hard cap, since a chunk never splits a
# paragraph mid-way.
CHUNK_FLUSH_THRESHOLD = 1000

_BLANK_LINE = re.compile(r"\n\s*\n")
_TERMINAL_PUNCTUATION = (".", "!", "?", ",", ";", ":")


@dataclass
class Chunk:
    text: str
    page_number: int
    section_header: str | None
    chunk_index: int


def _is_section_header(paragraph: str) -> bool:
    if not paragraph or len(paragraph) >= 80:
        return False
    if paragraph.endswith(_TERMINAL_PUNCTUATION):
        return False
    return paragraph.isupper() or paragraph.istitle()


def chunk_pages(pages: list[str]) -> list[Chunk]:
    """Deterministic chunking: split each page into paragraphs on blank
    lines, track the most recent section header as metadata, and flush a
    chunk once the buffer reaches ~1000 characters or the page ends. No
    overlap between chunks (v0 — see docs/plans/m2-document-ingestion-hub.md).
    """
    chunks: list[Chunk] = []
    current_header: str | None = None
    buffer_parts: list[str] = []
    buffer_len = 0
    buffer_page: int | None = None

    def flush() -> None:
        nonlocal buffer_parts, buffer_len, buffer_page
        if not buffer_parts:
            return
        chunks.append(
            Chunk(
                text="\n\n".join(buffer_parts),
                page_number=buffer_page,
                section_header=current_header,
                chunk_index=len(chunks),
            )
        )
        buffer_parts = []
        buffer_len = 0
        buffer_page = None

    for page_number, page_text in enumerate(pages, start=1):
        paragraphs = [p.strip() for p in _BLANK_LINE.split(page_text) if p.strip()]
        for paragraph in paragraphs:
            if _is_section_header(paragraph):
                current_header = paragraph
                # Still fall through and buffer this paragraph as content —
                # the heuristic misfires on short real content (e.g. a part
                # number or a stamped "PAID"), and excluding it from
                # `chunk.text` would silently drop it from the embedded,
                # searchable text while only keeping it as metadata.
            # Flush *before* adding a paragraph that would push the buffer
            # over threshold, so a chunk never straddles the boundary —
            # the new paragraph starts the next chunk instead.
            if buffer_parts and buffer_len + len(paragraph) > CHUNK_FLUSH_THRESHOLD:
                flush()
            if buffer_page is None:
                buffer_page = page_number
            buffer_parts.append(paragraph)
            buffer_len += len(paragraph)
        flush()

    return chunks
