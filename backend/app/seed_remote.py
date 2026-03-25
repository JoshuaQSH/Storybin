"""Seed a remote Storybin backend from a machine that can reach the source site."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    workers: int = 1,
    crawler_module: Any = crawler,
) -> list[str]:
    if page_end < page_start:
        return []

    first_result = crawler_module.fetch_booklist_page_result(page_start, category_id=category_id)
    effective_end = min(page_end, first_result.total_pages) if first_result.total_pages is not None else page_end
    page_results = {page_start: first_result}

    remaining_pages = list(range(page_start + 1, effective_end + 1))
    if workers > 1 and remaining_pages:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_page = {
                executor.submit(crawler_module.fetch_booklist_page_result, page, category_id=category_id): page
                for page in remaining_pages
            }
            for future in as_completed(future_to_page):
                page_results[future_to_page[future]] = future.result()
    else:
        for page in remaining_pages:
            page_results[page] = crawler_module.fetch_booklist_page_result(page, category_id=category_id)

    urls: list[str] = []
    for page in range(page_start, effective_end + 1):
        result = page_results.get(page)
        if result is None or not result.novels:
            break
        for novel in result.novels:
            urls.append(novel.url)
            if limit is not None and len(urls) >= limit:
                return urls

    return urls


def discover_all_novel_urls(
    *,
    category_id: int,
    limit: int | None = None,
    workers: int = 1,
    crawler_module: Any = crawler,
) -> list[str]:
    first_page = crawler_module.fetch_booklist_page_result(1, category_id=category_id)
    total_pages = first_page.total_pages or 1
    return discover_novel_urls(
        page_start=1,
        page_end=total_pages,
        category_id=category_id,
        limit=limit,
        workers=workers,
        crawler_module=crawler_module,
    )


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
    workers: int = 1,
    crawler_module: Any = crawler,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if workers > 1:
        return _seed_novel_urls_parallel(
            backend_url=backend_url,
            admin_token=admin_token,
            novel_urls=novel_urls,
            workers=workers,
            crawler_module=crawler_module,
        )

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


def _seed_novel_urls_parallel(
    *,
    backend_url: str,
    admin_token: str,
    novel_urls: Sequence[str],
    workers: int,
    crawler_module: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    imported_by_index: dict[int, dict[str, Any]] = {}
    failures_by_index: dict[int, dict[str, str]] = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _seed_single_url,
                index,
                novel_url,
                backend_url=backend_url,
                admin_token=admin_token,
                crawler_module=crawler_module,
            ): index
            for index, novel_url in enumerate(novel_urls)
        }
        for future in as_completed(futures):
            index, imported, failure = future.result()
            if imported is not None:
                imported_by_index[index] = imported
            if failure is not None:
                failures_by_index[index] = failure

    imported = [imported_by_index[index] for index in sorted(imported_by_index)]
    failures = [failures_by_index[index] for index in sorted(failures_by_index)]
    return imported, failures


def _seed_single_url(
    index: int,
    novel_url: str,
    *,
    backend_url: str,
    admin_token: str,
    crawler_module: Any,
) -> tuple[int, dict[str, Any] | None, dict[str, str] | None]:
    try:
        payload = build_import_payload(novel_url, crawler_module=crawler_module)
        response = import_cached_novel(
            backend_url=backend_url,
            admin_token=admin_token,
            payload=payload,
        )
        return index, response, None
    except Exception as exc:
        return index, None, {"novel_url": novel_url, "error": str(exc)}


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
    parser.add_argument("--all-pages", action="store_true", help="Auto-discover every list page for the category.")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent workers for discovery/import.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    novel_urls = [*args.novel_url, *(novel_url_from_id(novel_id) for novel_id in args.novel_id)]

    if not novel_urls:
        if args.all_pages:
            novel_urls = discover_all_novel_urls(
                category_id=args.category_id,
                limit=args.limit,
                workers=max(1, args.workers),
            )
        else:
            if args.page_start is None or args.page_end is None:
                raise SystemExit("Provide --novel-id/--novel-url, use --all-pages, or give both --page-start and --page-end.")
            novel_urls = discover_novel_urls(
                page_start=args.page_start,
                page_end=args.page_end,
                category_id=args.category_id,
                limit=args.limit,
                workers=max(1, args.workers),
            )

    if args.limit is not None:
        novel_urls = novel_urls[: args.limit]

    imported, failures = seed_novel_urls(
        backend_url=args.backend_url,
        admin_token=args.admin_token,
        novel_urls=novel_urls,
        workers=max(1, args.workers),
    )

    for result in imported:
        print(json.dumps(result, ensure_ascii=False))
    for failure in failures:
        print(json.dumps(failure, ensure_ascii=False))

    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
