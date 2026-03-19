# AGENT.md — Agent Operating Instructions

## Who This File Is For

This file tells an AI coding agent (Claude, Cursor, Copilot Workspace, etc.) **exactly how to behave** when building, modifying, or debugging the Banxia Downloader project. Read this file in full before taking any action.

---

## Absolute Rules (Never Violate)

1. **Never claim something works without running a test.** After writing any code, immediately write and run a test. If you cannot test it (e.g. network is blocked), say so explicitly and explain what test must be run manually.

2. **Work modularly. One module at a time.** Complete a module, show test results, then stop and wait for confirmation before proceeding to the next module. Do not write 500 lines and hope it all works.

3. **Iterate and fix errors yourself.** Run the code. Observe the output. Fix problems. Do not hand errors back to the user and say "you might need to fix X". Fix X yourself, re-run, confirm it passes.

4. **Be explicit about unknowns.** If you are uncertain about a CSS selector, a URL pattern, or any site-specific behaviour, say so. Do not guess silently. State the assumption and ask the user to verify it against a real browser session.

5. **Use `uv` for environment management.** All Python commands use `uv run`. The `pyproject.toml` is the single source of truth for dependencies. Never use bare `pip install` for runtime dependencies.

6. **Every module has a corresponding `pytest` test.** Do not move to the next module until the current module's tests pass.

7. **All tests must pass before calling a module done.** If a test fails, fix the code (or the test if the test is wrong), re-run, and confirm.

---

## Module Build Order

Build in this exact order. Do not skip ahead.

```
Module 0: Project scaffold (pyproject.toml, directory structure, uv setup)
Module 1: converter.py  (Traditional→Simplified, no network needed, easy to test)
Module 2: crawler.py    (HTTP fetching + HTML parsing, tested with fixtures)
Module 3: index_store.py (SQLite operations, in-memory DB for tests)
Module 4: search.py     (fuzzy search over title list)
Module 5: main.py       (FastAPI routes, tested with TestClient + mocks)
Module 6: frontend/index.html (static HTML UI)
Module 7: Dockerfile    (build + run tests inside container)
Module 8: CI workflow   (.github/workflows/ci.yml)
Module 9: REPO_STRUCTURE.md
```

---

## Module 0: Project Scaffold

### What to do

```bash
mkdir -p banxia-downloader/backend/app
mkdir -p banxia-downloader/backend/tests/fixtures
mkdir -p banxia-downloader/frontend
mkdir -p banxia-downloader/.github/workflows

cd banxia-downloader/backend
uv init --no-workspace
# Edit pyproject.toml with dependencies from SKILL.md
uv sync --dev
```

### Verify

```bash
uv run python -c "import fastapi, requests, bs4, opencc, rapidfuzz; print('OK')"
```

Expected output: `OK`

If any import fails → fix `pyproject.toml`, re-run `uv sync`, re-verify.

---

## Module 1: `converter.py`

### What to build

`backend/app/converter.py`:
- One function: `to_simplified(text: str) -> str`
- Uses `opencc.OpenCC('t2s')`
- Converter instance created once at module level (not on every call)

### Test to write

`backend/tests/test_converter.py`:

```python
from app.converter import to_simplified

def test_basic_conversion():
    assert to_simplified("歡迎") == "欢迎"

def test_taiwan():
    assert to_simplified("臺灣") == "台湾"

def test_software():
    assert to_simplified("軟體") == "软件" or to_simplified("軟體") == "软体"
    # Note: 軟體 may map to either depending on opencc config — accept both

def test_already_simplified_is_unchanged():
    # Simplified Chinese should pass through without corruption
    text = "你好世界"
    assert to_simplified(text) == text

def test_empty_string():
    assert to_simplified("") == ""

def test_mixed_text():
    result = to_simplified("Chapter 1: 歡迎來到臺灣")
    assert "欢迎" in result
    assert "台湾" in result
```

### Run

```bash
cd backend && uv run pytest tests/test_converter.py -v
```

