from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest
import requests

from app import crawler
from app.crawler import (
    SourceSiteBlockedError,
    crawl_full_novel,
    fetch_booklist_page,
    fetch_booklist_page_result,
    fetch_chapter,
    fetch_novel_detail,
)

FIXTURES = Path(__file__).parent / "fixtures"


def make_mock_response(html: str, status: int = 200):
    response = MagicMock()
    response.status_code = status
    response.text = html
    response.apparent_encoding = "utf-8"
    response.url = "https://www.xbanxia.cc/mock"
    response.raise_for_status = MagicMock()
    return response


def make_mock_session(html: str, status: int = 200):
    response = make_mock_response(html, status=status)
    session = MagicMock()
    session.request.return_value = response
    return session


def test_fetch_booklist_page_returns_list():
    html = (FIXTURES / "booklist_page1.html").read_text(encoding="utf-8")
    session = make_mock_session(html)

    results = fetch_booklist_page(1, session=session)

    assert isinstance(results, list)
    assert len(results) == 2
    assert results[0].title == "鱗鲛無月"
    assert results[0].author == "鎏宴"
    assert results[0].url.startswith("http")


def test_fetch_booklist_page_result_includes_total_pages():
    html = (FIXTURES / "booklist_page1.html").read_text(encoding="utf-8")
    session = make_mock_session(html)

    result = fetch_booklist_page_result(1, session=session)

    assert result.total_pages == 4610
    assert len(result.novels) == 2


def test_fetch_novel_detail_returns_chapters():
    html = (FIXTURES / "novel_page.html").read_text(encoding="utf-8")
    session = make_mock_session(html)

    detail = fetch_novel_detail("https://www.xbanxia.cc/books/409088.html", session=session)

    assert detail.title == "鱗鲛無月"
    assert detail.author == "鎏宴"
    assert detail.category == "耽美同人"
    assert detail.latest_update == "2026-03-13"
    assert len(detail.chapter_urls) == 3


def test_fetch_chapter_returns_body():
    html = (FIXTURES / "chapter_page.html").read_text(encoding="utf-8")
    session = make_mock_session(html)

    chapter = fetch_chapter("https://www.xbanxia.cc/books/409088/71458547.html", session=session)

    assert chapter.title == "朔雲"
    assert "正值酷暑" in chapter.body
    assert len(chapter.body) > 10


def test_fetch_booklist_404_raises():
    session = make_mock_session("", status=404)
    session.request.return_value.raise_for_status.side_effect = Exception("404")

    with pytest.raises(Exception):
        fetch_booklist_page(1, session=session)


def test_fetch_booklist_cloudflare_403_raises_source_blocked():
    response = make_mock_response("<title>Just a moment...</title>", status=403)
    response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
    session = MagicMock()
    session.request.return_value = response

    with pytest.raises(SourceSiteBlockedError):
        fetch_booklist_page(1, session=session)


def test_fetch_booklist_cloudflare_200_falls_back_to_curl_cffi(monkeypatch):
    html = (FIXTURES / "booklist_page1.html").read_text(encoding="utf-8")
    blocked_response = make_mock_response("<title>Just a moment...</title>", status=200)

    monkeypatch.setattr(crawler.config, "FETCH_BACKENDS", ("requests", "curl_cffi"))

    def fake_curl_cffi(url: str, *, apply_rate_limit: bool):
        del url, apply_rate_limit
        return html

    monkeypatch.setattr(requests.Session, "request", lambda self, *args, **kwargs: blocked_response)
    monkeypatch.setattr(crawler, "_request_text_via_curl_cffi", fake_curl_cffi)

    result = fetch_booklist_page(1)

    assert len(result) == 2
    assert result[0].title == "鱗鲛無月"


