"""to_uuid(): Mongo ids (32 hex chars, no dashes) -> uuid.UUID for Postgres."""

import uuid

import pytest

from ingestion.cleaning import to_uuid

EXPECTED = uuid.UUID('cfc05ea1-3de6-4b6c-a963-47343cb10651')


@pytest.mark.parametrize(
    'value',
    [
        'cfc05ea13de64b6ca96347343cb10651',  # how Mongo stores it
        'cfc05ea1-3de6-4b6c-a963-47343cb10651',  # dashed form (e.g. in the file path)
        'CFC05EA13DE64B6CA96347343CB10651',  # upper case
        '  cfc05ea13de64b6ca96347343cb10651 ',  # stray spaces
        EXPECTED,  # already a UUID
    ],
)
def test_accepts_every_form_of_the_same_id(value):
    assert to_uuid(value) == EXPECTED


@pytest.mark.parametrize('value', ['', '   ', None, 'xyz', 'cfc05ea1', 12345, 'maison-verte'])
def test_refuses_anything_else(value):
    # A broken id must never be written: the chunk could land under the wrong tenant.
    with pytest.raises(ValueError):
        to_uuid(value)
