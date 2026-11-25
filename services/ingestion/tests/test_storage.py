"""read_document_file(): reads only inside MEDIA_ROOT, with clear errors."""

import pytest

from ingestion.errors import IngestionError
from ingestion.storage import read_document_file

REL = 'tenants/cfc05ea1-3de6-4b6c-a963-47343cb10651/5c3351a8-e26a-4d15-86c9-f5960048c462'


@pytest.fixture
def media_root(tmp_path):
    root = tmp_path / 'media'
    (root / REL).mkdir(parents=True)
    (root / REL / 'stock_document.pdf').write_bytes(b'%PDF-1.4 test')
    (tmp_path / 'secret.txt').write_text('outside the uploads folder')
    return root


def test_reads_the_file_and_returns_the_uploaded_name(media_root):
    doc = {'file': f'{REL}/stock_document.pdf', 'original_filename': 'Stock Document.pdf'}
    assert read_document_file(doc, media_root) == ('Stock Document.pdf', b'%PDF-1.4 test')


def test_falls_back_to_the_stored_name(media_root):
    doc = {'file': f'{REL}/stock_document.pdf', 'original_filename': ''}
    assert read_document_file(doc, media_root)[0] == 'stock_document.pdf'


@pytest.mark.parametrize('path', ['../secret.txt', f'{REL}/../../../../secret.txt'])
def test_refuses_paths_outside_media_root(media_root, path):
    with pytest.raises(IngestionError, match='Invalid file path'):
        read_document_file({'file': path}, media_root)


def test_refuses_absolute_paths(media_root, tmp_path):
    with pytest.raises(IngestionError, match='Invalid file path'):
        read_document_file({'file': str(tmp_path / 'secret.txt')}, media_root)


def test_missing_file(media_root):
    with pytest.raises(IngestionError, match='File not found in storage'):
        read_document_file({'file': f'{REL}/gone.pdf'}, media_root)


@pytest.mark.parametrize('doc', [{}, {'file': ''}, {'file': None}])
def test_no_file_attached(media_root, doc):
    with pytest.raises(IngestionError, match='no file attached'):
        read_document_file(doc, media_root)
