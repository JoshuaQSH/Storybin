from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

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
