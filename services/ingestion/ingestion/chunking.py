"""
Text extraction + section-aware chunking for tenant documents.

Pure Python (no model, no database), so it can be tested on its own:
    python chunking.py stock_document.pdf
"""

from __future__ import annotations

import csv
import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAX_CHARS = 800  # target max size of one chunk body (~200 tokens)

# ---------------------------------------------------------------- extraction


def extract_text(filename: str, data: bytes) -> str:
    """Return plain text from an uploaded file (formats accepted by validators.py)."""
    ext = Path(filename).suffix.lower()
    if ext == '.pdf':
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        # "\f" (page break) between pages, so cleaning.clean_text() can find
        # repeated headers/footers per page. splitlines() treats "\f" as a line
        # break, so chunking below works the same either way.
        return '\f'.join((p.extract_text() or '') for p in reader.pages)
    if ext == '.docx':
        from docx import Document

        doc = Document(io.BytesIO(data))
        return '\n'.join(p.text for p in doc.paragraphs)
    if ext == '.xlsx':
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            lines.append(f'{ws.title}')
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    lines.append(' | '.join(cells))
        return '\n'.join(lines)
    if ext == '.csv':
        text = data.decode('utf-8', errors='replace')
        return '\n'.join(' | '.join(r) for r in csv.reader(io.StringIO(text)) if r)
    if ext == '.txt':
        return data.decode('utf-8', errors='replace')
    raise ValueError(f'Unsupported file type: {ext}')


# ---------------------------------------------------------------- line logic

BULLET = re.compile(r'^[•\-\*▪●]\s*')
NUMBERED = re.compile(r'^\d+(\.\d+)*\.?\s*\S')
# "5. Compliance Codes & Internal Penalties"
H1 = re.compile(r'^(\d+)\.\s+([A-Z][^:]{0,70})$')
H2 = re.compile(r'^(\d+\.\d+)\.?\s+([A-Z][^:]{0,70})$')  # "3.1 Warehouse Staff"
LABEL = re.compile(r'^[A-Z][^.]{0,48}:$')  # "Core Duties:", "Receiving:"
ATTACH = re.compile(r'^[•\-\*▪●]?\s*Penalty\b', re.I)  # belongs to the rule above it
# "Financial Service Code FSC-101"
CODE_GROUP = re.compile(r'((?:[A-Z][\w-]*\s){1,3}Code)\s+[A-Z]{2,4}-\d')
ARTICLE = re.compile(r'\bArticle\s+\d+\b')
CODE = re.compile(r'\b[A-Z]{2,4}-\d{2,4}\b')  # "SCP-101", "FSC-103"


def logical_lines(text: str) -> list[str]:
    """Re-join lines that the PDF wrapped in the middle of a sentence."""
    out: list[str] = []
    for raw in text.splitlines():
        line = ' '.join(raw.split())
        if not line:
            continue
        starts_new = (
            not out
            or BULLET.match(line)
            or NUMBERED.match(line)
            or LABEL.match(line)
            or out[-1].endswith(('.', ':', '?', '!'))
            or len(out[-1]) < 60  # previous line was a short heading
            or out[-1] == '•'
        )
        if out and out[-1] == '•':  # lone bullet followed by its text
            out[-1] = '• ' + line
        elif starts_new:
            out.append(line)
        else:
            out[-1] += ' ' + line
    return out


def is_heading(line: str, pattern: re.Pattern) -> bool:
    return bool(pattern.match(line)) and not line.endswith('.') and len(line) <= 75


# ---------------------------------------------------------------- chunking


@dataclass
class Chunk:
    document_title: str
    section: str
    content: str  # what gets embedded and given to the LLM
    codes: list[str] = field(default_factory=list)


def guess_title(lines: list[str], fallback: str) -> str:
    for line in lines[:10]:
        m = re.search(r'Document Title:\s*(.+?)(\s+—|$)', line)
        if m:
            return m.group(1).strip()
    head = []
    for line in lines[:4]:
        if re.match(r'^(Document ID|Effective Date|Version|Prepared by)\b', line):
            break
        head.append(line)
    return ' '.join(head).strip() or fallback


def chunk_document(text: str, fallback_title: str) -> list[Chunk]:
    lines = logical_lines(text)
    title = guess_title(lines, fallback_title)

    # 1) group lines into sections
    sections: list[tuple[str, list[str]]] = []
    h1 = 'Document information'
    h2 = ''
    body: list[str] = []

    def flush():
        if body:
            sections.append((' > '.join(x for x in (h1, h2) if x), body.copy()))
            body.clear()

    for line in lines:
        group = CODE_GROUP.search(line)
        if group:  # "Sales Service Code SSC-201 — ..."
            flush()
            h1, h2 = group.group(1), ''
            body.append(line)
        elif ARTICLE.search(line) and h1 == 'Document information':
            flush()
            h1, h2 = 'General Articles', ''
            body.append(line)
        elif is_heading(line, H2):
            flush()
            h2 = line
        elif is_heading(line, H1):
            flush()
            h1, h2 = line, ''
        else:
            body.append(line)
    flush()

    # 2) split big sections, keep "Label:" lines with what follows them
    #    and "Penalty: ..." lines with the rule they belong to
    chunks: list[Chunk] = []
    for section, raw_lines in sections:
        sec_lines: list[str] = []
        for line in raw_lines:
            if sec_lines and ATTACH.match(line):
                sec_lines[-1] += '\n' + line
            else:
                sec_lines.append(line)
        groups: list[list[str]] = [[]]
        size = 0
        for line in sec_lines:
            if groups[-1] and size + len(line) > MAX_CHARS:
                carry = [groups[-1].pop()] if LABEL.match(groups[-1][-1]) else []
                groups.append(carry)
                size = sum(len(x) for x in carry)
            groups[-1].append(line)
            size += len(line)
        for g in groups:
            if not g:
                continue
            body_text = '\n'.join(g)
            content = f'[{title} > {section}]\n{body_text}'
            chunks.append(Chunk(title, section, content, sorted(set(CODE.findall(body_text)))))
    return chunks


if __name__ == '__main__':
    for path in sys.argv[1:]:
        p = Path(path)
        chs = chunk_document(extract_text(p.name, p.read_bytes()), p.stem)
        print(f'\n######## {p.name}: {len(chs)} chunks')
        for i, c in enumerate(chs):
            print(f'\n--- chunk {i} ({len(c.content)} chars) codes={c.codes}\n{c.content}')
