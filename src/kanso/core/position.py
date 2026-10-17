"""Fractional indexing: string keys that sort between any two neighbours.

Unlike floats, keys never run out of precision, so items can be inserted anywhere
indefinitely and replicas can pick positions independently. Keys use ASCII digits
so SQLite's default (binary) collation sorts them correctly, and never end in "0"
so there is always room before any key.
"""

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def between(a: str | None, b: str | None) -> str:
    """Return a key strictly between `a` and `b`; `None` means the start/end of the list."""
    if a is not None and b is not None and a >= b:
        raise ValueError(f"{a!r} must sort before {b!r}")
    return _midpoint(a or "", b)


def _midpoint(a: str, b: str | None) -> str:
    if b is not None:
        # Copy the shared prefix, padding `a` with zeros.
        n = 0
        while n < len(b) and (a[n] if n < len(a) else "0") == b[n]:
            n += 1
        if n:
            return b[:n] + _midpoint(a[n:], b[n:])
    lo = DIGITS.index(a[0]) if a else 0
    hi = DIGITS.index(b[0]) if b else len(DIGITS)
    if hi - lo > 1:
        return DIGITS[(lo + hi) // 2]
    # Adjacent first digits: `b[0]` itself works if `b` continues past it, else go deeper after `a[0]`.
    if b and len(b) > 1:
        return b[0]
    return DIGITS[lo] + _midpoint(a[1:], None)