def test_crawl_full_novel_assembles_text():
    novel_html = (FIXTURES / "novel_page.html").read_text(encoding="utf-8")
    chapter_html = (FIXTURES / "chapter_page.html").read_text(encoding="utf-8")
    session = MagicMock()
    session.request.side_effect = [
        make_mock_response(novel_html),
        make_mock_response(chapter_html),
        make_mock_response(chapter_html.replace("朔雲", "初見", 2)),
        make_mock_response(chapter_html.replace("朔雲", "初雪", 2)),
    ]

    result = crawl_full_novel("https://www.xbanxia.cc/books/409088.html", session=session)

    assert "作者：鎏宴" in result
    assert "類型：耽美同人" in result
    assert "正值酷暑" in result
    assert "初見" in result


def test_fetch_booklist_page_falls_back_to_playwright(monkeypatch):
    html = (FIXTURES / "booklist_page1.html").read_text(encoding="utf-8")

    monkeypatch.setattr(crawler.config, "FETCH_BACKENDS", ("requests", "playwright"))

    def blocked_requests(url: str, *, session, apply_rate_limit: bool):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")

    def fake_playwright(url: str, *, apply_rate_limit: bool):
        return html

    monkeypatch.setattr(crawler, "_request_response", blocked_requests)
    monkeypatch.setattr(crawler, "_request_text_via_playwright", fake_playwright)

    result = fetch_booklist_page(1)

    assert len(result) == 2
    assert result[0].title == "鱗鲛無月"


def test_fetch_booklist_page_falls_back_to_curl_cffi(monkeypatch):
    html = (FIXTURES / "booklist_page1.html").read_text(encoding="utf-8")

    monkeypatch.setattr(crawler.config, "FETCH_BACKENDS", ("requests", "curl_cffi"))

    def blocked_requests(url: str, *, session, apply_rate_limit: bool):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")

    def fake_curl_cffi(url: str, *, apply_rate_limit: bool):
        return html

    monkeypatch.setattr(crawler, "_request_response", blocked_requests)
    monkeypatch.setattr(crawler, "_request_text_via_curl_cffi", fake_curl_cffi)

    result = fetch_booklist_page(1)

    assert len(result) == 2
    assert result[0].title == "鱗鲛無月"


def test_fetch_booklist_page_falls_back_to_windows_chrome(monkeypatch):
    html = (FIXTURES / "booklist_page1.html").read_text(encoding="utf-8")

    monkeypatch.setattr(crawler.config, "FETCH_BACKENDS", ("requests", "windows_chrome"))

    def blocked_requests(url: str, *, session, apply_rate_limit: bool):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")

    def fake_windows_chrome(url: str, *, apply_rate_limit: bool):
        del url, apply_rate_limit
        return html

    monkeypatch.setattr(crawler, "_request_response", blocked_requests)
    monkeypatch.setattr(crawler, "_request_text_via_windows_chrome", fake_windows_chrome)

    result = fetch_booklist_page(1)

    assert len(result) == 2
    assert result[0].title == "鱗鲛無月"


def test_fetch_booklist_page_preserves_source_blocked_error_with_fallback_enabled(monkeypatch):
    monkeypatch.setattr(crawler.config, "FETCH_BACKENDS", ("requests", "playwright"))

    def blocked_requests(url: str, *, session, apply_rate_limit: bool):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")

    def broken_playwright(url: str, *, apply_rate_limit: bool):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")

    monkeypatch.setattr(crawler, "_request_response", blocked_requests)
    monkeypatch.setattr(crawler, "_request_text_via_playwright", broken_playwright)

    with pytest.raises(SourceSiteBlockedError):
        fetch_booklist_page(1)


def test_fetch_booklist_page_preserves_source_blocked_error_with_curl_cffi_fallback_enabled(monkeypatch):
    monkeypatch.setattr(crawler.config, "FETCH_BACKENDS", ("requests", "curl_cffi"))

    def blocked_requests(url: str, *, session, apply_rate_limit: bool):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")

    def blocked_curl_cffi(url: str, *, apply_rate_limit: bool):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")

    monkeypatch.setattr(crawler, "_request_response", blocked_requests)
    monkeypatch.setattr(crawler, "_request_text_via_curl_cffi", blocked_curl_cffi)

    with pytest.raises(SourceSiteBlockedError):
        fetch_booklist_page(1)


