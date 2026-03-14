"""Traditional Chinese to Simplified Chinese conversion helpers."""

from __future__ import annotations

import opencc

_converter = opencc.OpenCC("t2s")


def to_simplified(text: str) -> str:
    """Convert Traditional Chinese text to Simplified Chinese."""

    return _converter.convert(text)
