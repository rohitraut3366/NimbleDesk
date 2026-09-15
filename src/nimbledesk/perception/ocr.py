from __future__ import annotations

import csv
import io
import shutil
import subprocess
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from nimbledesk.protocol.models import Rectangle


class OcrMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    bounds: Rectangle
    confidence: float = Field(ge=0, le=1)


class OcrProvider(Protocol):
    @property
    def available(self) -> bool: ...

    def locate(self, image: bytes, query: str, exact: bool) -> tuple[OcrMatch, ...]: ...


class TesseractOcrProvider:
    @property
    def available(self) -> bool:
        return shutil.which("tesseract") is not None

    def locate(self, image: bytes, query: str, exact: bool) -> tuple[OcrMatch, ...]:
        executable = shutil.which("tesseract")
        if executable is None:
            return ()
        completed = subprocess.run(
            [executable, "stdin", "stdout", "tsv"],
            input=image,
            capture_output=True,
            check=False,
            timeout=20,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                completed.stderr.decode(errors="replace").strip() or "Tesseract OCR failed"
            )
        return _matches(completed.stdout.decode(errors="replace"), query, exact)


def _matches(tsv: str, query: str, exact: bool) -> tuple[OcrMatch, ...]:
    normalized_query = " ".join(query.casefold().split())
    lines: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        key = (
            str(row.get("page_num", "")),
            str(row.get("block_num", "")),
            str(row.get("par_num", "")),
            str(row.get("line_num", "")),
        )
        lines.setdefault(key, []).append(row)
    matches: list[OcrMatch] = []
    for words in lines.values():
        text = " ".join((word.get("text") or "").strip() for word in words).strip()
        normalized = " ".join(text.casefold().split())
        matched = normalized == normalized_query if exact else normalized_query in normalized
        if not matched:
            continue
        confidence = min(float(word.get("conf") or 0) for word in words) / 100
        left = min(int(word["left"]) for word in words)
        top = min(int(word["top"]) for word in words)
        right = max(int(word["left"]) + int(word["width"]) for word in words)
        bottom = max(int(word["top"]) + int(word["height"]) for word in words)
        matches.append(
            OcrMatch(
                text=text,
                bounds=Rectangle(left=left, top=top, width=right - left, height=bottom - top),
                confidence=max(0, confidence),
            )
        )
    return tuple(sorted(matches, key=lambda match: match.confidence, reverse=True))