All 6 tests must pass. Fix any failures before proceeding.

---

## Module 2: `crawler.py`

### ⚠️ Critical constraint

The live site `xbanxia.cc` is likely **not reachable from CI or from many development environments** due to network restrictions or anti-scraping. All automated tests MUST use fixture HTML files. Never make live network calls in tests.

### Fixture HTML files required

Before writing the crawler, you need real HTML from the site. The user must provide these, OR you build the crawler with placeholder selectors and mark them `# TODO: verify selector against real browser`.

The fixtures must go in `backend/tests/fixtures/`:
- `booklist_page1.html` — one page of the novel list
- `novel_page.html` — one novel's detail page (with chapter list)
- `chapter_page.html` — one chapter's content page

**If fixtures are not yet available:** Create stub fixtures with minimal valid HTML that matches your best guess of the structure, mark them `# STUB - must be replaced with real HTML`, and have the tests pass against the stubs. Document clearly that the stubs must be replaced.

### What to build

`backend/app/crawler.py` — implement these functions using `requests` + `BeautifulSoup`:

```python
from dataclasses import dataclass

@dataclass
class NovelMeta:
    novel_id: str
    title: str
    author: str
    category: str
    url: str

@dataclass
class NovelDetail:
    novel_id: str
    title: str
    author: str
    chapter_urls: list[str]  # ordered

@dataclass
class ChapterContent:
    title: str
    body: str

def fetch_booklist_page(page: int, *, session=None) -> list[NovelMeta]: ...
def fetch_novel_detail(novel_url: str, *, session=None) -> NovelDetail: ...
def fetch_chapter(chapter_url: str, *, session=None) -> ChapterContent: ...
def crawl_full_novel(novel_url: str) -> str: ...  # assembled text, TC not yet converted
```

The `session` parameter is injectable so tests can pass a `unittest.mock.MagicMock` or `responses`-mocked session.

### Test to write

`backend/tests/test_crawler.py` — all tests use fixture HTML, no live network:

```python
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from app.crawler import fetch_booklist_page, fetch_novel_detail, fetch_chapter

FIXTURES = Path(__file__).parent / "fixtures"

def make_mock_session(html: str, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = html
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session

def test_fetch_booklist_page_returns_list():
    html = (FIXTURES / "booklist_page1.html").read_text(encoding="utf-8")
    session = make_mock_session(html)
    results = fetch_booklist_page(1, session=session)
    assert isinstance(results, list)
    assert len(results) > 0
    assert results[0].title != ""
    assert results[0].url.startswith("http")

def test_fetch_novel_detail_returns_chapters():
    html = (FIXTURES / "novel_page.html").read_text(encoding="utf-8")
    session = make_mock_session(html)
    detail = fetch_novel_detail("https://www.xbanxia.cc/12345/", session=session)
    assert detail.title != ""
    assert len(detail.chapter_urls) > 0

def test_fetch_chapter_returns_body():
    html = (FIXTURES / "chapter_page.html").read_text(encoding="utf-8")
    session = make_mock_session(html)
    chapter = fetch_chapter("https://www.xbanxia.cc/12345/1.html", session=session)
    assert chapter.title != ""
    assert len(chapter.body) > 10

def test_fetch_booklist_404_raises():
    session = make_mock_session("", status=404)
    session.get.return_value.raise_for_status.side_effect = Exception("404")
    with pytest.raises(Exception):
        fetch_booklist_page(1, session=session)
```

### Run

```bash
cd backend && uv run pytest tests/test_crawler.py -v
```

All tests must pass. If fixture files are stubs, tests may pass trivially — that is acceptable at this stage, but note it explicitly.

---

## Module 3: `index_store.py`

### What to build

SQLite-backed store for novel metadata. Uses the standard library `sqlite3` (no async needed here — index operations are background only).

