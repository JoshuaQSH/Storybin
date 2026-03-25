import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from zipfile import ZipFile

import pytest
from httpx import ASGITransport, AsyncClient

from app.crawler import BooklistPage, ChapterContent, NovelDetail, NovelMeta, SourceSiteBlockedError
from app.index_store import IndexStore
from app.main import AppState, create_app


class MemoryObjectStorage:
    def __init__(self):
        self.objects = {}

    def put_text(self, key: str, text: str):
        self.objects[key] = text
        return {
            "object_key": key,
            "content_bytes": len(text.encode("utf-8")),
        }

    def iter_text(self, key: str, *, chunk_size: int = 65536):
        text = self.objects[key]
        for index in range(0, len(text), chunk_size):
            yield text[index : index + chunk_size]


class DummyCrawler:
    def __init__(self):
        self.booklist_calls = []
        self.detail_calls = []
        self.chapter_calls = []

    def fetch_booklist_page(self, page: int, *, session=None, category_id=None):
        return self.fetch_booklist_page_result(page, session=session, category_id=category_id).novels

    def fetch_booklist_page_result(self, page: int, *, session=None, category_id=None):
        self.booklist_calls.append((page, category_id))
        if page > 2:
            return BooklistPage(novels=[], total_pages=2)
        if page == 1:
            return BooklistPage(
                novels=[
                    NovelMeta(
                        novel_id="409088",
                        title="臺灣戀曲",
                        author="作者甲",
                        category="臺灣言情",
                        url="https://www.xbanxia.cc/books/409088.html",
                    ),
                    NovelMeta(
                        novel_id="409089",
                        title="臺灣娛樂",
                        author="作者乙",
                        category="現代情感",
                        url="https://www.xbanxia.cc/books/409089.html",
                    ),
                ],
                total_pages=2,
            )
        return BooklistPage(
            novels=[
                NovelMeta(
                    novel_id="409090",
                    title="我們臺灣這些",
                    author="作者丙",
                    category="其他言情",
                    url="https://www.xbanxia.cc/books/409090.html",
                )
            ],
            total_pages=2,
        )

    def fetch_novel_detail(self, novel_url: str, *, session=None):
        self.detail_calls.append(novel_url)
        if novel_url.endswith("409089.html"):
            return NovelDetail(
                novel_id="409089",
                title="臺灣娛樂",
                author="作者乙",
                category="現代情感",
                chapter_urls=["https://www.xbanxia.cc/books/409089/1.html"],
                latest_update="2026-03-14",
            )
        if novel_url.endswith("409090.html"):
            return NovelDetail(
                novel_id="409090",
                title="我們臺灣這些",
                author="作者丙",
                category="其他言情",
                chapter_urls=["https://www.xbanxia.cc/books/409090/1.html"],
                latest_update="2026-03-12",
            )
        return NovelDetail(
            novel_id="409088",
            title="臺灣戀曲",
            author="作者甲",
            category="臺灣言情",
            chapter_urls=[
                "https://www.xbanxia.cc/books/409088/1.html",
                "https://www.xbanxia.cc/books/409088/2.html",
            ],
            latest_update="2026-03-13",
        )

    def fetch_chapter(self, chapter_url: str, *, session=None):
        self.chapter_calls.append(chapter_url)
        title = "第一節" if chapter_url.endswith("/1.html") else "第二節"
        body = "歡迎來到臺灣。"
        return ChapterContent(title=title, body=body)


class BlockedCrawler(DummyCrawler):
    def fetch_booklist_page(self, page: int, *, session=None, category_id=None):
        raise SourceSiteBlockedError("Source site blocked automated access for https://www.xbanxia.cc/list/1_1.html")

    def fetch_booklist_page_result(self, page: int, *, session=None, category_id=None):
        raise SourceSiteBlockedError("Source site blocked automated access for https://www.xbanxia.cc/list/1_1.html")


