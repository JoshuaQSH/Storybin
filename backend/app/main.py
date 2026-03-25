"""FastAPI application for the Banxia downloader."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
import hashlib
import logging
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import config, crawler
from app.converter import to_simplified
from app.crawler import (
    BooklistPage,
    CrawlerHTTPError,
    CrawlerParseError,
    NovelMeta,
    SourceSiteBlockedError,
)
from app.index_store import IndexStore
from app.search import SearchDocument, fuzzy_search
from app.storage import ObjectStorageError, build_object_storage_from_config

logger = logging.getLogger(__name__)


class ImportedCachedNovel(BaseModel):
    novel_id: str
    title: str
    author: str
    category: str
    url: str
    content_txt: str
    chapter_count: int
    latest_update: str | None = None


@dataclass
class AppState:
    store: IndexStore = field(default_factory=lambda: IndexStore(config.DB_PATH, database_url=config.DATABASE_URL))
    crawler_module: Any = crawler
    admin_token: str = config.ADMIN_TOKEN
    cache_storage_backend: str = config.CACHE_STORAGE_BACKEND
    object_storage: Any = field(default_factory=build_object_storage_from_config)
    auto_start_index_build: bool = True
    booklist_category_ids: tuple[int, ...] = field(
        default_factory=lambda: config.BOOKLIST_CATEGORY_IDS
        or (config.DEFAULT_BOOKLIST_CATEGORY_ID,)
    )
    index_max_pages: int = config.INDEX_MAX_PAGES
    featured_limit: int = config.FEATURED_LIMIT
    cache_max_novels: int = config.CACHE_MAX_NOVELS
    cache_prune_to_novels: int = config.CACHE_PRUNE_TO_NOVELS
    index_status: str = "idle"
    search_documents: list[SearchDocument] = field(default_factory=list)
    index_task: asyncio.Task | None = None
    last_error: str | None = None
    index_complete: bool = False
    pages_crawled: int = 0
    pages_total: int | None = None
    source_site_blocked: bool = False
    cache_pruned_total: int = 0

    def __post_init__(self):
        self.enforce_cache_limit()
        self.refresh_search_documents()
        if self.count > 0 and self.index_status == "idle":
            self.index_status = "ready"
        if self.count > 0 and not self.auto_start_index_build:
            self.index_complete = True

    @property
    def count(self) -> int:
        return self.store.count()

    @property
    def cached_novel_count(self) -> int:
        return self.store.cached_novel_count()

    def refresh_search_documents(self):
        self.search_documents = self.store.get_search_documents()

    def enforce_cache_limit(self) -> int:
        deleted = self.store.prune_oldest_novels(
            max_novels=self.cache_max_novels,
            prune_to_novels=self.cache_prune_to_novels,
        )
        if deleted:
            self.cache_pruned_total += deleted
        return deleted


async def build_index_background(state: AppState):
    state.index_status = "building"
    state.index_complete = False
    state.last_error = None
    state.pages_crawled = 0
    state.pages_total = 0
    state.source_site_blocked = False

    try:
        for category_id in state.booklist_category_ids:
            await _crawl_category(state, category_id)
        state.index_status = "ready"
        state.index_complete = True
    except SourceSiteBlockedError as exc:
        state.last_error = str(exc)
        state.index_status = "blocked"
        state.source_site_blocked = True
    except Exception as exc:  # pragma: no cover - exercised in integration only.
        logger.exception("Index build failed")
        state.last_error = str(exc)
        state.index_status = "error"


async def _crawl_category(state: AppState, category_id: int):
    page = 1
    max_pages = state.index_max_pages if state.index_max_pages > 0 else None

    while True:
        page_result = await asyncio.to_thread(
            _fetch_booklist_page_result,
            state.crawler_module,
            page,
            category_id,
        )
        if page == 1:
            _register_total_pages(state, page_result.total_pages, max_pages)
        if not page_result.novels:
            break

        state.store.upsert_novels(page_result.novels)
        await _enrich_latest_updates(state, page_result.novels)
        state.enforce_cache_limit()
        state.refresh_search_documents()
        state.pages_crawled += 1

        page += 1
        if max_pages is not None and page > max_pages:
            break
        if page_result.total_pages is not None and page > page_result.total_pages:
            break


async def _enrich_latest_updates(state: AppState, novels: list[NovelMeta]):
    for novel in novels:
        try:
            detail = await asyncio.to_thread(state.crawler_module.fetch_novel_detail, novel.url)
        except Exception as exc:  # pragma: no cover - network failures are non-deterministic.
            logger.warning("Failed to enrich %s: %s", novel.url, exc)
            continue
        state.store.update_novel_detail(detail, url=novel.url)


def _register_total_pages(state: AppState, total_pages: int | None, max_pages: int | None):
    if total_pages is None:
        state.pages_total = None
        return
    capped_total = min(total_pages, max_pages) if max_pages is not None else total_pages
    if state.pages_total is None:
        return
    state.pages_total += capped_total


def _fetch_booklist_page_result(crawler_module: Any, page: int, category_id: int) -> BooklistPage:
    if hasattr(crawler_module, "fetch_booklist_page_result"):
        return crawler_module.fetch_booklist_page_result(page, category_id=category_id)
    return BooklistPage(
        novels=crawler_module.fetch_booklist_page(page, category_id=category_id),
        total_pages=None,
    )


def ensure_index_build(state: AppState, *, force: bool = False) -> bool:
    if state.index_task and not state.index_task.done():
        return False
    if state.index_complete and not force:
        return False
    if state.source_site_blocked and not force:
        return False
    if force:
        state.source_site_blocked = False
    state.index_status = "building"
    state.index_complete = False
    state.index_task = asyncio.create_task(build_index_background(state))
    return True


def get_state(request: Request) -> AppState:
    return request.app.state.service_state


def _search_results(state: AppState, query: str, limit: int) -> list[dict[str, Any]]:
    results = []
    for match in fuzzy_search(query, state.search_documents, limit=limit):
        novel = state.store.get_novel_by_id(match["novel_id"])
        if novel is None:
            continue
        results.append(
            {
                "novel_id": novel["novel_id"],
                "title": novel["title_sc"],
                "title_tc": novel["title_tc"],
                "author": novel["author_sc"] or to_simplified(novel["author_tc"] or ""),
                "category": novel["category_sc"] or to_simplified(novel["category_tc"] or ""),
                "url": novel["url"],
                "latest_update": novel["latest_update"],
                "score": match["score"],
                "match_type": match["match_type"],
            }
        )
    state.store.touch_novels([result["novel_id"] for result in results])
    return results


def _featured_results(state: AppState, limit: int) -> list[dict[str, Any]]:
    return [
        {
            "novel_id": novel["novel_id"],
            "title": novel["title_sc"],
            "title_tc": novel["title_tc"],
            "author": novel["author_sc"] or to_simplified(novel["author_tc"] or ""),
            "category": novel["category_sc"] or to_simplified(novel["category_tc"] or ""),
            "url": novel["url"],
            "latest_update": novel["latest_update"],
        }
        for novel in state.store.get_recent_novels(limit)
    ]


def _refresh_search_documents_if_needed(state: AppState):
    if len(state.search_documents) != state.count:
        state.refresh_search_documents()


def _require_admin_token(x_admin_token: str | None, state: AppState):
    if x_admin_token != state.admin_token:
        raise HTTPException(status_code=401, detail="Invalid admin token.")


def _download_headers(title: str) -> dict[str, str]:
    encoded_name = quote(f"{title}.txt")
    return {
        "Content-Disposition": (
            'attachment; filename="banxia.txt"; '
            f"filename*=UTF-8''{encoded_name}"
        )
    }


def _render_novel(detail, fetch_chapter_fn) -> dict[str, Any]:
    title_sc = to_simplified(detail.title)
    author_sc = to_simplified(detail.author)
    parts = [f"《{title_sc}》", f"作者：{author_sc}", ""]

    for chapter_number, chapter_url in enumerate(detail.chapter_urls, start=1):
        chapter = fetch_chapter_fn(chapter_url)
        chapter_title = to_simplified(chapter.title)
        chapter_body = to_simplified(chapter.body)
        parts.extend([f"第{chapter_number}章 {chapter_title}", "", chapter_body, ""])

    return {
        "title_sc": title_sc,
        "content_txt": "\n".join(parts).strip() + "\n",
        "chapter_count": len(detail.chapter_urls),
    }


def _stream_text(text: str) -> Any:
    def iterator():
        yield text

    return iterator()


def _stream_cached_novel(state: AppState, cached: dict[str, Any]) -> Any:
    if cached["storage_backend"] == "r2":
        if state.object_storage is None or not cached["object_key"]:
            raise RuntimeError("R2 cache is enabled but the object storage client is unavailable.")
        return state.object_storage.iter_text(cached["object_key"])
    return _stream_text(cached["content_txt"])


def _cache_object_key(novel_id: str, content_sha256: str) -> str:
    return f"{novel_id}/{content_sha256}.txt"


def _persist_cached_novel(state: AppState, novel_id: str, rendered: dict[str, Any]):
    content_txt = rendered["content_txt"]
    content_bytes = len(content_txt.encode("utf-8"))
    content_sha256 = hashlib.sha256(content_txt.encode("utf-8")).hexdigest()

    if state.cache_storage_backend == "r2":
        if state.object_storage is None:
            raise RuntimeError("CACHE_STORAGE_BACKEND is set to r2 but object storage is not configured.")
        uploaded = state.object_storage.put_text(
            _cache_object_key(novel_id, content_sha256),
            content_txt,
        )
        state.store.upsert_cached_novel(
            novel_id=novel_id,
            title_sc=rendered["title_sc"],
            content_txt="",
            storage_backend="r2",
            object_key=str(uploaded["object_key"]),
            content_bytes=int(uploaded["content_bytes"]),
            content_sha256=content_sha256,
            chapter_count=rendered["chapter_count"],
        )
        return

    state.store.upsert_cached_novel(
        novel_id=novel_id,
        title_sc=rendered["title_sc"],
        content_txt=content_txt,
        storage_backend="database",
        object_key=None,
        content_bytes=content_bytes,
        content_sha256=content_sha256,
        chapter_count=rendered["chapter_count"],
    )


def _cache_or_get_novel(state: AppState, novel: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    cached = state.store.get_cached_novel(novel["novel_id"])
    if cached is not None:
        state.store.touch_cached_novel(novel["novel_id"])
        return cached, True

    detail = state.crawler_module.fetch_novel_detail(novel["url"])
    rendered = _render_novel(detail, state.crawler_module.fetch_chapter)
    _persist_cached_novel(state, novel["novel_id"], rendered)
    cached = state.store.get_cached_novel(novel["novel_id"])
    if cached is None:  # pragma: no cover - defensive guard
        raise RuntimeError(f"Failed to cache novel {novel['novel_id']}")
    return cached, False


def create_app(state: AppState | None = None) -> FastAPI:
    service_state = state or AppState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service_state = service_state
        if service_state.auto_start_index_build:
            ensure_index_build(service_state)
        elif service_state.count > 0:
            service_state.index_status = "ready"

        try:
            yield
        finally:
            if service_state.index_task and not service_state.index_task.done():
                service_state.index_task.cancel()
                with suppress(asyncio.CancelledError):
                    await service_state.index_task
            service_state.store.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.ALLOWED_ORIGINS),
        allow_origin_regex=r"https?://localhost(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def health_check():
        return {"status": "ok"}

    @app.get("/status")
    async def get_status(state: AppState = Depends(get_state)):
        cache_stats = state.store.cache_stats()
        return {
            "index_status": state.index_status,
            "indexed_count": state.count,
            "cached_novel_count": state.cached_novel_count,
            "cache_storage_backend": state.cache_storage_backend,
            "index_complete": state.index_complete,
            "pages_crawled": state.pages_crawled,
            "pages_total": state.pages_total,
            "source_site_blocked": state.source_site_blocked,
            "last_error": state.last_error,
            "cache_max_novels": state.cache_max_novels,
            "cache_prune_to_novels": state.cache_prune_to_novels,
            "cache_pruned_total": state.cache_pruned_total,
            "cache_oldest_indexed_at": cache_stats["oldest_indexed_at"],
            "cache_newest_indexed_at": cache_stats["newest_indexed_at"],
            "storage_backend": state.store.storage_backend,
        }

    @app.get("/featured")
    async def featured_novels(
        limit: int = Query(default=config.FEATURED_LIMIT, ge=1, le=20),
        state: AppState = Depends(get_state),
    ):
        _refresh_search_documents_if_needed(state)
        if not state.index_complete:
            ensure_index_build(state)
        return {
            "results": _featured_results(state, limit),
            "index_status": state.index_status,
            "indexed_count": state.count,
        }

    @app.get("/search")
    async def search_novels(
        q: str = Query(default=""),
        limit: int = Query(default=20, ge=1, le=50),
        state: AppState = Depends(get_state),
    ):
        _refresh_search_documents_if_needed(state)
        if not state.index_complete:
            ensure_index_build(state)
        return {
            "results": _search_results(state, q, limit) if q.strip() else [],
            "index_status": state.index_status,
            "indexed_count": state.count,
        }

    @app.get("/download")
    async def download_novel(
        novel_id: str = Query(...),
        state: AppState = Depends(get_state),
    ):
        novel = state.store.get_novel_by_id(novel_id)
        if novel is None:
            raise HTTPException(
                status_code=404,
                detail="Novel not found. Try searching first.",
            )
        state.store.touch_novels([novel_id])
        try:
            cached, cache_hit = _cache_or_get_novel(state, novel)
            stream = _stream_cached_novel(state, cached)
        except (CrawlerHTTPError, CrawlerParseError, ObjectStorageError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return StreamingResponse(
            stream,
            media_type="text/plain; charset=utf-8",
            headers={
                **_download_headers(cached["title_sc"]),
                "X-Storybin-Download-Cache": "hit" if cache_hit else "miss",
            },
        )

    @app.post("/admin/refresh")
    async def refresh_index(
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
        state: AppState = Depends(get_state),
    ):
        _require_admin_token(x_admin_token, state)
        ensure_index_build(state, force=True)
        return {
            "status": state.index_status,
            "indexed_count": state.count,
            "index_complete": state.index_complete,
        }

    @app.post("/admin/cache")
    async def cache_novel(
        novel_id: str = Query(...),
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
        state: AppState = Depends(get_state),
    ):
        _require_admin_token(x_admin_token, state)
        novel = state.store.get_novel_by_id(novel_id)
        if novel is None:
            raise HTTPException(status_code=404, detail="Novel not found. Try searching first.")
        try:
            cached, cache_hit = _cache_or_get_novel(state, novel)
        except (CrawlerHTTPError, CrawlerParseError, ObjectStorageError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "status": "cached",
            "novel_id": novel_id,
            "cache_hit": cache_hit,
            "cached_novel_count": state.cached_novel_count,
            "chapter_count": cached["chapter_count"],
            "title": cached["title_sc"],
        }

    @app.post("/admin/import-cached")
    async def import_cached_novel(
        payload: ImportedCachedNovel,
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
        state: AppState = Depends(get_state),
    ):
        _require_admin_token(x_admin_token, state)
        state.store.upsert_novels(
            [
                NovelMeta(
                    novel_id=payload.novel_id,
                    title=payload.title,
                    author=payload.author,
                    category=payload.category,
                    url=payload.url,
                    latest_update=payload.latest_update,
                )
            ]
        )
        _persist_cached_novel(
            state,
            payload.novel_id,
            {
                "title_sc": to_simplified(payload.title),
                "content_txt": to_simplified(payload.content_txt),
                "chapter_count": payload.chapter_count,
            },
        )
        state.refresh_search_documents()
        cached = state.store.get_cached_novel(payload.novel_id)
        if cached is None:  # pragma: no cover - defensive guard
            raise HTTPException(status_code=500, detail="Imported cache was not persisted.")
        return {
            "status": "imported",
            "novel_id": payload.novel_id,
            "title": cached["title_sc"],
            "chapter_count": cached["chapter_count"],
            "cache_storage_backend": cached["storage_backend"],
        }

    return app


app = create_app()
