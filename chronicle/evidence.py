"""Evidence files: which files are accepted, how they are named and how they are served (ADR 0004).

A file is accepted only if its extension and its content agree. SVG and HTML are rejected on purpose:
browsers run scripts inside them, and evidence is served from the same origin as the application.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import get_args
from urllib.parse import quote

from .models import EvidenceType

MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 500
EVIDENCE_TYPES: tuple[str, ...] = get_args(EvidenceType)

_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".log": "text/plain",
}

_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "application/pdf": (b"%PDF-",),
}


class EvidenceRejected(ValueError):
    """The file is not accepted as evidence. The message is shown to the user."""


@dataclass(frozen=True)
class Blob:
    """A checked file, ready to be stored. `name` is for display only and is never used as a path."""

    name: str
    mime: str
    sha256: str
    size: int
    content: bytes


def display_name(filename: str) -> str:
    """The last path component without control characters, at most MAX_NAME_LENGTH characters."""
    base = re.split(r"[\\/]", filename)[-1]
    cleaned = "".join(char for char in base if char.isprintable()).strip()
    return cleaned[:MAX_NAME_LENGTH] or "evidence"


def check_file(filename: str, data: bytes) -> Blob:
    name = display_name(filename)
    if len(data) > MAX_EVIDENCE_BYTES:
        raise EvidenceRejected(f"'{name}' ist größer als {MAX_EVIDENCE_BYTES // (1024 * 1024)} MiB")

    suffix = PurePosixPath(name).suffix.lower()
    mime = _EXTENSIONS.get(suffix)
    if mime is None:
        raise EvidenceRejected(f"'{name}': Dateiendung nicht erlaubt. Erlaubt sind {', '.join(sorted(_EXTENSIONS))}")

    if mime == "text/plain":
        _require_utf8_text(name, data)
    elif not _has_signature(mime, data):
        raise EvidenceRejected(f"'{name}': Der Inhalt passt nicht zur Endung {suffix}")

    return Blob(name=name, mime=mime, sha256=hashlib.sha256(data).hexdigest(), size=len(data), content=data)


def delivery_headers(name: str, mime: str) -> dict[str, str]:
    """Headers for serving stored evidence: no type guessing, no scripts, images shown inline."""
    disposition = "inline" if mime.startswith("image/") else "attachment"
    return {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(display_name(name), safe='')}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, no-cache",
    }


def _has_signature(mime: str, data: bytes) -> bool:
    if mime == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return any(data.startswith(signature) for signature in _SIGNATURES[mime])


def _require_utf8_text(name: str, data: bytes) -> None:
    if b"\x00" in data:
        raise EvidenceRejected(f"'{name}' enthält Binärdaten und ist keine Textdatei")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceRejected(f"'{name}' ist kein gültiges UTF-8") from exc
