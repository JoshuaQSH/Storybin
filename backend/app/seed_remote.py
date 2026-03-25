"""Seed a remote Storybin backend from a machine that can reach the source site."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import requests

from app import config, crawler
from app.rendering import render_novel
from app.storage import NovelObjectStorage, build_r2_storage_from_config


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


def import_external_cached_novel(
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
            f"{backend_url.rstrip('/')}/admin/import-cached-external",
            headers={"X-Admin-Token": admin_token},
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise SeedRemoteError(f"Failed to import external novel {payload['novel_id']}: {exc}") from exc
    finally:
        if own_session:
            session.close()

    if response.status_code >= 400:
        body = response.text.strip()
        raise SeedRemoteError(
            f"Failed to import external novel {payload['novel_id']}: HTTP {response.status_code} {body}"
        )
    return response.json()


def seed_novel_urls(
    *,
    backend_url: str,
    admin_token: str,
    novel_urls: Sequence[str],
    workers: int = 1,
    spool_dir: str | None = None,
    spool_only: bool = False,
    upload_to_r2: bool = False,
    object_storage: NovelObjectStorage | None = None,
    crawler_module: Any = crawler,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if workers > 1:
        return _seed_novel_urls_parallel(
            backend_url=backend_url,
            admin_token=admin_token,
            novel_urls=novel_urls,
            workers=workers,
            spool_dir=spool_dir,
            spool_only=spool_only,
            upload_to_r2=upload_to_r2,
            object_storage=object_storage,
            crawler_module=crawler_module,
        )

    if upload_to_r2 and object_storage is None:
        object_storage = build_r2_storage_from_config()

    imported: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    with requests.Session() as session:
        for novel_url in novel_urls:
            try:
                payload = build_or_load_payload(
                    novel_url,
                    spool_dir=spool_dir,
                    crawler_module=crawler_module,
                )
                if spool_only:
                    response = {
                        "status": "spooled",
                        "novel_id": payload["novel_id"],
                        "title": payload["title"],
                        "chapter_count": payload["chapter_count"],
                    }
                elif upload_to_r2:
                    external_payload = build_external_cached_payload(payload, object_storage=object_storage)
                    response = import_external_cached_novel(
                        backend_url=backend_url,
                        admin_token=admin_token,
                        payload=external_payload,
                        session=session,
                    )
                else:
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
    spool_dir: str | None,
    spool_only: bool,
    upload_to_r2: bool,
    object_storage: NovelObjectStorage | None,
    crawler_module: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if upload_to_r2 and object_storage is None:
        object_storage = build_r2_storage_from_config()

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
                spool_dir=spool_dir,
                spool_only=spool_only,
                upload_to_r2=upload_to_r2,
                object_storage=object_storage,
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
    spool_dir: str | None,
    spool_only: bool,
    upload_to_r2: bool,
    object_storage: NovelObjectStorage | None,
    crawler_module: Any,
) -> tuple[int, dict[str, Any] | None, dict[str, str] | None]:
    try:
        payload = build_or_load_payload(
            novel_url,
            spool_dir=spool_dir,
            crawler_module=crawler_module,
        )
        if spool_only:
            response = {
                "status": "spooled",
                "novel_id": payload["novel_id"],
                "title": payload["title"],
                "chapter_count": payload["chapter_count"],
            }
        elif upload_to_r2:
            if object_storage is None:
                object_storage = build_r2_storage_from_config()
            external_payload = build_external_cached_payload(payload, object_storage=object_storage)
            response = import_external_cached_novel(
                backend_url=backend_url,
                admin_token=admin_token,
                payload=external_payload,
            )
        else:
            response = import_cached_novel(
                backend_url=backend_url,
                admin_token=admin_token,
                payload=payload,
            )
        return index, response, None
    except Exception as exc:
        return index, None, {"novel_url": novel_url, "error": str(exc)}


def build_or_load_payload(
    novel_url: str,
    *,
    spool_dir: str | None,
    crawler_module: Any = crawler,
) -> dict[str, Any]:
    payload_path = payload_path_for_novel_url(novel_url, spool_dir) if spool_dir else None
    if payload_path is not None and payload_path.exists():
        return json.loads(payload_path.read_text(encoding="utf-8"))

    payload = build_import_payload(novel_url, crawler_module=crawler_module)
    if payload_path is not None:
        save_payload(payload_path, payload)
    return payload


def payload_path_for_novel_url(novel_url: str, spool_dir: str | None) -> Path:
    if not spool_dir:
        raise ValueError("spool_dir is required to build a payload path.")
    return Path(spool_dir).expanduser() / f"{crawler._extract_novel_id(novel_url)}.json"


def save_payload(path: Path, payload: dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_external_cached_payload(
    payload: dict[str, Any],
    *,
    object_storage: NovelObjectStorage,
) -> dict[str, Any]:
    content_txt = payload["content_txt"]
    content_sha256 = hashlib.sha256(content_txt.encode("utf-8")).hexdigest()
    uploaded = object_storage.put_text(
        cache_object_key(payload["novel_id"], content_sha256),
        content_txt,
    )
    return {
        "novel_id": payload["novel_id"],
        "title": payload["title"],
        "author": payload["author"],
        "category": payload["category"],
        "url": payload["url"],
        "object_key": str(uploaded["object_key"]),
        "content_bytes": int(uploaded["content_bytes"]),
        "content_sha256": content_sha256,
        "chapter_count": payload["chapter_count"],
        "latest_update": payload.get("latest_update"),
    }


def cache_object_key(novel_id: str, content_sha256: str) -> str:
    return f"{novel_id}/{content_sha256}.txt"


def import_spooled_payloads(
    *,
    backend_url: str,
    admin_token: str,
    spool_dir: str,
    workers: int = 1,
    limit: int | None = None,
    upload_to_r2: bool = False,
    object_storage: NovelObjectStorage | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    payload_paths = sorted(Path(spool_dir).expanduser().glob("*.json"))
    if limit is not None:
        payload_paths = payload_paths[:limit]

    if workers > 1:
        return _import_spooled_payloads_parallel(
            backend_url=backend_url,
            admin_token=admin_token,
            payload_paths=payload_paths,
            workers=workers,
            upload_to_r2=upload_to_r2,
            object_storage=object_storage,
        )

    if upload_to_r2 and object_storage is None:
        object_storage = build_r2_storage_from_config()

    imported: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with requests.Session() as session:
        for path in payload_paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if upload_to_r2:
                    external_payload = build_external_cached_payload(payload, object_storage=object_storage)
                    response = import_external_cached_novel(
                        backend_url=backend_url,
                        admin_token=admin_token,
                        payload=external_payload,
                        session=session,
                    )
                else:
                    response = import_cached_novel(
                        backend_url=backend_url,
                        admin_token=admin_token,
                        payload=payload,
                        session=session,
                    )
                imported.append(response)
            except Exception as exc:
                failures.append({"payload_path": str(path), "error": str(exc)})
    return imported, failures


def _import_spooled_payloads_parallel(
    *,
    backend_url: str,
    admin_token: str,
    payload_paths: Sequence[Path],
    workers: int,
    upload_to_r2: bool,
    object_storage: NovelObjectStorage | None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if upload_to_r2 and object_storage is None:
        object_storage = build_r2_storage_from_config()

    imported_by_index: dict[int, dict[str, Any]] = {}
    failures_by_index: dict[int, dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _import_single_payload_path,
                index,
                path,
                backend_url=backend_url,
                admin_token=admin_token,
                upload_to_r2=upload_to_r2,
                object_storage=object_storage,
            ): index
            for index, path in enumerate(payload_paths)
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


def _import_single_payload_path(
    index: int,
    path: Path,
    *,
    backend_url: str,
    admin_token: str,
    upload_to_r2: bool,
    object_storage: NovelObjectStorage | None,
) -> tuple[int, dict[str, Any] | None, dict[str, str] | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if upload_to_r2:
            if object_storage is None:
                object_storage = build_r2_storage_from_config()
            external_payload = build_external_cached_payload(payload, object_storage=object_storage)
            response = import_external_cached_novel(
                backend_url=backend_url,
                admin_token=admin_token,
                payload=external_payload,
            )
        else:
            response = import_cached_novel(
                backend_url=backend_url,
                admin_token=admin_token,
                payload=payload,
            )
        return index, response, None
    except Exception as exc:
        return index, None, {"payload_path": str(path), "error": str(exc)}


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
    parser.add_argument("--spool-dir", help="Optional local directory for cached JSON payloads.")
    parser.add_argument("--spool-only", action="store_true", help="Crawl and save payloads locally without importing.")
    parser.add_argument("--import-from-spool", help="Import previously saved JSON payloads from this directory.")
    parser.add_argument(
        "--upload-to-r2",
        action="store_true",
        help="Upload novel TXT to R2 from the local machine, then register metadata with Render.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.import_from_spool:
        imported, failures = import_spooled_payloads(
            backend_url=args.backend_url,
            admin_token=args.admin_token,
            spool_dir=args.import_from_spool,
            workers=max(1, args.workers),
            limit=args.limit,
            upload_to_r2=args.upload_to_r2,
        )
        for result in imported:
            print(json.dumps(result, ensure_ascii=False))
        for failure in failures:
            print(json.dumps(failure, ensure_ascii=False))
        return 1 if failures else 0

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
        spool_dir=args.spool_dir,
        spool_only=args.spool_only,
        upload_to_r2=args.upload_to_r2,
    )

    for result in imported:
        print(json.dumps(result, ensure_ascii=False))
    for failure in failures:
        print(json.dumps(failure, ensure_ascii=False))

    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
