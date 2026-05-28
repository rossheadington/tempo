"""Small, defensive value-coercion helpers for transforms.

Raw API payloads are external data: a field may be absent, ``None``, or an empty
string. These helpers turn "missing or unusable" into ``None`` and otherwise
coerce to the target numeric/text type, so a structured row never carries a
junk value and a transform never raises on an optional field.
"""

from __future__ import annotations

from typing import Any


def _opt_float(value: Any) -> float | None:
    """Coerce to ``float`` or ``None`` (for absent/empty/unparseable values)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: Any) -> int | None:
    """Coerce to ``int`` or ``None``. Floats are truncated toward zero."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _opt_str(value: Any) -> str | None:
    """Return a non-empty ``str`` or ``None``."""
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None
