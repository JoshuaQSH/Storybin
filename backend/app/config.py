"""Configuration for the Banxia downloader backend."""

from __future__ import annotations

import os


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


BASE_URL = os.getenv("BASE_URL", "https://www.xbanxia.cc")
DEFAULT_BOOKLIST_CATEGORY_ID = int(os.getenv("DEFAULT_BOOKLIST_CATEGORY_ID", "1"))
BOOKLIST_CATEGORY_IDS = tuple(
    int(value) for value in _split_csv(os.getenv("BOOKLIST_CATEGORY_IDS", str(DEFAULT_BOOKLIST_CATEGORY_ID)))
)
INDEX_MAX_PAGES = int(os.getenv("INDEX_MAX_PAGES", "0"))
FEATURED_LIMIT = int(os.getenv("FEATURED_LIMIT", "10"))
CACHE_MAX_NOVELS = int(os.getenv("CACHE_MAX_NOVELS", "20000"))
CACHE_PRUNE_TO_NOVELS = int(os.getenv("CACHE_PRUNE_TO_NOVELS", "16000"))
FETCH_BACKENDS = _split_csv(os.getenv("FETCH_BACKENDS", "requests,curl_cffi")) or ("requests",)
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "1.5"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "2.0"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20.0"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "dev")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
ALLOWED_ORIGINS = _split_csv(ALLOWED_ORIGIN) if ALLOWED_ORIGIN != "*" else ("*",)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or None
DB_PATH = os.getenv("DB_PATH", "data/banxia_cache.sqlite3")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
CACHE_STORAGE_BACKEND = os.getenv("CACHE_STORAGE_BACKEND", "database").strip().lower() or "database"
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET = os.getenv("R2_BUCKET", "").strip()
R2_KEY_PREFIX = os.getenv("R2_KEY_PREFIX", "novels").strip().strip("/")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Referer": f"{BASE_URL}/",
}

BOOKLIST_PAGE_URL_TEMPLATE = f"{BASE_URL}/list/{{category_id}}_{{page}}.html"

# TODO: VERIFY selector against the live site in a real browser session.
BOOKLIST_CATEGORY_SELECTOR = "article.post h1.cat-title"
# TODO: VERIFY selector against the live site in a real browser session.
BOOKLIST_PAGE_STATS_SELECTOR = "#pagestats"
# TODO: VERIFY selector against the live site in a real browser session.
BOOKLIST_ITEM_SELECTOR = "div.pop-books2 li.pop-book2"
# TODO: VERIFY selector against the live site in a real browser session.
BOOKLIST_TITLE_SELECTOR = "h2.pop-tit"
# TODO: VERIFY selector against the live site in a real browser session.
BOOKLIST_AUTHOR_SELECTOR = "span.pop-intro"
# TODO: VERIFY selector against the live site in a real browser session.
BOOKLIST_LINK_SELECTOR = "a[href*='/books/']"

# TODO: VERIFY selector against the live site in a real browser session.
NOVEL_INTRO_SELECTOR = "div.book-intro"
# TODO: VERIFY selector against the live site in a real browser session.
NOVEL_TITLE_SELECTOR = "div.book-describe h1"
# TODO: VERIFY selector against the live site in a real browser session.
NOVEL_META_PARAGRAPH_SELECTOR = "div.book-describe p"
# TODO: VERIFY selector against the live site in a real browser session.
NOVEL_CHAPTER_LINK_SELECTOR = "div.book-list a"

# TODO: VERIFY selector against the live site in a real browser session.
CHAPTER_TITLE_SELECTOR = "h1#nr_title"
# TODO: VERIFY selector against the live site in a real browser session.
CHAPTER_BODY_SELECTOR = "div#nr1"
