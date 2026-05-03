import time
import uuid

_uuid7 = getattr(uuid, "uuid7", None)


def new_id_hex() -> str:
    """Return a chronologically sortable hex id.

    Uses uuid7 on Python 3.14+. Falls back to a millisecond timestamp prefix
    plus uuid4 so listings still sort by creation time on older runtimes.
    """
    if _uuid7 is not None:
        return _uuid7().hex
    ts_ms = time.time_ns() // 1_000_000
    return f"{ts_ms:012x}{uuid.uuid4().hex}"
