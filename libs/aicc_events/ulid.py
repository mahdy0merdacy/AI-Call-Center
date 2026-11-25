"""ULIDs: 128-bit IDs that sort by creation time.

48 bits of milliseconds since the epoch, then 80 random bits, written as 26
Crockford base32 characters. Used for event IDs and call IDs: unique without
coordination, and roughly time-ordered, which keeps database index inserts at
the end of the B-tree and makes IDs readable when debugging. Never used to
ORDER events across machines: the time part is a wall clock.
"""

from __future__ import annotations

import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(now_ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    n = (ms << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))