def test_fetch_booklist_page_preserves_source_blocked_error_with_windows_chrome_fallback_enabled(monkeypatch):
    monkeypatch.setattr(crawler.config, "FETCH_BACKENDS", ("requests", "windows_chrome"))

    def blocked_requests(url: str, *, session, apply_rate_limit: bool):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")

    def blocked_windows_chrome(url: str, *, apply_rate_limit: bool):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")

    monkeypatch.setattr(crawler, "_request_response", blocked_requests)
    monkeypatch.setattr(crawler, "_request_text_via_windows_chrome", blocked_windows_chrome)

    with pytest.raises(SourceSiteBlockedError):
        fetch_booklist_page(1)


def test_fetch_booklist_page_passes_proxy_config_to_requests(monkeypatch):
    html = (FIXTURES / "booklist_page1.html").read_text(encoding="utf-8")
    session = make_mock_session(html)
    monkeypatch.setattr(crawler.config, "CRAWLER_PROXIES", {"https": "http://proxy.local:8080"})

    fetch_booklist_page(1, session=session)

    _, kwargs = session.request.call_args
    assert kwargs["proxies"] == {"https": "http://proxy.local:8080"}


def test_curl_cffi_fetch_uses_proxy_config(monkeypatch):
    html = "<html><body>ok</body></html>"
    captured = {}

    class FakeResponse:
        status_code = 200
        text = html

    class FakeCurlRequests:
        class RequestsError(Exception):
            pass

        @staticmethod
        def get(url: str, **kwargs):
            del url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(crawler.config, "CRAWLER_PROXIES", {"https": "http://proxy.local:8080"})
    monkeypatch.setitem(__import__("sys").modules, "curl_cffi.requests", FakeCurlRequests)
    monkeypatch.setitem(__import__("sys").modules, "curl_cffi", type("FakeCurlModule", (), {"requests": FakeCurlRequests}))

    assert crawler._request_text_via_curl_cffi("https://example.com", apply_rate_limit=False) == html
    assert captured["proxies"] == {"https": "http://proxy.local:8080"}


def test_windows_chrome_fetch_uses_subprocess(monkeypatch):
    captured = {}
    html = "<html><body>ok</body></html>"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout=html, stderr="")

    monkeypatch.setattr(crawler.config, "WINDOWS_CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    monkeypatch.setattr(crawler.config, "WINDOWS_CHROME_TIMEOUT_SECONDS", 42.0)
    monkeypatch.setattr(crawler.subprocess, "run", fake_run)

    result = crawler._request_text_via_windows_chrome("https://example.com", apply_rate_limit=False)

    assert result == html
    assert captured["cmd"][:3] == ["powershell.exe", "-NoProfile", "-Command"]
    assert "chrome.exe" in captured["cmd"][3]
    assert "--dump-dom" in captured["cmd"][3]
    assert "https://example.com" in captured["cmd"][3]
    assert captured["kwargs"]["timeout"] == 42.0


def test_windows_chrome_fetch_raises_source_blocked_for_cloudflare_html(monkeypatch):
    def fake_run(cmd, **kwargs):
        del cmd, kwargs
        return subprocess.CompletedProcess(
            ["powershell.exe"],
            0,
            stdout="<title>Access denied | www.xbanxia.cc used Cloudflare to restrict access</title>",
            stderr="",
        )

    monkeypatch.setattr(crawler.config, "WINDOWS_CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    monkeypatch.setattr(crawler.subprocess, "run", fake_run)

    with pytest.raises(SourceSiteBlockedError):
        crawler._request_text_via_windows_chrome("https://example.com", apply_rate_limit=False)
