"""HTTP fetching and HTML parsing for Banxia novel pages."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import re
import subprocess
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from app import config

NOVEL_URL_RE = re.compile(r"/books/(?P<novel_id>\d+)(?:/(?P<chapter_id>\d+))?\.html$")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


class CrawlerHTTPError(RuntimeError):
    """Raised when a request fails after retries."""


class CrawlerParseError(RuntimeError):
    """Raised when the expected HTML structure is missing."""


class SourceSiteBlockedError(CrawlerHTTPError):
    """Raised when the source site blocks automated access."""


@dataclass(slots=True)
class NovelMeta:
    novel_id: str
    title: str
    author: str
    category: str
    url: str
    latest_update: str | None = None


@dataclass(slots=True)
class BooklistPage:
    novels: list[NovelMeta]
    total_pages: int | None


@dataclass(slots=True)
class NovelDetail:
    novel_id: str
    title: str
    author: str
    category: str
    chapter_urls: list[str]
    latest_update: str | None = None


@dataclass(slots=True)
class ChapterContent:
    title: str
    body: str


def fetch_booklist_page(
    page: int,
    *,
    session: requests.Session | None = None,
    category_id: int | None = None,
) -> list[NovelMeta]:
    return fetch_booklist_page_result(
        page,
        session=session,
        category_id=category_id,
    ).novels


def fetch_booklist_page_result(
    page: int,
    *,
    session: requests.Session | None = None,
    category_id: int | None = None,
) -> BooklistPage:
    own_session = session is None
    session = session or requests.Session()
    url = config.BOOKLIST_PAGE_URL_TEMPLATE.format(
        category_id=category_id or config.DEFAULT_BOOKLIST_CATEGORY_ID,
        page=page,
    )

    try:
        soup = _fetch_soup(
            url,
            session=session,
            apply_rate_limit=own_session,
            allow_fallback=own_session,
        )
        return _parse_booklist_soup(soup)
    finally:
        if own_session:
            session.close()


def fetch_html(
    url: str,
    *,
    session: requests.Session | None = None,
    allow_fallback: bool | None = None,
) -> str:
    own_session = session is None
    session = session or requests.Session()

    try:
        return _request_text(
            url,
            session=session,
            apply_rate_limit=own_session,
            allow_fallback=own_session if allow_fallback is None else allow_fallback,
        )
    finally:
        if own_session:
            session.close()


def fetch_novel_detail(
    novel_url: str,
    *,
    session: requests.Session | None = None,
) -> NovelDetail:
    own_session = session is None
    session = session or requests.Session()

    try:
        soup = _fetch_soup(
            novel_url,
            session=session,
            apply_rate_limit=own_session,
            allow_fallback=own_session,
        )
        return _parse_novel_detail_soup(soup, novel_url)
    finally:
        if own_session:
            session.close()


def fetch_chapter(
    chapter_url: str,
    *,
    session: requests.Session | None = None,
) -> ChapterContent:
    own_session = session is None
    session = session or requests.Session()

    try:
        soup = _fetch_soup(
            chapter_url,
            session=session,
            apply_rate_limit=own_session,
            allow_fallback=own_session,
        )
        title = _required_text(soup, config.CHAPTER_TITLE_SELECTOR, "chapter title")
        body_node = _select_one(soup, config.CHAPTER_BODY_SELECTOR, "chapter body")
        body = _clean_chapter_body(body_node, title)
        return ChapterContent(title=title, body=body)
    finally:
        if own_session:
            session.close()


def crawl_full_novel(
    novel_url: str,
    *,
    session: requests.Session | None = None,
) -> str:
    own_session = session is None
    session = session or requests.Session()

    try:
        detail = fetch_novel_detail(novel_url, session=session)
        parts = [detail.title, f"作者：{detail.author}", f"類型：{detail.category}", ""]
        for chapter_url in detail.chapter_urls:
            chapter = fetch_chapter(chapter_url, session=session)
            parts.extend([chapter.title, "", chapter.body, ""])
        return "\n".join(parts).strip() + "\n"
    finally:
        if own_session:
            session.close()


def _fetch_soup(
    url: str,
    *,
    session: requests.Session,
    apply_rate_limit: bool,
    allow_fallback: bool,
) -> BeautifulSoup:
    html = _request_text(
        url,
        session=session,
        apply_rate_limit=apply_rate_limit,
        allow_fallback=allow_fallback,
    )
    return BeautifulSoup(html, "lxml")


def _request_response(
    url: str,
    *,
    session: requests.Session,
    apply_rate_limit: bool,
    method: str = "GET",
    data: dict[str, str] | None = None,
    params: dict[str, str | int] | None = None,
) -> requests.Response:
    last_error: Exception | None = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            if apply_rate_limit and config.RATE_LIMIT_SECONDS > 0:
                time.sleep(config.RATE_LIMIT_SECONDS)
            response = session.request(
                method,
                url,
                headers=config.HEADERS,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
                data=data,
                params=params,
                proxies=config.CRAWLER_PROXIES or None,
            )
            response.raise_for_status()
            apparent_encoding = getattr(response, "apparent_encoding", None)
            if isinstance(apparent_encoding, str) and apparent_encoding:
                response.encoding = apparent_encoding
            return response
        except (requests.exceptions.HTTPError, requests.exceptions.Timeout) as exc:
            last_error = exc
            response = getattr(exc, "response", None)
            if _looks_like_cloudflare_block(response):
                raise SourceSiteBlockedError(
                    f"Source site blocked automated access for {url}"
                ) from exc
            if attempt >= config.MAX_RETRIES:
                raise CrawlerHTTPError(f"Failed to fetch {url}: {exc}") from exc
            if apply_rate_limit:
                time.sleep(config.RETRY_BACKOFF ** (attempt - 1))
        except requests.exceptions.RequestException as exc:
            raise CrawlerHTTPError(f"Failed to fetch {url}: {exc}") from exc

    raise CrawlerHTTPError(f"Failed to fetch {url}: {last_error}") from last_error


def _request_text(
    url: str,
    *,
    session: requests.Session,
    apply_rate_limit: bool,
    allow_fallback: bool,
) -> str:
    backends = config.FETCH_BACKENDS if allow_fallback else ("requests",)
    last_error: Exception | None = None

    for backend in backends:
        try:
            if backend == "requests":
                response = _request_response(
                    url,
                    session=session,
                    apply_rate_limit=apply_rate_limit,
                )
                return response.text
            if backend == "curl_cffi":
                return _request_text_via_curl_cffi(
                    url,
                    apply_rate_limit=apply_rate_limit,
                )
            if backend == "windows_chrome":
                return _request_text_via_windows_chrome(
                    url,
                    apply_rate_limit=apply_rate_limit,
                )
            if backend == "playwright":
                return _request_text_via_playwright(
                    url,
                    apply_rate_limit=apply_rate_limit,
                )
            last_error = CrawlerHTTPError(f"Unsupported fetch backend: {backend}")
        except (CrawlerHTTPError, SourceSiteBlockedError) as exc:
            last_error = exc
            if not allow_fallback:
                raise

    if last_error is None:
        raise CrawlerHTTPError(f"Failed to fetch {url}: no fetch backends configured")
    if isinstance(last_error, SourceSiteBlockedError):
        raise last_error
    raise CrawlerHTTPError(f"Failed to fetch {url}: {last_error}") from last_error


def _request_text_via_playwright(
    url: str,
    *,
    apply_rate_limit: bool,
) -> str:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on runtime installation.
        raise CrawlerHTTPError(
            "Playwright fallback is configured but playwright is not installed."
        ) from exc

    if apply_rate_limit and config.RATE_LIMIT_SECONDS > 0:
        time.sleep(config.RATE_LIMIT_SECONDS)

    try:
        with sync_playwright() as playwright:
            launch_options = {"headless": True}
            proxy_server = config.CRAWLER_HTTPS_PROXY or config.CRAWLER_HTTP_PROXY
            if proxy_server:
                launch_options["proxy"] = {"server": proxy_server}
            browser = playwright.chromium.launch(**launch_options)
            context = browser.new_context(
                user_agent=config.HEADERS["User-Agent"],
                locale="zh-TW",
                extra_http_headers={
                    "Accept": config.HEADERS["Accept"],
                    "Accept-Language": config.HEADERS["Accept-Language"],
                    "Referer": config.HEADERS["Referer"],
                },
            )
            page = context.new_page()
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(config.REQUEST_TIMEOUT_SECONDS * 1000),
            )
            with suppress(PlaywrightError):
                page.wait_for_load_state(
                    "networkidle",
                    timeout=int(config.REQUEST_TIMEOUT_SECONDS * 1000),
                )
            html = page.content()
            context.close()
            browser.close()
    except PlaywrightError as exc:  # pragma: no cover - exercised in live probing only.
        raise CrawlerHTTPError(f"Playwright failed to fetch {url}: {exc}") from exc

    status = response.status if response is not None else 200
    if status >= 400:
        if "Just a moment..." in html or "cloudflare" in html.lower():
            raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")
        raise CrawlerHTTPError(f"Playwright failed to fetch {url}: HTTP {status}")
    if "Just a moment..." in html or "cloudflare" in html.lower():
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")
    return html


def _request_text_via_curl_cffi(
    url: str,
    *,
    apply_rate_limit: bool,
) -> str:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:  # pragma: no cover - depends on runtime installation.
        raise CrawlerHTTPError(
            "curl_cffi fallback is configured but curl-cffi is not installed."
        ) from exc

    if apply_rate_limit and config.RATE_LIMIT_SECONDS > 0:
        time.sleep(config.RATE_LIMIT_SECONDS)

    try:
        response = curl_requests.get(
            url,
            headers=config.HEADERS,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            impersonate="chrome",
            proxies=config.CRAWLER_PROXIES or None,
        )
    except curl_requests.RequestsError as exc:  # pragma: no cover - exercised in live probing only.
        raise CrawlerHTTPError(f"curl_cffi failed to fetch {url}: {exc}") from exc

    html = response.text
    if response.status_code >= 400:
        if response.status_code == 403 or _looks_like_blocked_html(html):
            raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")
        raise CrawlerHTTPError(f"curl_cffi failed to fetch {url}: HTTP {response.status_code}")
    if _looks_like_blocked_html(html):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")
    return html


def _request_text_via_windows_chrome(
    url: str,
    *,
    apply_rate_limit: bool,
) -> str:
    chrome_path = config.WINDOWS_CHROME_PATH.strip()
    if not chrome_path:
        raise CrawlerHTTPError(
            "Windows Chrome fallback is configured but WINDOWS_CHROME_PATH is not available."
        )

    if apply_rate_limit and config.RATE_LIMIT_SECONDS > 0:
        time.sleep(config.RATE_LIMIT_SECONDS)

    powershell_script = " ".join(
        [
            f"& '{_powershell_single_quote(chrome_path)}'",
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--dump-dom",
            f"'{_powershell_single_quote(url)}'",
        ]
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", powershell_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.WINDOWS_CHROME_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CrawlerHTTPError(
            "Windows Chrome fallback requires powershell.exe to be available from this environment."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CrawlerHTTPError(f"Windows Chrome timed out while fetching {url}") from exc

    html = completed.stdout
    stderr = completed.stderr.strip()
    if completed.returncode != 0 and not html:
        raise CrawlerHTTPError(
            f"Windows Chrome failed to fetch {url}: {stderr or f'exit code {completed.returncode}'}"
        )
    if not html.strip():
        raise CrawlerHTTPError(f"Windows Chrome returned empty DOM for {url}")
    if _looks_like_blocked_html(html):
        raise SourceSiteBlockedError(f"Source site blocked automated access for {url}")
    return html


def _powershell_single_quote(value: str) -> str:
    return value.replace("'", "''")


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


def _extract_meta_value(meta_nodes: list[Tag], label: str, *, required: bool = True) -> str | None:
    for node in meta_nodes:
        text = node.get_text(" ", strip=True)
        if text.startswith(f"{label}︰") or text.startswith(f"{label}:"):
            if node.a:
                return node.a.get_text(" ", strip=True)
            return text.split("︰", maxsplit=1)[-1].split(":", maxsplit=1)[-1].strip()
    if required:
        raise CrawlerParseError(
            f"Could not parse novel metadata label {label!r} using selector {config.NOVEL_META_PARAGRAPH_SELECTOR!r}"
        )
    return None


def _parse_novel_detail_soup(soup: BeautifulSoup, novel_url: str) -> NovelDetail:
    intro = _select_one(soup, config.NOVEL_INTRO_SELECTOR, "novel intro")
    title = _required_text(intro, config.NOVEL_TITLE_SELECTOR, "novel title")
    meta_nodes = intro.select(config.NOVEL_META_PARAGRAPH_SELECTOR)
    author = _extract_meta_value(meta_nodes, "作者")
    category = _extract_meta_value(meta_nodes, "類型")
    latest_update = _extract_meta_value(meta_nodes, "最近更新", required=False)
    chapter_urls = [
        _absolute_url(link.get("href"))
        for link in soup.select(config.NOVEL_CHAPTER_LINK_SELECTOR)
        if link.get("href")
    ]
    if not chapter_urls:
        raise CrawlerParseError(
            f"Could not parse novel chapters using selector {config.NOVEL_CHAPTER_LINK_SELECTOR!r}"
        )

    return NovelDetail(
        novel_id=_extract_novel_id(novel_url),
        title=title,
        author=author,
        category=category,
        chapter_urls=chapter_urls,
        latest_update=latest_update,
    )


def _parse_booklist_soup(soup: BeautifulSoup) -> BooklistPage:
    category = _required_text(soup, config.BOOKLIST_CATEGORY_SELECTOR, "booklist category")
    results: list[NovelMeta] = []

    for item in soup.select(config.BOOKLIST_ITEM_SELECTOR):
        title = _required_text(item, config.BOOKLIST_TITLE_SELECTOR, "booklist title")
        author = _required_text(item, config.BOOKLIST_AUTHOR_SELECTOR, "booklist author")
        link = _select_one(item, config.BOOKLIST_LINK_SELECTOR, "booklist link")
        novel_url = _absolute_url(link.get("href"))
        results.append(
            NovelMeta(
                novel_id=_extract_novel_id(novel_url),
                title=title,
                author=author,
                category=category,
                url=novel_url,
            )
        )

    return BooklistPage(
        novels=results,
        total_pages=_extract_total_pages(soup),
    )

def _clean_chapter_body(body_node: Tag, title: str) -> str:
    text = body_node.get_text("\n", strip=True).replace("\xa0", " ")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and lines[0] == title:
        lines = lines[1:]
    body = "\n".join(lines)
    body = MULTI_NEWLINE_RE.sub("\n\n", body)
    if not body:
        raise CrawlerParseError(
            f"Parsed empty chapter body using selector {config.CHAPTER_BODY_SELECTOR!r}"
        )
    return body


def _absolute_url(href: str | None) -> str:
    if not href:
        raise CrawlerParseError("Encountered empty href while parsing crawler results")
    return urljoin(f"{config.BASE_URL}/", href)


def _extract_novel_id(url: str) -> str:
    match = NOVEL_URL_RE.search(urlparse(url).path)
    if not match:
        raise CrawlerParseError(f"Could not extract novel id from URL {url!r}")
    return match.group("novel_id")
def _looks_like_cloudflare_block(response: requests.Response | None) -> bool:
    if response is None or response.status_code != 403:
        return False
    body = response.text or ""
    return _looks_like_blocked_html(body)


def _looks_like_blocked_html(body: str) -> bool:
    normalized = body.lower()
    return any(
        token in normalized
        for token in (
            "just a moment",
            "cloudflare",
            "access denied |",
            "error code 1006",
            "attention required",
        )
    )


def _extract_total_pages(node: BeautifulSoup | Tag) -> int | None:
    stats = node.select_one(config.BOOKLIST_PAGE_STATS_SELECTOR)
    if stats is None:
        return None
    text = stats.get_text(" ", strip=True)
    if "/" not in text:
        return None
    _, total_text = text.split("/", maxsplit=1)
    try:
        return int(total_text)
    except ValueError:
        return None
