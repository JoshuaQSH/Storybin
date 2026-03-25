"""Seed a remote Storybin backend from a machine that can reach the source site."""

from __future__ import annotations

import argparse
import json
from typing import Any, Sequence

import requests

from app import config, crawler
from app.rendering import render_novel


class SeedRemoteError(RuntimeError):
    """Raised when a remote cache import fails."""


def novel_url_from_id(novel_id: str) -> str:
    return f"{config.BASE_URL}/books/{novel_id}.html"


def build_import_payload(
    novel_url: str,
    *,
    crawler_module: Any = crawler,
) -> dict[str, Any]:
    detail = crawler_module.fetch_novel_detail(novel_url)
    rendered = render_novel(detail, crawler_module.fetch_chapter)
    return {
        "novel_id": detail.novel_id,
        "title": detail.title,
        "author": detail.author,
        "category": detail.category,
        "url": novel_url,
        "content_txt": rendered["content_txt"],
        "chapter_count": rendered["chapter_count"],
        "latest_update": detail.latest_update,
    }


def discover_novel_urls(
    *,
    page_start: int,
    page_end: int,
    category_id: int,
    limit: int | None = None,
    crawler_module: Any = crawler,
) -> list[str]:
    urls: list[str] = []

    for page in range(page_start, page_end + 1):
        result = crawler_module.fetch_booklist_page_result(page, category_id=category_id)
        if not result.novels:
            break

        for novel in result.novels:
            urls.append(novel.url)
            if limit is not None and len(urls) >= limit:
                return urls

        if result.total_pages is not None and page >= result.total_pages:
            break

    return urls


def import_cached_novel(
    *,
    backend_url: str,
    admin_token: str,
    payload: dict[str, Any],
    session: requests.Session | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    own_session = session is None
    session = session or requests.Session()

    try:
        response = session.post(
            f"{backend_url.rstrip('/')}/admin/import-cached",
            headers={"X-Admin-Token": admin_token},
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise SeedRemoteError(f"Failed to import novel {payload['novel_id']}: {exc}") from exc
    finally:
        if own_session:
            session.close()

    if response.status_code >= 400:
        body = response.text.strip()
        raise SeedRemoteError(
            f"Failed to import novel {payload['novel_id']}: HTTP {response.status_code} {body}"
        )
    return response.json()


def seed_novel_urls(
    *,
    backend_url: str,
    admin_token: str,
    novel_urls: Sequence[str],
    crawler_module: Any = crawler,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    imported: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    with requests.Session() as session:
        for novel_url in novel_urls:
            try:
                payload = build_import_payload(novel_url, crawler_module=crawler_module)
                response = import_cached_novel(
                    backend_url=backend_url,
                    admin_token=admin_token,
                    payload=payload,
                    session=session,
                )
                imported.append(response)
            except Exception as exc:
                failures.append({"novel_url": novel_url, "error": str(exc)})

    return imported, failures


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", required=True, help="Remote Storybin base URL.")
    parser.add_argument(
        "--admin-token",
        default=config.ADMIN_TOKEN,
        help="Remote X-Admin-Token value. Defaults to local ADMIN_TOKEN env.",
    )
    parser.add_argument("--novel-id", action="append", default=[], help="Novel ID to crawl and import.")
    parser.add_argument("--novel-url", action="append", default=[], help="Novel URL to crawl and import.")
    parser.add_argument("--category-id", type=int, default=config.DEFAULT_BOOKLIST_CATEGORY_ID)
    parser.add_argument("--page-start", type=int)
    parser.add_argument("--page-end", type=int)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    novel_urls = [*args.novel_url, *(novel_url_from_id(novel_id) for novel_id in args.novel_id)]

    if not novel_urls:
        if args.page_start is None or args.page_end is None:
            raise SystemExit("Provide --novel-id/--novel-url or both --page-start and --page-end.")
        novel_urls = discover_novel_urls(
            page_start=args.page_start,
            page_end=args.page_end,
            category_id=args.category_id,
            limit=args.limit,
        )

    if args.limit is not None:
        novel_urls = novel_urls[: args.limit]

    imported, failures = seed_novel_urls(
        backend_url=args.backend_url,
        admin_token=args.admin_token,
        novel_urls=novel_urls,
    )

    for result in imported:
        print(json.dumps(result, ensure_ascii=False))
    for failure in failures:
        print(json.dumps(failure, ensure_ascii=False))

    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
