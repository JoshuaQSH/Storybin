"""Helpers for rendering fetched novels into downloadable text."""

from __future__ import annotations

from typing import Any, Callable

from app.converter import to_simplified
from app.crawler import ChapterContent, NovelDetail


def render_novel(
    detail: NovelDetail,
    fetch_chapter_fn: Callable[[str], ChapterContent],
    *,
    text_transform: Callable[[str], str] = to_simplified,
) -> dict[str, Any]:
    title_sc = text_transform(detail.title)
    author_sc = text_transform(detail.author)
    parts = [f"《{title_sc}》", f"作者：{author_sc}", ""]

    for chapter_number, chapter_url in enumerate(detail.chapter_urls, start=1):
        chapter = fetch_chapter_fn(chapter_url)
        chapter_title = text_transform(chapter.title)
        chapter_body = text_transform(chapter.body)
        parts.extend([f"第{chapter_number}章 {chapter_title}", "", chapter_body, ""])

    return {
        "title_sc": title_sc,
        "content_txt": "\n".join(parts).strip() + "\n",
        "chapter_count": len(detail.chapter_urls),
    }
