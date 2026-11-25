"""Text cleaning between extraction and chunking, plus id conversion.

Pure functions: no database, no model, no files. Easy to unit test.

    extract_text()  ->  clean_text()  ->  chunk_document()  ->  drop_tiny()
     (chunking.py)       (here)            (chunking.py)         (here)
"""

import re
import unicodedata
import uuid
from collections import Counter

from .chunking import BULLET, Chunk
from .errors import IngestionError

NO_TEXT_MESSAGE = 'No text found in the document (scanned PDF?).'

# Header/footer detection. A real header or footer:
#   - sits in the first or last EDGE_LINES lines of the page,
#   - at the SAME position on most pages (e.g. always line 1, or always the last),
#   - with the same text (case and digits ignored, so "Page 2" == "Page 3"),
#   - and is not a bullet. Rules such as "• Penalty: written warning" can repeat on
#     many pages, even near the bottom, so bullet lines are never removed.
EDGE_LINES = 3
# Removed if found at the same edge position on at least this share of pages...
REPEAT_SHARE = 0.6
# ...and only for documents with at least this many pages (on 2 pages, a line
# repeated twice is just as likely to be real content).
MIN_PAGES_FOR_REPEATS = 3
# Only pages at least this long are used to DECIDE what is a header/footer. On a
# 4-line page every line is "near an edge", so a real rule repeated on each page
# would look like a header. Keeping real content wins over removing noise.
MIN_LINES_PER_PAGE = 2 * EDGE_LINES + 1

# "3", "Page 3", "page 3 of 12", "3 / 12", "- 3 -", and the same with en/em dashes
# (written \u2013 and \u2014 so the source only contains plain characters).
PAGE_NUMBER = re.compile(
    r'^(?:page\s*)?\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?$'
    r'|^[-\u2013\u2014]\s*\d{1,4}\s*[-\u2013\u2014]$',
    re.IGNORECASE,
)

# Minimum size of a chunk's body (text without the "[Title > Section]" line).
MIN_CHUNK_CHARS = 20


def _clean_line(line: str) -> str:
    """NFKC + collapse every run of spaces/tabs into one space."""
    # NFKC turns look-alike characters into their normal form:
    #   the "fi" ligature (one character) -> "f" + "i",
    #   full-width digits (as in some PDFs) -> "10 years",
    #   non-breaking space -> normal space.
    # Without it, "fine" written with the ligature in a PDF and "fine" in a
    # question would not match.
    line = unicodedata.normalize('NFKC', line)
    return ' '.join(line.split())


def _repeat_key(line: str) -> str:
    """Text key used to spot repeated headers/footers: case and digits ignored.

    "Company B — Stock Policy — Page 2" and "... Page 3" get the same key.
    """
    return re.sub(r'\d+', '#', line.lower())


def _edge_slots(page: list[str]) -> list[tuple[int, tuple[str, int, str]]]:
    """(line index, slot) for every non-bullet line near the top or bottom edge.

    A slot is (side, position from that edge, text key), e.g.
    ("top", 0, "company b — stock policy") or ("bottom", 0, "page # of #").
    """
    slots = []
    for i, line in enumerate(page):
        if BULLET.match(line):
            continue  # rules are never headers/footers
        from_bottom = len(page) - 1 - i
        if i < EDGE_LINES:
            slots.append((i, ('top', i, _repeat_key(line))))
        if from_bottom < EDGE_LINES:
            slots.append((i, ('bottom', from_bottom, _repeat_key(line))))
    return slots


def clean_text(text: str) -> str:
    """Clean extracted text. Raises IngestionError if no real text is left.

    Pages are separated by "\\f" (see chunking.extract_text); other formats
    (docx, csv...) come as a single page.
    """
    pages = [[_clean_line(x) for x in page.splitlines()] for page in text.split('\f')]
    pages = [[x for x in page if x] for page in pages]  # drop empty lines
    multi_page = len(pages) >= 2

    # 1) Find header/footer slots: same text at the same edge position on most
    #    (long) pages.
    repeated: set[tuple[str, int, str]] = set()
    long_pages = [page for page in pages if len(page) >= MIN_LINES_PER_PAGE]
    if len(long_pages) >= MIN_PAGES_FOR_REPEATS:
        counts: Counter[tuple[str, int, str]] = Counter()
        for page in long_pages:
            counts.update({slot for _, slot in _edge_slots(page)})  # once per page
        repeated = {slot for slot, n in counts.items() if n / len(long_pages) >= REPEAT_SHARE}

    # 2) Rebuild the text without headers/footers and without page numbers.
    kept: list[str] = []
    for page in pages:
        drop = {i for i, slot in _edge_slots(page) if slot in repeated}
        for i, line in enumerate(page):
            at_edge = i < EDGE_LINES or len(page) - 1 - i < EDGE_LINES
            if i in drop:
                continue  # header/footer
            if at_edge and multi_page and PAGE_NUMBER.match(line):
                continue  # page number
            kept.append(line)

    cleaned = '\n'.join(kept)
    # Only symbols/numbers left (or nothing): nothing worth searching.
    if not any(ch.isalpha() for ch in cleaned):
        raise IngestionError(NO_TEXT_MESSAGE)
    return cleaned


def drop_tiny(chunks: list[Chunk], min_chars: int = MIN_CHUNK_CHARS) -> list[Chunk]:
    """Remove chunks whose body is too short to answer anything.

    chunk.content is "[Title > Section]\\nbody". Example of a chunk dropped:
    "[Stock Policy > 4. Audits]\\n4.1" (a heading left alone).
    """
    kept = []
    for chunk in chunks:
        body = chunk.content.split('\n', 1)[1] if '\n' in chunk.content else ''
        if len(body.strip()) >= min_chars:
            kept.append(chunk)
    return kept


def to_uuid(value: object) -> uuid.UUID:
    """Mongo id -> uuid.UUID, for the uuid columns of document_chunks.

    Mongo (django-mongodb-backend) stores ids as 32 hex chars without dashes:
        "cfc05ea13de64b6ca96347343cb10651" -> UUID('cfc05ea1-3de6-4b6c-a963-47343cb10651')
    The dashed form is accepted too. Anything else raises ValueError: a broken id
    must never be written, or a chunk could end up under the wrong tenant.
    """
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'not a UUID: {value!r}')
    return uuid.UUID(value.strip())  # raises ValueError if malformed
