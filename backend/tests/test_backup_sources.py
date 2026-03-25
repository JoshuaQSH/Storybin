from pathlib import Path
from unittest.mock import MagicMock

from app.backup_sources import (
    fetch_backup_chapter,
    fetch_backup_novel,
    identify_source,
    manual_source_links,
    search_backup_sources,
    source_is_simplified,
    source_label,
)

FIXTURES = Path(__file__).parent / "fixtures"


def make_session_by_url(html_by_url: dict[str, str]):
    session = MagicMock()

    def request(_method, url, **_kwargs):
        response = MagicMock()
        response.status_code = 200
        response.text = html_by_url[url]
        response.apparent_encoding = "utf-8"
        response.raise_for_status = MagicMock()
        return response

    session.request.side_effect = request
    return session


def test_search_backup_sources_returns_ranked_banx_results():
    html = (FIXTURES / "banx_search_page.html").read_text(encoding="utf-8")
    session = make_session_by_url(
        {
            "https://www.banx.la/modules/article/search.php?searchkey=%E5%8F%B0%E6%B9%BE&searchtype=all&page=1": html
        }
    )

    results = search_backup_sources("台湾", session=session)

    assert [item.novel_id for item in results[:3]] == ["banx-55183", "banx-56920", "banx-62830"]
    assert results[0].source == "banx"
    assert results[0].is_simplified is True
    assert results[0].url == "https://www.banx.la/book/55183"


def test_fetch_backup_novel_returns_chapters_and_metadata():
    html = (FIXTURES / "banx_novel_page.html").read_text(encoding="utf-8")
    session = make_session_by_url({"https://www.banx.la/book/55183": html})

    detail = fetch_backup_novel("banx", "banx-55183", session=session)

    assert detail.novel_id == "banx-55183"
    assert detail.title == "台湾甜心"
    assert detail.author == "贝佳"
    assert detail.latest_update == "2023-04-01"
    assert detail.is_simplified is True
    assert detail.chapter_urls == [
        "https://www.banx.la/chapter/55183/12290047",
        "https://www.banx.la/chapter/55183/12290054",
    ]


def test_fetch_backup_chapter_extracts_clean_body():
    html = (FIXTURES / "banx_chapter_page.html").read_text(encoding="utf-8")
    session = make_session_by_url({"https://www.banx.la/chapter/55183/12290047": html})

    chapter = fetch_backup_chapter("banx", "https://www.banx.la/chapter/55183/12290047", session=session)

    assert chapter.title == "第一章"
    assert chapter.body == "欢迎来到台湾。\n这里已经是简体版本。"


def test_manual_source_links_include_backup_sites():
    links = manual_source_links("台湾恋曲")

    assert links[0].url.startswith("https://www.banx.la/modules/article/search.php?")
    assert any(link.url == "https://love.kanunu8.com/" for link in links)
    assert any(link.url == "https://www.kanunu8.com/" for link in links)


def test_source_helpers_identify_backup_sites():
    assert identify_source("https://www.banx.la/book/55183") == "banx"
    assert identify_source("https://www.xbanxia.cc/books/409088.html") == "xbanxia"
    assert source_label("banx") == "半夏简体"
    assert source_is_simplified("banx") is True