@asynccontextmanager
async def client_for_state(state: AppState):
    app = create_app(state)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def preload_store(store: IndexStore, crawler: DummyCrawler | None = None):
    store.upsert_novels(
        [
            NovelMeta(
                novel_id="409088",
                title="臺灣戀曲",
                author="作者甲",
                category="臺灣言情",
                url="https://www.xbanxia.cc/books/409088.html",
            ),
            NovelMeta(
                novel_id="409089",
                title="臺灣娛樂",
                author="作者乙",
                category="現代情感",
                url="https://www.xbanxia.cc/books/409089.html",
            ),
        ]
    )
    crawler = crawler or DummyCrawler()
    store.update_novel_detail(crawler.fetch_novel_detail("https://www.xbanxia.cc/books/409088.html"))
    store.update_novel_detail(crawler.fetch_novel_detail("https://www.xbanxia.cc/books/409089.html"))


@pytest.mark.asyncio
async def test_health_check():
    state = AppState(store=IndexStore(":memory:"), crawler_module=DummyCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_search_returns_keyword_and_associative_results():
    store = IndexStore(":memory:")
    preload_store(store)
    state = AppState(store=store, crawler_module=DummyCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        resp = await client.get("/search", params={"q": "台湾"})
        assert resp.status_code == 200
        payload = resp.json()
        assert [item["novel_id"] for item in payload["results"][:2]] == ["409089", "409088"]
        assert payload["results"][0]["match_type"] == "keyword"
        assert payload["index_status"] == "ready"


@pytest.mark.asyncio
async def test_featured_returns_recent_novels():
    store = IndexStore(":memory:")
    preload_store(store)
    state = AppState(store=store, crawler_module=DummyCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        resp = await client.get("/featured")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["results"][0]["novel_id"] == "409089"
        assert payload["results"][0]["latest_update"] == "2026-03-14"


@pytest.mark.asyncio
async def test_download_novel():
    store = IndexStore(":memory:")
    preload_store(store)
    state = AppState(store=store, crawler_module=DummyCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        resp = await client.get("/download", params={"novel_id": "409088"})
        assert resp.status_code == 200
        assert "《台湾恋曲》" in resp.text
        assert "欢迎来到台湾。" in resp.text
        assert "filename*=UTF-8''%E5%8F%B0%E6%B9%BE%E6%81%8B%E6%9B%B2.txt" in resp.headers["content-disposition"]
        assert resp.headers["x-storybin-download-cache"] == "miss"


@pytest.mark.asyncio
async def test_download_novel_epub():
    store = IndexStore(":memory:")
    preload_store(store)
    state = AppState(store=store, crawler_module=DummyCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        await client.get("/download", params={"novel_id": "409088"})
        resp = await client.get("/download/epub", params={"novel_id": "409088"})

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/epub+zip"
        assert "filename*=UTF-8''%E5%8F%B0%E6%B9%BE%E6%81%8B%E6%9B%B2.epub" in resp.headers["content-disposition"]
        with ZipFile(BytesIO(resp.content)) as archive:
            chapter = archive.read("OEBPS/text/chapter-001.xhtml").decode("utf-8")
            assert "欢迎来到台湾。" in chapter


@pytest.mark.asyncio
async def test_download_unknown_novel_returns_404():
    state = AppState(store=IndexStore(":memory:"), crawler_module=DummyCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        resp = await client.get("/download", params={"novel_id": "missing"})
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_missing_novel_id_returns_422():
    state = AppState(store=IndexStore(":memory:"), crawler_module=DummyCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        resp = await client.get("/download")
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_refresh_requires_token():
    state = AppState(store=IndexStore(":memory:"), crawler_module=DummyCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        resp = await client.post("/admin/refresh")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_status_reports_full_index_progress():
    state = AppState(
        store=IndexStore(":memory:"),
        crawler_module=DummyCrawler(),
        auto_start_index_build=True,
        index_max_pages=0,
    )

    async with client_for_state(state) as client:
        for _ in range(20):
            resp = await client.get("/status")
            payload = resp.json()
            if payload["index_complete"]:
                break
            await asyncio.sleep(0)
        assert payload["index_complete"] is True
        assert payload["indexed_count"] == 3
        assert payload["pages_total"] == 2


@pytest.mark.asyncio
async def test_status_reports_cache_limits():
    store = IndexStore(":memory:")
    preload_store(store)
    state = AppState(
        store=store,
        crawler_module=DummyCrawler(),
        auto_start_index_build=False,
        cache_max_novels=20000,
        cache_prune_to_novels=16000,
    )

    async with client_for_state(state) as client:
        resp = await client.get("/status")
        payload = resp.json()
        assert payload["cache_max_novels"] == 20000
        assert payload["cache_prune_to_novels"] == 16000
        assert payload["cache_pruned_total"] == 0
        assert payload["cached_novel_count"] == 0
        assert payload["cache_storage_backend"] == "database"
        assert payload["cache_oldest_indexed_at"] is not None
        assert payload["cache_newest_indexed_at"] is not None
        assert payload["storage_backend"] == "sqlite"


@pytest.mark.asyncio
async def test_search_returns_cached_results_when_source_is_blocked():
    store = IndexStore(":memory:")
    preload_store(store)
    state = AppState(
        store=store,
        crawler_module=BlockedCrawler(),
        auto_start_index_build=False,
    )

    async with client_for_state(state) as client:
        resp = await client.get("/search", params={"q": "台湾"})
        assert resp.status_code == 200
        payload = resp.json()
        assert [item["novel_id"] for item in payload["results"][:2]] == ["409089", "409088"]
        assert payload["indexed_count"] == 2


@pytest.mark.asyncio
async def test_status_reports_source_site_block_cleanly():
    state = AppState(
        store=IndexStore(":memory:"),
        crawler_module=BlockedCrawler(),
        auto_start_index_build=True,
    )

    async with client_for_state(state) as client:
        for _ in range(20):
            resp = await client.get("/status")
            payload = resp.json()
            if payload["index_status"] == "blocked":
                break
            await asyncio.sleep(0)
        assert payload["index_status"] == "blocked"
        assert payload["source_site_blocked"] is True
        assert "Source site blocked automated access" in payload["last_error"]


@pytest.mark.asyncio
async def test_admin_cache_persists_novel_and_download_reuses_cached_text():
    crawler = DummyCrawler()
    store = IndexStore(":memory:")
    preload_store(store, crawler=crawler)
    baseline_detail_calls = len(crawler.detail_calls)
    state = AppState(store=store, crawler_module=crawler, auto_start_index_build=False)

    async with client_for_state(state) as client:
        cache_resp = await client.post(
            "/admin/cache",
            params={"novel_id": "409088"},
            headers={"X-Admin-Token": "dev"},
        )
        assert cache_resp.status_code == 200
        payload = cache_resp.json()
        assert payload["cache_hit"] is False
        assert payload["cached_novel_count"] == 1
        assert payload["chapter_count"] == 2
        assert len(crawler.detail_calls) == baseline_detail_calls + 1
        assert len(crawler.chapter_calls) == 2

        download_resp = await client.get("/download", params={"novel_id": "409088"})
        assert download_resp.status_code == 200
        assert download_resp.headers["x-storybin-download-cache"] == "hit"
        assert "欢迎来到台湾。" in download_resp.text
        assert len(crawler.detail_calls) == baseline_detail_calls + 1
        assert len(crawler.chapter_calls) == 2

        second_cache_resp = await client.post(
            "/admin/cache",
            params={"novel_id": "409088"},
            headers={"X-Admin-Token": "dev"},
        )
        assert second_cache_resp.status_code == 200
        assert second_cache_resp.json()["cache_hit"] is True


@pytest.mark.asyncio
async def test_download_uses_object_storage_when_r2_backend_is_enabled():
    crawler = DummyCrawler()
    store = IndexStore(":memory:")
    preload_store(store, crawler=crawler)
    object_storage = MemoryObjectStorage()
    baseline_detail_calls = len(crawler.detail_calls)
    state = AppState(
        store=store,
        crawler_module=crawler,
        auto_start_index_build=False,
        cache_storage_backend="r2",
        object_storage=object_storage,
    )

    async with client_for_state(state) as client:
        first = await client.get("/download", params={"novel_id": "409088"})
        assert first.status_code == 200
        assert first.headers["x-storybin-download-cache"] == "miss"
        assert "欢迎来到台湾。" in first.text

        cached = store.get_cached_novel("409088")
        assert cached is not None
        assert cached["storage_backend"] == "r2"
        assert cached["content_txt"] == ""
        assert cached["object_key"] in object_storage.objects
        assert cached["content_bytes"] > 0
        assert len(crawler.detail_calls) == baseline_detail_calls + 1
        assert len(crawler.chapter_calls) == 2

        second = await client.get("/download", params={"novel_id": "409088"})
        assert second.status_code == 200
        assert second.headers["x-storybin-download-cache"] == "hit"
        assert "欢迎来到台湾。" in second.text
        assert len(crawler.detail_calls) == baseline_detail_calls + 1
        assert len(crawler.chapter_calls) == 2


@pytest.mark.asyncio
async def test_search_refreshes_documents_after_external_store_update():
    store = IndexStore(":memory:")
    state = AppState(store=store, crawler_module=BlockedCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        store.upsert_novels(
            [
                NovelMeta(
                    novel_id="500001",
                    title="臺灣新書",
                    author="作者丁",
                    category="測試分類",
                    url="https://www.xbanxia.cc/books/500001.html",
                )
            ]
        )
        store.update_novel_detail(
            NovelDetail(
                novel_id="500001",
                title="臺灣新書",
                author="作者丁",
                category="測試分類",
                chapter_urls=[],
                latest_update="2026-03-25",
            )
        )

        resp = await client.get("/search", params={"q": "台湾新书"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["results"]
        assert payload["results"][0]["novel_id"] == "500001"


@pytest.mark.asyncio
async def test_admin_import_cached_novel_makes_search_and_download_available():
    store = IndexStore(":memory:")
    state = AppState(store=store, crawler_module=BlockedCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        import_resp = await client.post(
            "/admin/import-cached",
            headers={"X-Admin-Token": "dev"},
            json={
                "novel_id": "600001",
                "title": "臺灣導入小說",
                "author": "作者戊",
                "category": "測試分類",
                "url": "https://www.xbanxia.cc/books/600001.html",
                "content_txt": "《臺灣導入小說》\n作者：作者戊\n\n第1章 測試\n\n歡迎來到臺灣。\n",
                "chapter_count": 1,
                "latest_update": "2026-03-25",
            },
        )
        assert import_resp.status_code == 200
        assert import_resp.json()["status"] == "imported"

        search_resp = await client.get("/search", params={"q": "台湾导入小说"})
        assert search_resp.status_code == 200
        search_payload = search_resp.json()
        assert search_payload["results"]
        assert search_payload["results"][0]["novel_id"] == "600001"

        download_resp = await client.get("/download", params={"novel_id": "600001"})
        assert download_resp.status_code == 200
        assert download_resp.headers["x-storybin-download-cache"] == "hit"
        assert "欢迎来到台湾。" in download_resp.text


@pytest.mark.asyncio
async def test_contribute_cache_makes_search_and_download_available_without_crawler():
    store = IndexStore(":memory:")
    state = AppState(store=store, crawler_module=BlockedCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        import_resp = await client.post(
            "/contribute/cache",
            json={
                "source_filename": "taiwan-share.txt",
                "novel_url": "https://www.xbanxia.cc/books/700001.html",
                "content_txt": "《臺灣共享小說》\n作者：作者庚\n\n第1章 初見\n\n歡迎來到臺灣。\n",
                "category": "測試分類",
            },
        )
        assert import_resp.status_code == 200
        payload = import_resp.json()
        assert payload["status"] == "imported"
        assert payload["novel_id"] == "700001"
        assert payload["title"] == "台湾共享小说"
        assert payload["txt_download_url"].endswith("/download?novel_id=700001")
        assert payload["epub_download_url"].endswith("/download/epub?novel_id=700001")

        search_resp = await client.get("/search", params={"q": "台湾共享小说"})
        assert search_resp.status_code == 200
        assert search_resp.json()["results"][0]["novel_id"] == "700001"

        download_resp = await client.get("/download", params={"novel_id": "700001"})
        assert download_resp.status_code == 200
        assert download_resp.headers["x-storybin-download-cache"] == "hit"
        assert "欢迎来到台湾。" in download_resp.text

        epub_resp = await client.get("/download/epub", params={"novel_id": "700001"})
        assert epub_resp.status_code == 200
        with ZipFile(BytesIO(epub_resp.content)) as archive:
            chapter = archive.read("OEBPS/text/chapter-001.xhtml").decode("utf-8")
            assert "欢迎来到台湾。" in chapter


@pytest.mark.asyncio
async def test_admin_import_cached_external_registers_existing_r2_object():
    store = IndexStore(":memory:")
    object_storage = MemoryObjectStorage()
    object_key = "novels/600002/external.txt"
    object_storage.objects[object_key] = "《台湾外部导入小说》\n作者：作者己\n\n欢迎来到台湾。\n"
    state = AppState(
        store=store,
        crawler_module=BlockedCrawler(),
        auto_start_index_build=False,
        cache_storage_backend="r2",
        object_storage=object_storage,
    )

    async with client_for_state(state) as client:
        import_resp = await client.post(
            "/admin/import-cached-external",
            headers={"X-Admin-Token": "dev"},
            json={
                "novel_id": "600002",
                "title": "臺灣外部導入小說",
                "author": "作者己",
                "category": "測試分類",
                "url": "https://www.xbanxia.cc/books/600002.html",
                "object_key": object_key,
                "content_bytes": len(object_storage.objects[object_key].encode("utf-8")),
                "content_sha256": "sha256-demo",
                "chapter_count": 1,
                "latest_update": "2026-03-25",
            },
        )
        assert import_resp.status_code == 200
        assert import_resp.json()["status"] == "imported"

        search_resp = await client.get("/search", params={"q": "台湾外部导入小说"})
        assert search_resp.status_code == 200
        assert search_resp.json()["results"][0]["novel_id"] == "600002"

        download_resp = await client.get("/download", params={"novel_id": "600002"})
        assert download_resp.status_code == 200
        assert download_resp.headers["x-storybin-download-cache"] == "hit"
        assert "欢迎来到台湾。" in download_resp.text


@pytest.mark.asyncio
async def test_upload_convert_makes_simplified_txt_and_epub_available():
    state = AppState(store=IndexStore(":memory:"), crawler_module=BlockedCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        upload_resp = await client.post(
            "/convert/upload",
            files={
                "file": (
                    "taiwan-love.txt",
                    "《臺灣戀曲》\n作者：作者甲\n\n第1章 初見\n\n歡迎來到臺灣。\n".encode("utf-8"),
                    "text/plain",
                )
            },
        )
        assert upload_resp.status_code == 200
        payload = upload_resp.json()
        assert payload["status"] == "converted"
        assert payload["title"] == "台湾恋曲"
        assert payload["author"] == "作者甲"
        assert payload["txt_download_url"].endswith(f"/convert/uploaded/{payload['upload_id']}.txt")
        assert payload["epub_download_url"].endswith(f"/convert/uploaded/{payload['upload_id']}.epub")

        txt_resp = await client.get(payload["txt_download_url"])
        assert txt_resp.status_code == 200
        assert "欢迎来到台湾。" in txt_resp.text
        assert "filename*=UTF-8''%E5%8F%B0%E6%B9%BE%E6%81%8B%E6%9B%B2.txt" in txt_resp.headers["content-disposition"]

        epub_resp = await client.get(payload["epub_download_url"])
        assert epub_resp.status_code == 200
        assert epub_resp.headers["content-type"] == "application/epub+zip"
        assert "filename*=UTF-8''%E5%8F%B0%E6%B9%BE%E6%81%8B%E6%9B%B2.epub" in epub_resp.headers["content-disposition"]
        with ZipFile(BytesIO(epub_resp.content)) as archive:
            chapter = archive.read("OEBPS/text/chapter-001.xhtml").decode("utf-8")
            assert "欢迎来到台湾。" in chapter


@pytest.mark.asyncio
async def test_upload_convert_rejects_empty_files():
    state = AppState(store=IndexStore(":memory:"), crawler_module=BlockedCrawler(), auto_start_index_build=False)

    async with client_for_state(state) as client:
        upload_resp = await client.post(
            "/convert/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert upload_resp.status_code == 400
        assert "empty" in upload_resp.json()["detail"].lower()