```python
class IndexStore:
    def __init__(self, db_path: str = ":memory:"):
        ...
    def init_db(self): ...
    def upsert_novels(self, novels: list[NovelMeta]): ...
    def get_all_titles(self) -> list[tuple[str, str]]:
        """Returns list of (novel_id, title_sc)"""
    def get_novel_by_id(self, novel_id: str) -> dict | None: ...
    def count(self) -> int: ...
```

### Test to write

`backend/tests/test_index_store.py` — always uses `:memory:` SQLite:

```python
def test_insert_and_count():
def test_upsert_is_idempotent():
def test_get_all_titles_returns_simplified():
def test_get_novel_by_id_returns_none_for_missing():
```

---

## Module 4: `search.py`

### What to build

```python
from rapidfuzz import process, fuzz

def fuzzy_search(
    query: str,
    titles: list[tuple[str, str]],   # (novel_id, title_sc)
    limit: int = 20,
    score_cutoff: int = 60,
) -> list[dict]:
    """Returns list of {novel_id, title, score} sorted by score desc."""
```

### Test to write

`backend/tests/test_search.py`:

```python
TITLES = [
    ("001", "嫁给残疾皇子后"),
    ("002", "失婚"),
    ("003", "痛症"),
    ("004", "古装迷情"),
]

def test_exact_match():
    results = fuzzy_search("失婚", TITLES)
    assert results[0]["novel_id"] == "002"

def test_fuzzy_match_with_typo():
    results = fuzzy_search("失昏", TITLES)   # one character off
    assert any(r["novel_id"] == "002" for r in results)

def test_empty_query_returns_empty():
    results = fuzzy_search("", TITLES)
    assert results == []

def test_limit_respected():
    results = fuzzy_search("的", TITLES * 10, limit=3)
    assert len(results) <= 3
```

---

## Module 5: `main.py` (FastAPI)

### What to build

Full FastAPI application with all routes from SKILL.md. Key implementation notes:

- Use `lifespan` context manager (not deprecated `@app.on_event`) for startup index build.
- `/download` uses `StreamingResponse` — never `FileResponse` (no disk writes).
- Dependency-inject `IndexStore` and `crawler` session for testability.
- CORS: `allow_origins=["https://<username>.github.io", "http://localhost:*"]` — make this configurable via env var.

### Test to write

`backend/tests/test_api.py` using `httpx.AsyncClient` + `pytest-asyncio`:

```python
@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_search_returns_results(mock_index):
    # inject mock index with known titles

@pytest.mark.asyncio
async def test_download_novel(mock_crawler):
    # mock crawler.crawl_full_novel to return fixture text

@pytest.mark.asyncio
async def test_download_unknown_novel_returns_404():
```

---

## Module 6: `frontend/index.html`

### What to build

A single self-contained HTML file. Requirements:

- No external JS frameworks (no React, no Vue). Vanilla JS only.
- No build step — must work by opening `index.html` directly in a browser.
- `const BACKEND_URL` at top of `<script>` block — easy to change before deploy.
- Search: `<input>` + debounce (300ms) + `fetch(BACKEND_URL + '/search?q=...')`
- Results: rendered as `<div class="novel-card">` elements dynamically
- Download button: `<a href="..." download>` or opens new tab to `/download?novel_id=...`
- Loading state: spinner shown during fetch
- Error state: friendly error message if backend unreachable
- Index status bar: polls `/status` every 5s while `index_status == "building"`

### Test

