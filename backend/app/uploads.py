"""Helpers for uploaded TXT conversion workflows."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from app.converter import to_simplified

TITLE_LINE_RE = re.compile(r"^《(?P<title>.+?)》$")
TITLE_LABEL_RE = re.compile(r"^(?:书名|書名|标题|標題|title)\s*[:：]\s*(?P<title>.+)$", re.IGNORECASE)
AUTHOR_LINE_RE = re.compile(r"^作者\s*[:：]\s*(?P<author>.+)$")


class UploadedTextDecodeError(ValueError):
    """Raised when an uploaded text file cannot be decoded."""


@dataclass(slots=True)
class ConvertedUpload:
    source_filename: str
    title_tc: str
    title_sc: str
    author_tc: str
    author_sc: str
    content_txt: str
    content_bytes: int
    content_sha256: str


def convert_uploaded_txt(source_filename: str, raw_bytes: bytes) -> ConvertedUpload:
    text_tc = decode_uploaded_text(raw_bytes)
    text_sc = to_simplified(text_tc).strip()
    if not text_sc:
        raise UploadedTextDecodeError("Uploaded TXT is empty after decoding.")

    title_tc, author_tc = extract_title_author(text_tc, source_filename)
    title_sc, author_sc = extract_title_author(text_sc, source_filename)
    normalized_content = f"{text_sc}\n"
    content_bytes = len(normalized_content.encode("utf-8"))
    content_sha256 = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()

    return ConvertedUpload(
        source_filename=source_filename,
        title_tc=title_tc,
        title_sc=title_sc,
        author_tc=author_tc,
        author_sc=author_sc,
        content_txt=normalized_content,
        content_bytes=content_bytes,
        content_sha256=content_sha256,
    )


def decode_uploaded_text(raw_bytes: bytes) -> str:
    if not raw_bytes:
        raise UploadedTextDecodeError("Uploaded TXT is empty.")

    encodings = ("utf-8-sig", "utf-8", "utf-16", "gb18030", "big5")
    for encoding in encodings:
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UploadedTextDecodeError(
        "Unable to decode uploaded TXT. Please upload UTF-8, UTF-16, GB18030, or Big5 text."
    )


def extract_title_author(text: str, source_filename: str) -> tuple[str, str]:
    non_empty_lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = _fallback_title(source_filename)
    author = "未知"

    for line in non_empty_lines[:20]:
        title_match = TITLE_LINE_RE.match(line) or TITLE_LABEL_RE.match(line)
        if title_match:
            title = title_match.group("title").strip()
            break
    for line in non_empty_lines[:20]:
        author_match = AUTHOR_LINE_RE.match(line)
        if author_match:
            author = author_match.group("author").strip()
            break

    return title, author


def _fallback_title(source_filename: str) -> str:
    stem = Path(source_filename or "uploaded").stem.strip()
    return stem or "未命名小说"
