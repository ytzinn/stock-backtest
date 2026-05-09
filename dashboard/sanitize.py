from __future__ import annotations

import re


_PATTERNS = [
    re.compile(r"(?i)(DART_API_KEY|API_KEY|DB_PASSWORD|PASSWORD|TOKEN|SECRET)\s*=\s*([^\s]+)"),
    re.compile(r"(?i)(postgres(?:ql)?://[^:\s]+:)([^@\s]+)(@)"),
    re.compile(r"(?i)(password=)([^;\s]+)"),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
]


def sanitize_text(text: str | None) -> str:
    if not text:
        return ""

    value = str(text)
    for pattern in _PATTERNS:
        if pattern.groups >= 3:
            value = pattern.sub(r"\1***\3", value)
        elif pattern.groups >= 2:
            value = pattern.sub(r"\1=***", value)
        else:
            value = pattern.sub("***", value)
    return value