No automated test for HTML. Manual test checklist:
- [ ] Open `index.html` in browser (file://)
- [ ] Change `BACKEND_URL` to `http://localhost:8000`
- [ ] Start backend: `cd backend && uv run uvicorn app.main:app --reload`
- [ ] Type a search query — results appear
- [ ] Click download — `.txt` file downloads correctly
- [ ] Shut down backend — friendly error appears in UI

---

## Module 7: Dockerfile

Build and verify in this order:

```bash
# 1. Build image
docker build -t banxia-backend ./backend

# 2. Run tests inside the container
docker run --rm banxia-backend uv run pytest -v --tb=short

# 3. Run the server
docker run -p 8000:8000 -e ADMIN_TOKEN=testtoken banxia-backend

# 4. Verify health check from host
curl http://localhost:8000/
```

All 4 steps must succeed. Fix any Docker-specific issues (missing system deps, path issues) before proceeding.

---

## Module 8: CI Workflow

Write `.github/workflows/ci.yml` as specified in SKILL.md.

**Critical**: The CI workflow must NOT make any network calls to `xbanxia.cc`. All tests use fixtures. If any test makes a live HTTP call that could fail in CI, mock it.

Verify locally by running the same steps:
```bash
pip install uv
cd backend && uv sync --dev
uv run pytest -v --tb=short
```

Must pass with exit code 0.

---

## Module 9: `REPO_STRUCTURE.md`

Write a Markdown file documenting:
1. Every file/directory and its purpose (1-2 sentences each)
2. Core implementation map: for each major feature, the file + function where it's implemented
3. How to run locally (step by step)
4. How to deploy (step by step)

---

## Handling the "Site Structure Unknown" Problem

The single biggest risk in this project is that CSS selectors in `crawler.py` are wrong because the HTML structure of `xbanxia.cc` hasn't been inspected directly.

**Protocol when this happens:**

1. Agent writes crawler with `# TODO: VERIFY SELECTOR` comments on every `.find()` call.
2. Agent creates stub fixture HTML that makes tests pass trivially.
3. Agent explicitly tells the user: *"The following selectors are guesses and MUST be verified in a real browser before deployment: [list them]. Open the site in Chrome DevTools, right-click each element, and confirm or correct the selector."*
4. Once the user provides corrected selectors (or real fixture HTML), the agent updates `config.py` and `tests/fixtures/` and re-runs all tests.

**Never silently guess at selectors and present them as correct.**

---

## On-Demand vs Background Crawl — Agent Decision Guide

The architecture uses **on-demand crawling** for novel content. The index is built in the background. Here is how the agent should implement the dual behaviour:

```
User requests /search?q=X
├── Index has > 0 novels?
│   ├── YES → fuzzy search and return results immediately
│   │         + return index_status in response body
│   └── NO  → trigger background index build (if not already running)
│             + return empty results with index_status: "building"

User requests /download?novel_id=Y
├── Novel ID in index?
│   ├── YES → crawl novel page + all chapters → stream .txt
│   └── NO  → return 404 {"error": "Novel not found. Try searching first."}
```

The background index builder runs as an `asyncio.Task`. It crawls one booklist page at a time with `asyncio.sleep(RATE_LIMIT_SECONDS)` between pages to avoid hammering the source site.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ADMIN_TOKEN` | Yes (prod) | `"dev"` | Secret for `POST /admin/refresh` |
| `ALLOWED_ORIGIN` | No | `"*"` | CORS allowed origin (set to your GitHub Pages URL) |
| `RATE_LIMIT_SECONDS` | No | `1.5` | Seconds between requests to source site |
| `DB_PATH` | No | `":memory:"` | SQLite path (use `/tmp/banxia.db` on Render) |
| `LOG_LEVEL` | No | `"INFO"` | Python logging level |

Never hardcode `ADMIN_TOKEN`. Always read from environment.

---

## What "Done" Looks Like for Each Module

| Module | Done when |
|---|---|
| Scaffold | `uv run python -c "import fastapi"` succeeds |
| converter.py | All 6 converter tests pass |
| crawler.py | All 4 crawler tests pass (even with stubs) |
| index_store.py | All 4 store tests pass |
| search.py | All 4 search tests pass |
| main.py | All API tests pass; `curl localhost:8000/` returns `{"status":"ok"}` |
| frontend | Manual checklist complete |
| Docker | `docker run --rm banxia-backend uv run pytest` exits 0 |
| CI | `uv run pytest` exits 0 locally with same steps as CI |
| REPO_STRUCTURE.md | Filed written, covers all files |
