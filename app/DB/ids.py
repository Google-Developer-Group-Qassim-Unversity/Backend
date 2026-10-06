"""New row ids: time-ordered UUIDs (version 7, RFC 9562).

The first 48 bits are the Unix time in milliseconds, so ids sort in the order
rows were created - "newest first" can break a timestamp tie by id, the way it
could with auto-increment integers. Within one millisecond the remaining 74
bits count up from a random start, so ids from one worker never go backwards.
"""

import secrets
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_last_rand = 0

_RAND_BITS = 74


def new_id() -> str:
    global _last_ms, _last_rand
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms > _last_ms:
            rand = secrets.randbits(_RAND_BITS - 1)  # headroom to count up within the millisecond
        else:
            ms, rand = _last_ms, _last_rand + 1
        _last_ms, _last_rand = ms, rand
    value = (ms << 80) | (0x7 << 76) | ((rand >> 62) << 64) | (0b10 << 62) | (rand & ((1 << 62) - 1))
    return str(uuid.UUID(int=value))
