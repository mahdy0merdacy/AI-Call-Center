"""Read an uploaded file from the documents service's storage.

Today: the documents service writes uploads to a Docker volume, which this worker
mounts read-only at MEDIA_ROOT (see docker-compose.yml).
Later (Kubernetes): files move to SeaweedFS S3. Only this module changes then;
the rest of the worker only ever calls read_document_file().
"""

from pathlib import Path

from .errors import IngestionError


def read_document_file(doc: dict, media_root: Path) -> tuple[str, bytes]:
    """Return (filename, file bytes) for one document record from Mongo.

    doc["file"] is a path relative to MEDIA_ROOT, written by the documents service:
        tenants/<tenant uuid>/<doc uuid>/stock_document.pdf

    The filename returned is the name the tenant uploaded (original_filename): the
    extractor uses its extension (.pdf, .docx...) and chunking uses it as a fallback
    document title.
    """
    relative = doc.get('file') or ''
    if not relative:
        raise IngestionError('The document has no file attached.')

    root = media_root.resolve()
    # resolve() turns "a/../b" and symlinks into the real absolute path.
    path = (root / relative).resolve()

    # Safety: the path must stay INSIDE MEDIA_ROOT. A value like "../../etc/passwd"
    # or "/etc/passwd" would otherwise let a bad record make us read any file.
    if not path.is_relative_to(root):
        raise IngestionError('Invalid file path.')

    if not path.is_file():
        # Record exists in Mongo but the file is gone (volume reset, manual delete...).
        raise IngestionError('File not found in storage. Please upload it again.')

    filename = doc.get('original_filename') or path.name
    return filename, path.read_bytes()
