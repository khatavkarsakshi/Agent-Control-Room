"""Input sanitisation helpers.

The API is intentionally tolerant about whitespace but strict about data that
could break logs, JSON storage or SQL LIKE queries. Note that SQL injection is
prevented by SQLAlchemy's parameter binding; the LIKE escaping here only ensures
a user supplied ``%`` is treated literally.
"""

from __future__ import annotations

import re
import unicodedata

# Control characters except tab (\x09), newline (\x0a) and carriage return (\x0d).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_NEWLINE = re.compile(r"\n{4,}")


def sanitize_text(value: str, *, max_length: int | None = None, allow_newlines: bool = True) -> str:
    """Normalise Unicode, strip control characters and enforce a max length."""
    if not isinstance(value, str):
        raise TypeError("sanitize_text expects a string")

    value = unicodedata.normalize("NFC", value)
    value = _CONTROL_CHARS.sub("", value)
    if allow_newlines:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        value = _MULTI_NEWLINE.sub("\n\n\n", value)
    else:
        value = value.replace("\n", " ")
    value = value.strip()

    if max_length is not None and len(value) > max_length:
        raise ValueError(f"value exceeds maximum length of {max_length} characters")
    return value


def sanitize_optional_text(
    value: str | None, *, max_length: int | None = None, allow_newlines: bool = True
) -> str | None:
    if value is None:
        return None
    return sanitize_text(value, max_length=max_length, allow_newlines=allow_newlines)


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so user input is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def safe_filename(value: str, *, fallback: str = "export") -> str:
    """Produce a conservative attachment filename."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return cleaned[:100] or fallback
