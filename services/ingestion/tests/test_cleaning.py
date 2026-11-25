"""clean_text() and drop_tiny(): what is removed, and above all what is KEPT."""

import pytest

from ingestion.chunking import Chunk
from ingestion.cleaning import NO_TEXT_MESSAGE, clean_text, drop_tiny
from ingestion.errors import IngestionError

HEADER = 'Company B - Stock Policy'


def long_page(n: int, body: list[str]) -> str:
    """A realistic page: header on line 1, content, page number as last line."""
    return '\n'.join([HEADER, *body, f'Page {n} of 4'])


def rules(n: int, count: int = 8) -> list[str]:
    return [f'Rule {n}.{i}: stock is counted every {i} days.' for i in range(count)]


def test_nfkc_turns_look_alike_characters_into_normal_ones():
    # "fi" ligature (one character) and full-width digits, as some PDFs produce.
    assert clean_text('\ufb01ne for theft: \uff11\uff10 days pay') == 'fine for theft: 10 days pay'


def test_collapses_spaces_and_drops_empty_lines():
    assert clean_text('Records are kept for  7 \t years.\n\n\n') == 'Records are kept for 7 years.'


def test_removes_repeated_header_and_page_numbers():
    text = '\f'.join(long_page(n, rules(n)) for n in range(1, 5))
    out = clean_text(text)
    assert HEADER not in out
    assert 'Page 2 of 4' not in out
    assert 'Rule 3.5: stock is counted every 5 days.' in out


def test_keeps_penalty_rules_repeated_on_every_page():
    # Regression: an earlier version deleted these, because the same rule sat near
    # the bottom of every page and looked like a footer.
    pages = []
    for n in range(1, 5):
        body = rules(n, count=6 + n)  # pages of different lengths
        body.insert(2, 'Penalty: written warning')  # mid-page, not a bullet
        body.append('\u2022 Penalty: written warning')  # bullet, just above the footer
        pages.append(long_page(n, body))
    out = clean_text('\f'.join(pages))
    assert out.count('Penalty: written warning') == 8
    assert HEADER not in out


def test_short_pages_are_not_trusted_to_find_headers():
    # 3-line pages: every line is "near an edge", so nothing is treated as a header.
    text = '\f'.join(f'{HEADER}\nRecords kept {n} years.\nPage {n} of 3' for n in (1, 2, 3))
    out = clean_text(text)
    assert out.count(HEADER) == 3
    assert 'Page 1 of 3' not in out  # page numbers are still removed


@pytest.mark.parametrize(
    'footer', ['3', 'Page 3', 'page 3 of 12', '3 / 12', '- 3 -', '\u2014 3 \u2014']
)
def test_page_number_formats(footer):
    text = f'Stock records.\n{footer}\fAudits happen yearly.'
    assert clean_text(text) == 'Stock records.\nAudits happen yearly.'


def test_single_page_keeps_a_line_that_is_only_a_number():
    # One page (docx, txt...): "3" may be real content (a quantity), not a page number.
    assert clean_text('Maximum boxes per shelf:\n3') == 'Maximum boxes per shelf:\n3'


@pytest.mark.parametrize('text', ['', '   \n\n', '12\f- 3 -\f', '--- *** ---'])
def test_no_text_raises_a_tenant_readable_error(text):
    with pytest.raises(IngestionError, match='No text found'):
        clean_text(text)
    assert 'scanned PDF' in NO_TEXT_MESSAGE


def test_drop_tiny_removes_lonely_headings_and_keeps_real_chunks():
    tiny = Chunk('Stock Policy', '4. Audits', '[Stock Policy > 4. Audits]\n4.1')
    real = Chunk('Stock Policy', '5. Records', '[Stock Policy > 5. Records]\nKept 10 years.......')
    empty = Chunk('Stock Policy', '6. X', '[Stock Policy > 6. X]')
    assert drop_tiny([tiny, real, empty]) == [real]
