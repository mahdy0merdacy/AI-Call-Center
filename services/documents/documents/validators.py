"""Upload validation: allowed file types, size limit, and content sniffing.

The client-supplied Content-Type header and filename are untrusted, so a file is
accepted only if its extension is allowed AND its leading bytes / structure match
that extension. The stored `content_type` is taken from this table, never from
the client.

To allow another type, add a row to ALLOWED_TYPES (and a sniffer if it needs one).
Note that every type accepted here must also be parseable by the later ingestion
stage (spec Section 9, Stage 5), which does not exist yet.
"""

import codecs
import os
import zipfile

from django.conf import settings
from rest_framework import serializers

SNIFF_BYTES = 64 * 1024


def _sniff_pdf(f):
    # The PDF spec allows the "%PDF-" header anywhere in the first 1024 bytes.
    return b'%PDF-' in f.read(1024)


def _make_zip_sniffer(required_member):
    def sniff(f):
        # OOXML files (docx/xlsx) are zip archives with a known main part.
        if not zipfile.is_zipfile(f):
            return False
        f.seek(0)
        with zipfile.ZipFile(f) as zf:
            return required_member in zf.namelist()

    return sniff


def _sniff_utf8_text(f):
    head = f.read(SNIFF_BYTES)
    if b'\x00' in head:  # binary data (also rejects UTF-16/32 text)
        return False
    try:
        # final=False tolerates a multi-byte character cut off at the sniff boundary.
        codecs.getincrementaldecoder('utf-8')().decode(head, final=False)
    except UnicodeDecodeError:
        return False
    return True


# extension -> (canonical content type, content sniffer)
ALLOWED_TYPES = {
    '.pdf': ('application/pdf', _sniff_pdf),
    '.docx': (
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        _make_zip_sniffer('word/document.xml'),
    ),
    '.xlsx': (
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        _make_zip_sniffer('xl/workbook.xml'),
    ),
    '.csv': ('text/csv', _sniff_utf8_text),
    '.txt': ('text/plain', _sniff_utf8_text),
}

MAX_FILENAME_LENGTH = 255  # matches Document.original_filename


def validate_upload(uploaded_file):
    """Validate an uploaded file. Returns (original_filename, content_type).

    Raises serializers.ValidationError (HTTP 400) on any problem.
    """
    original_filename = os.path.basename(uploaded_file.name or '')
    if not original_filename:
        raise serializers.ValidationError('The uploaded file has no filename.')
    if len(original_filename) > MAX_FILENAME_LENGTH:
        raise serializers.ValidationError(
            f'Filename is too long ({len(original_filename)} chars; max {MAX_FILENAME_LENGTH}).'
        )

    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in ALLOWED_TYPES:
        allowed = ', '.join(sorted(ALLOWED_TYPES))
        raise serializers.ValidationError(
            f"Unsupported file type '{ext or '(none)'}'. Allowed types: {allowed}."
        )

    if uploaded_file.size == 0:
        raise serializers.ValidationError('The uploaded file is empty.')
    if uploaded_file.size > settings.MAX_UPLOAD_SIZE_BYTES:
        limit_mb = settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
        raise serializers.ValidationError(f'File is too large (max {limit_mb} MB).')

    content_type, sniffer = ALLOWED_TYPES[ext]
    uploaded_file.seek(0)
    try:
        matches = sniffer(uploaded_file)
    except (zipfile.BadZipFile, OSError):
        matches = False
    finally:
        uploaded_file.seek(0)  # leave the file readable for storage
    if not matches:
        raise serializers.ValidationError(f"File content does not look like a valid '{ext}' file.")

    return original_filename, content_type
