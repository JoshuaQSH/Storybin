"""Backup-source search and fetching helpers for online fallback downloads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from urllib.parse import quote_plus, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from app import crawler
from app.converter import to_simplified
from app.crawler import ChapterContent, CrawlerHTTPError, CrawlerParseError
from app.search import SearchDocument, fuzzy_search

BANX_BASE_URL = "https://www.banx.la"
BANX_SEARCH_URL_TEMPLATE = BANX_BASE_URL + "/modules/article/search.php?searchkey={query}&searchtype=all&page=1"
JINA_BASE_URL = "https://r.jina.ai/http://"
BANX_BOOK_URL_RE = re.compile(r"/book/(?P<book_id>\d+)$")
BANX_CHAPTER_URL_RE = re.compile(r"/chapter/(?P<book_id>\d+)/\d+$")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
NOISE_CHAPTER_LINE_CHARS = set("_．。·…-=~*#：: ")
JINA_SEARCH_RESULT_RE = re.compile(
    r"\d+\.\s+\[!\[.*?\]\(.*?\)\s+##\s+(?P<title>.+?)\]\((?P<url>https?://www\.banx\.la/book/\d+)[^)]*\)(?P<author>[^\n]+)",
    re.DOTALL,
)
JINA_DETAIL_TITLE_RE = re.compile(r"^##\s+(?P<title>.+)$", re.MULTILINE)
JINA_DETAIL_AUTHOR_RE = re.compile(r"^作者︰(?:\[(?P<linked>[^\]]+)\][^(]*\([^)]*\)|(?P<plain>.+))$", re.MULTILINE)
JINA_DETAIL_UPDATE_RE = re.compile(r"^最近更新︰(?P<latest>.+)$", re.MULTILINE)
JINA_DETAIL_CHAPTER_RE = re.compile(
    r"^\*\s+\[(?P<title>[^\]]+)\]\((?P<url>https?://www\.banx\.la/chapter/\d+/\d+)[^)]*\)$",
    re.MULTILINE,
)
JINA_TITLE_RE = re.compile(r"^Title:\s+(?P<title>.+)$", re.MULTILINE)


class BackupSourceError(RuntimeError):
    """Raised when a backup source cannot be used."""


@dataclass(frozen=True, slots=True)
class BackupSearchResult:
    source: str
    source_name: str
    novel_id: str
    title: str
    author: str
    category: str
    url: str
    latest_update: str | None = None
    is_simplified: bool = False


@dataclass(slots=True)
class BackupNovelDetail:
    source: str
    source_name: str
    novel_id: str
    title: str
    author: str
    category: str
    url: str
    chapter_urls: list[str]
    latest_update: str | None = None
    is_simplified: bool = False


@dataclass(frozen=True, slots=True)
class ManualSourceLink:
    label: str
    url: str


def search_backup_sources(
    query: str,
    *,
    limit: int = 10,
    session: requests.Session | None = None,
) -> list[BackupSearchResult]:
    normalized_query = query.strip()
    if not normalized_query:
        return []

    candidates: list[BackupSearchResult] = []
    try:
        candidates.extend(_search_banx(normalized_query, session=session))
    except (BackupSourceError, CrawlerHTTPError, CrawlerParseError):
        pass

    if not candidates:
        try:
            candidates.extend(_search_banx_via_jina(normalized_query, session=session))
        except (BackupSourceError, CrawlerHTTPError, CrawlerParseError):
            pass

    if not candidates:
        return []

    documents = [
        SearchDocument(
            novel_id=result.novel_id,
            title_sc=to_simplified(result.title),
            title_tc=result.title,
            author_sc=to_simplified(result.author),
            author_tc=result.author,
            category_sc=to_simplified(result.category),
            category_tc=result.category,
        )
        for result in candidates
    ]
    ranked = fuzzy_search(normalized_query, documents, limit=limit, score_cutoff=25)
    by_novel_id = {result.novel_id: result for result in candidates}
    return [by_novel_id[match["novel_id"]] for match in ranked if match["novel_id"] in by_novel_id]


def fetch_backup_novel(
    source: str,
    novel_id: str,
    *,
    session: requests.Session | None = None,
) -> BackupNovelDetail:
    normalized_source = source.strip().lower()
    if normalized_source == "banx":
        return _fetch_banx_novel(novel_id, session=session)
    raise BackupSourceError(f"Unsupported backup source: {source}")


def fetch_backup_chapter(
    source: str,
    chapter_url: str,
    *,
    session: requests.Session | None = None,
) -> ChapterContent:
    normalized_source = source.strip().lower()
    if normalized_source == "banx":
        return _fetch_banx_chapter(chapter_url, session=session)
    raise BackupSourceError(f"Unsupported backup source: {source}")


def manual_source_links(query: str) -> list[ManualSourceLink]:
    normalized_query = query.strip()
    banx_search = BANX_SEARCH_URL_TEMPLATE.format(query=quote_plus(normalized_query)) if normalized_query else BANX_BASE_URL
    return [
        ManualSourceLink(label="半夏简体", url=banx_search),
        ManualSourceLink(label="言情小说网", url="https://love.kanunu8.com/"),
        ManualSourceLink(label="努努书坊", url="https://www.kanunu8.com/"),
    ]


def source_label(source: str | None) -> str:
    normalized = (source or "").strip().lower()
    return {
        "banx": "半夏简体",
        "xbanxia": "半夏原站",
        "love_kanunu8": "言情小说网",
        "kanunu8": "努努书坊",
    }.get(normalized, normalized or "未知来源")


def identify_source(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if host.endswith("xbanxia.cc"):
        return "xbanxia"
    if host.endswith("banx.la"):
        return "banx"
    if host == "love.kanunu8.com":
        return "love_kanunu8"
    if host.endswith("kanunu8.com"):
        return "kanunu8"
    return None


def source_is_simplified(source: str | None) -> bool:
    return (source or "").strip().lower() == "banx"


def _search_banx(query: str, *, session: requests.Session | None = None) -> list[BackupSearchResult]:
    search_url = BANX_SEARCH_URL_TEMPLATE.format(query=quote_plus(query))
    soup = _fetch_soup(search_url, session=session)
    results: list[BackupSearchResult] = []

    for item in soup.select("div.pop-books2 li.pop-book2"):
        title = _required_text(item, "h2.pop-tit", "banx search title")
        author = _required_text(item, "span.pop-intro", "banx search author")
        link = _select_one(item, "a[href*='/book/']", "banx search link")
        novel_url = _absolute_url(BANX_BASE_URL, link.get("href"))
        results.append(
            BackupSearchResult(
                source="banx",
                source_name=source_label("banx"),
                novel_id=_banx_prefixed_novel_id(_extract_banx_book_id(novel_url)),
                title=title,
                author=author,
                category="半夏简体",
                url=novel_url,
                is_simplified=True,
            )
        )

    return _dedupe_results(results)


def _fetch_banx_novel(
    prefixed_novel_id: str,
    *,
    session: requests.Session | None = None,
) -> BackupNovelDetail:
    novel_id = _unpack_banx_novel_id(prefixed_novel_id)
    detail_url = f"{BANX_BASE_URL}/book/{novel_id}"
    try:
        soup = _fetch_soup(detail_url, session=session)
        intro = _select_one(soup, "div.book-intro", "banx novel intro")
        title = _required_text(intro, "div.book-describe h1", "banx novel title")
        meta_nodes = intro.select("div.book-describe p")
        author = _extract_meta_value(meta_nodes, "作者", required=False) or "未知"
        latest_update = _extract_meta_value(meta_nodes, "最近更新", required=False)
        chapter_urls = [
            _absolute_url(BANX_BASE_URL, link.get("href"))
            for link in soup.select("div.book-list a")
            if link.get("href")
        ]
        if not chapter_urls:
            raise CrawlerParseError("Could not parse banx novel chapters using selector 'div.book-list a'")

        return BackupNovelDetail(
            source="banx",
            source_name=source_label("banx"),
            novel_id=_banx_prefixed_novel_id(novel_id),
            title=title,
            author=author,
            category="半夏简体",
            url=detail_url,
            chapter_urls=chapter_urls,
            latest_update=latest_update,
            is_simplified=True,
        )
    except (CrawlerHTTPError, CrawlerParseError):
        return _fetch_banx_novel_via_jina(prefixed_novel_id, session=session)


def _fetch_banx_chapter(
    chapter_url: str,
    *,
    session: requests.Session | None = None,
) -> ChapterContent:
    try:
        soup = _fetch_soup(chapter_url, session=session)
        title = _required_text(soup, "h1#nr_title", "banx chapter title")
        body_node = _select_one(soup, "div#nr1", "banx chapter body")
        return ChapterContent(title=title, body=_clean_chapter_body(body_node, title, selector="div#nr1"))
    except (CrawlerHTTPError, CrawlerParseError):
        return _fetch_banx_chapter_via_jina(chapter_url, session=session)


def _fetch_soup(url: str, *, session: requests.Session | None = None) -> BeautifulSoup:
    html = _fetch_text(url, session=session)
    return BeautifulSoup(html, "lxml")


def _fetch_text(url: str, *, session: requests.Session | None = None) -> str:
    return crawler.fetch_html(url, session=session)


def _dedupe_results(results: list[BackupSearchResult]) -> list[BackupSearchResult]:
    seen: set[tuple[str, str]] = set()
    unique: list[BackupSearchResult] = []
    for result in results:
        key = (result.source, result.novel_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


def _extract_banx_book_id(url: str) -> str:
    path = urlparse(url).path
    match = BANX_BOOK_URL_RE.search(path) or BANX_CHAPTER_URL_RE.search(path)
    if not match:
        raise CrawlerParseError(f"Could not extract banx novel id from URL {url!r}")
    return match.group("book_id")


def _banx_prefixed_novel_id(novel_id: str) -> str:
    return f"banx-{novel_id}"


def _unpack_banx_novel_id(prefixed_novel_id: str) -> str:
    normalized = prefixed_novel_id.strip()
    if normalized.startswith("banx-"):
        return normalized.split("-", maxsplit=1)[1]
    if normalized.isdigit():
        return normalized
    fallback = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    raise BackupSourceError(f"Invalid Banx novel id: {prefixed_novel_id} ({fallback})")


def _extract_meta_value(meta_nodes: list[Tag], label: str, *, required: bool = True) -> str | None:
    for node in meta_nodes:
        text = node.get_text(" ", strip=True)
        if text.startswith(f"{label}︰") or text.startswith(f"{label}:"):
            if node.a:
                return node.a.get_text(" ", strip=True)
            return text.split("︰", maxsplit=1)[-1].split(":", maxsplit=1)[-1].strip()
    if required:
        raise CrawlerParseError(f"Could not parse banx metadata label {label!r}")
    return None


def _required_text(node: BeautifulSoup | Tag, selector: str, label: str) -> str:
    element = _select_one(node, selector, label)
    text = element.get_text(" ", strip=True)
    if not text:
        raise CrawlerParseError(f"Parsed empty {label} using selector {selector!r}")
    return text


def _select_one(node: BeautifulSoup | Tag, selector: str, label: str) -> Tag:
    element = node.select_one(selector)
    if element is None:
        raise CrawlerParseError(f"Could not parse {label} using selector {selector!r}")
    return element


def _clean_chapter_body(body_node: Tag, title: str, *, selector: str) -> str:
    text = body_node.get_text("\n", strip=True).replace("\xa0", " ")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not _is_noise_line(line.strip())
    ]
    if lines and lines[0] == title:
        lines = lines[1:]
    body = "\n".join(lines)
    body = MULTI_NEWLINE_RE.sub("\n\n", body)
    if not body:
        raise CrawlerParseError(f"Parsed empty chapter body using selector {selector!r}")
    return body


def _is_noise_line(line: str) -> bool:
    return len(line) >= 8 and all(char in NOISE_CHAPTER_LINE_CHARS for char in line)


def _search_banx_via_jina(query: str, *, session: requests.Session | None = None) -> list[BackupSearchResult]:
    search_url = BANX_SEARCH_URL_TEMPLATE.format(query=quote_plus(query))
    markdown = _fetch_text(_jina_url(search_url), session=session)
    results: list[BackupSearchResult] = []

    for match in JINA_SEARCH_RESULT_RE.finditer(markdown):
        novel_url = match.group("url").strip()
        results.append(
            BackupSearchResult(
                source="banx",
                source_name=source_label("banx"),
                novel_id=_banx_prefixed_novel_id(_extract_banx_book_id(novel_url)),
                title=match.group("title").strip(),
                author=match.group("author").strip(),
                category="半夏简体",
                url=novel_url,
                is_simplified=True,
            )
        )

    return _dedupe_results(results)


def _fetch_banx_novel_via_jina(
    prefixed_novel_id: str,
    *,
    session: requests.Session | None = None,
) -> BackupNovelDetail:
    novel_id = _unpack_banx_novel_id(prefixed_novel_id)
    detail_url = f"{BANX_BASE_URL}/book/{novel_id}"
    markdown = _fetch_text(_jina_url(detail_url), session=session)

    title_match = JINA_DETAIL_TITLE_RE.search(markdown)
    title = title_match.group("title").strip() if title_match else None
    if not title:
        raise CrawlerParseError("Could not parse banx novel title from jina mirror")

    author_match = JINA_DETAIL_AUTHOR_RE.search(markdown)
    author = (
        (author_match.group("linked") or author_match.group("plain")).strip()
        if author_match
        else "未知"
    )
    latest_update_match = JINA_DETAIL_UPDATE_RE.search(markdown)
    latest_update = latest_update_match.group("latest").strip() if latest_update_match else None
    chapter_urls = [match.group("url").strip() for match in JINA_DETAIL_CHAPTER_RE.finditer(markdown)]
    if not chapter_urls:
        raise CrawlerParseError("Could not parse banx novel chapters from jina mirror")

    return BackupNovelDetail(
        source="banx",
        source_name=source_label("banx"),
        novel_id=_banx_prefixed_novel_id(novel_id),
        title=title,
        author=author,
        category="半夏简体",
        url=detail_url,
        chapter_urls=chapter_urls,
        latest_update=latest_update,
        is_simplified=True,
    )


def _fetch_banx_chapter_via_jina(
    chapter_url: str,
    *,
    session: requests.Session | None = None,
) -> ChapterContent:
    markdown = _fetch_text(_jina_url(chapter_url), session=session)
    title = _extract_jina_chapter_title(markdown)
    content_marker = "Markdown Content:"
    if content_marker not in markdown:
        raise CrawlerParseError("Could not parse banx chapter content from jina mirror")
    body = _clean_jina_markdown_body(markdown.split(content_marker, maxsplit=1)[1], title=title)
    return ChapterContent(title=title, body=body)


def _extract_jina_chapter_title(markdown: str) -> str:
    title_match = JINA_TITLE_RE.search(markdown)
    if not title_match:
        raise CrawlerParseError("Could not parse banx chapter title from jina mirror")
    raw_title = title_match.group("title").strip()
    chapter_title = raw_title.split(" ", maxsplit=1)[-1].strip() if " " in raw_title else raw_title
    return chapter_title or raw_title


def _clean_jina_markdown_body(raw_body: str, *, title: str) -> str:
    lines = [line.strip() for line in raw_body.splitlines() if line.strip()]
    if lines and lines[0] == title:
        lines = lines[1:]
    lines = [line for line in lines if not _is_noise_line(line)]
    body = "\n".join(lines).strip()
    body = MULTI_NEWLINE_RE.sub("\n\n", body)
    if not body:
        raise CrawlerParseError("Parsed empty banx chapter body from jina mirror")
    return body


def _jina_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    if not parsed.netloc:
        raise BackupSourceError(f"Cannot build jina mirror URL for {source_url!r}")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{JINA_BASE_URL}{parsed.netloc}{parsed.path}{query}"


def _absolute_url(base_url: str, href: str | None) -> str:
    if not href:
        raise CrawlerParseError("Encountered empty href while parsing backup source results")
    return urljoin(f"{base_url}/", href)
