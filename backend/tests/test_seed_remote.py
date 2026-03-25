import requests

from app.crawler import BooklistPage, ChapterContent, NovelDetail, NovelMeta
from app.seed_remote import (
    build_import_payload,
    discover_all_novel_urls,
    discover_novel_urls,
    import_cached_novel,
    novel_url_from_id,
    seed_novel_urls,
)


class DummyCrawler:
    def fetch_booklist_page_result(self, page: int, *, category_id: int):
        del category_id
        if page == 1:
            return BooklistPage(
                novels=[
                    NovelMeta(
                        novel_id="410113",
                        title="二十年夏",
                        author="吟稀",
                        category="耽美同人",
                        url="https://www.xbanxia.cc/books/410113.html",
                    ),
                    NovelMeta(
                        novel_id="410182",
                        title="羅青不耐",
                        author="有春知",
                        category="耽美同人",
                        url="https://www.xbanxia.cc/books/410182.html",
                    ),
                ],
                total_pages=2,
            )
        if page == 2:
            return BooklistPage(
                novels=[
                    NovelMeta(
                        novel_id="410199",
                        title="第三本小說",
                        author="作者丙",
                        category="耽美同人",
                        url="https://www.xbanxia.cc/books/410199.html",
                    )
                ],
                total_pages=2,
            )
        return BooklistPage(novels=[], total_pages=2)

    def fetch_novel_detail(self, novel_url: str):
        if novel_url.endswith("missing.html"):
            raise RuntimeError("novel missing")
        if novel_url.endswith("410182.html"):
            return NovelDetail(
                novel_id="410182",
                title="羅青不耐",
                author="有春知",
                category="耽美同人",
                chapter_urls=[
                    "https://www.xbanxia.cc/books/410182/1.html",
                    "https://www.xbanxia.cc/books/410182/2.html",
                ],
                latest_update="2026-03-24",
            )
        return NovelDetail(
            novel_id="410113",
            title="二十年夏",
            author="吟稀",
            category="耽美同人",
            chapter_urls=[
                "https://www.xbanxia.cc/books/410113/1.html",
                "https://www.xbanxia.cc/books/410113/2.html",
            ],
            latest_update="2026-03-24",
        )

    def fetch_chapter(self, chapter_url: str):
        title = "第一章" if chapter_url.endswith("/1.html") else "第二章"
        return ChapterContent(title=title, body="歡迎來到臺灣。")


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls = []

    def post(self, url: str, headers: dict, json: dict, timeout: float):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.responses.pop(0)

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False


def test_novel_url_from_id():
    assert novel_url_from_id("410113").endswith("/books/410113.html")


def test_build_import_payload_uses_rendered_simplified_text():
    payload = build_import_payload(
        "https://www.xbanxia.cc/books/410113.html",
        crawler_module=DummyCrawler(),
    )

    assert payload["novel_id"] == "410113"
    assert payload["chapter_count"] == 2
    assert "《二十年夏》" in payload["content_txt"]
    assert "欢迎来到台湾。" in payload["content_txt"]


def test_discover_novel_urls_respects_limit():
    urls = discover_novel_urls(
        page_start=1,
        page_end=2,
        category_id=1,
        limit=1,
        crawler_module=DummyCrawler(),
    )

    assert urls == ["https://www.xbanxia.cc/books/410113.html"]


def test_discover_novel_urls_supports_parallel_page_fetch():
    urls = discover_novel_urls(
        page_start=1,
        page_end=2,
        category_id=1,
        workers=2,
        crawler_module=DummyCrawler(),
    )

    assert urls == [
        "https://www.xbanxia.cc/books/410113.html",
        "https://www.xbanxia.cc/books/410182.html",
        "https://www.xbanxia.cc/books/410199.html",
    ]


def test_discover_all_novel_urls_uses_total_pages():
    urls = discover_all_novel_urls(category_id=1, crawler_module=DummyCrawler())

    assert len(urls) == 3
    assert urls[-1].endswith("410199.html")


def test_import_cached_novel_posts_expected_payload():
    session = FakeSession(
        [
            FakeResponse(
                200,
                {"status": "imported", "novel_id": "410113", "cache_storage_backend": "r2"},
            )
        ]
    )

    result = import_cached_novel(
        backend_url="https://storybin.onrender.com",
        admin_token="secret",
        payload={"novel_id": "410113"},
        session=session,
    )

    assert result["status"] == "imported"
    assert session.calls[0]["url"] == "https://storybin.onrender.com/admin/import-cached"
    assert session.calls[0]["headers"]["X-Admin-Token"] == "secret"


def test_import_cached_novel_raises_helpful_error():
    session = FakeSession([FakeResponse(502, {}, text='{"detail":"boom"}')])

    try:
        import_cached_novel(
            backend_url="https://storybin.onrender.com",
            admin_token="secret",
            payload={"novel_id": "410113"},
            session=session,
        )
    except Exception as exc:
        assert "HTTP 502" in str(exc)
        assert "410113" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected import_cached_novel to raise")


def test_seed_novel_urls_collects_successes_and_failures(monkeypatch):
    fake_session = FakeSession(
        [
            FakeResponse(200, {"status": "imported", "novel_id": "410113", "cache_storage_backend": "r2"}),
        ]
    )
    monkeypatch.setattr(requests, "Session", lambda: fake_session)

    imported, failures = seed_novel_urls(
        backend_url="https://storybin.onrender.com",
        admin_token="secret",
        novel_urls=[
            "https://www.xbanxia.cc/books/410113.html",
            "https://www.xbanxia.cc/books/missing.html",
        ],
        crawler_module=DummyCrawler(),
    )

    assert imported == [{"status": "imported", "novel_id": "410113", "cache_storage_backend": "r2"}]
    assert len(failures) == 1
    assert failures[0]["novel_url"].endswith("missing.html")


def test_seed_novel_urls_supports_parallel_workers(monkeypatch):
    def fake_import_cached_novel(*, backend_url: str, admin_token: str, payload: dict, session=None, timeout: float = 180.0):
        del backend_url, admin_token, session, timeout
        return {"status": "imported", "novel_id": payload["novel_id"], "cache_storage_backend": "r2"}

    monkeypatch.setattr("app.seed_remote.import_cached_novel", fake_import_cached_novel)

    imported, failures = seed_novel_urls(
        backend_url="https://storybin.onrender.com",
        admin_token="secret",
        novel_urls=[
            "https://www.xbanxia.cc/books/410113.html",
            "https://www.xbanxia.cc/books/410182.html",
        ],
        workers=2,
        crawler_module=DummyCrawler(),
    )

    assert failures == []
    assert [item["novel_id"] for item in imported] == ["410113", "410182"]
