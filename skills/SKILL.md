# SKILL.md — 半夏小說下載器 (Banxia Novel Downloader)

## Purpose

This skill describes how to build, test, and deploy a full-stack web application that:

1. **Crawls** novel metadata and chapter content from `https://www.xbanxia.cc/` on demand.
2. **Converts** all fetched text from Traditional Chinese (繁體中文) to Simplified Chinese (简体中文).
3. **Serves** a search API with fuzzy matching over novel titles.
4. **Generates** a downloadable `.txt` file per novel, with chapters in order.
5. **Deploys** a FastAPI backend on [Render](https://render.com) and a static frontend on GitHub Pages.

---

## ⚠️ Prerequisites & Known Constraints

| Constraint | Detail |
|---|---|
| **xbanxia.cc is anti-scrape** | The site returns 403/blocks automated access without proper headers. The crawler MUST spoof a browser User-Agent and include realistic request headers. Rate limiting and retry logic are mandatory. |
| **Site structure may change** | As of early 2026, the site uses path-based novel IDs (e.g. `/12345/`). Chapter pages follow `/12345/chapter-N.html` or similar. The crawler must be resilient to HTML structure changes. |
| **No live testing from Anthropic sandbox** | `xbanxia.cc` is blocked by the Anthropic egress proxy. All integration tests in CI use mock HTML fixtures that mirror the real site's structure. **You must test the crawler locally against the live site before deploying.** |
| **Render free tier** | No persistent disk. SQLite is used for the metadata index (stored in `/tmp` or a mounted volume). On cold start, the index is empty and is populated lazily as users search. |
| **Copyright** | This tool accesses a publicly available website. The operator is solely responsible for ensuring compliance with applicable copyright laws and the site's Terms of Service before deploying publicly. |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   GitHub Pages                       │
│   frontend/index.html  (static HTML + vanilla JS)   │
│   - Search box → calls Render API                   │
│   - Results list → Download button per novel        │
└─────────────────────┬───────────────────────────────┘
                      │  HTTPS REST calls
                      ▼
┌─────────────────────────────────────────────────────┐
│              Render.com (FastAPI backend)            │
│                                                     │
│  GET /search?q=<query>                              │
│    └─ fuzzy-search in-memory title index            │
│         └─ if index empty: trigger background crawl │
│                                                     │
│  GET /download?novel_id=<id>                        │
│    └─ crawl novel page → extract chapter list       │
│    └─ fetch each chapter page (rate-limited)        │
│    └─ convert Traditional → Simplified Chinese      │
│    └─ assemble ordered .txt                         │
│    └─ stream file to client                         │
│                                                     │
│  POST /admin/refresh-index   (protected endpoint)   │
│    └─ re-crawl book list pages → update SQLite       │
└─────────────────────┬───────────────────────────────┘
                      │  HTTP (requests + BeautifulSoup)
                      ▼
             https://www.xbanxia.cc/
```

---

## Repository Structure

```
banxia-downloader/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI app, route definitions
│   │   ├── crawler.py          # HTTP fetching + BeautifulSoup parsing
│   │   ├── converter.py        # Traditional → Simplified Chinese (opencc-python-reimplemented)
│   │   ├── search.py           # Fuzzy search index (rapidfuzz + in-memory list)
│   │   ├── index_store.py      # SQLite-backed novel metadata store
│   │   └── config.py           # Settings (base URL, rate limits, headers)
│   ├── tests/
│   │   ├── fixtures/           # Static HTML files mirroring xbanxia.cc structure
│   │   │   ├── booklist_page1.html
│   │   │   ├── novel_page.html
│   │   │   └── chapter_page.html
│   │   ├── test_crawler.py
│   │   ├── test_converter.py
│   │   ├── test_search.py
│   │   ├── test_index_store.py
│   │   └── test_api.py
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── render.yaml
├── frontend/
│   └── index.html              # Single-page static UI (no build step needed)
├── .github/
│   └── workflows/
│       └── ci.yml              # GitHub Actions: run pytest on every PR
├── SKILL.md                    # This file
├── AGENT.md                    # Agent operational instructions
└── REPO_STRUCTURE.md           # Detailed file-by-file summary + core impl map
```

---

## Step-by-Step Implementation Workflow

### Step 1 — Understand the Source Site Structure

Before writing any crawler code, manually inspect `https://www.xbanxia.cc/` in a browser:

1. **Homepage** — lists featured/recent novels. Each novel links to a novel detail page.
2. **Book list / category pages** — paginated lists of all novels, URL pattern:
   - `https://www.xbanxia.cc/booklist/` or `/fenlei/` (check actual path)
   - Pagination: `?page=2`, `?page=3`, etc.
3. **Novel detail page** — e.g. `https://www.xbanxia.cc/12345/`
   - Contains: title, author, category, description, chapter list (ordered `<a>` tags)
4. **Chapter page** — e.g. `https://www.xbanxia.cc/12345/1234567.html`
   - Contains: chapter title, body text inside a specific `<div>` (inspect with DevTools)

**Document the exact CSS selectors** you find for:
- Novel title: (e.g. `h1.book-title` or `#info h1`)
- Chapter list links: (e.g. `#list a`, `.chapter-list li a`)
- Chapter body: (e.g. `#content`, `.chapter-content`)

Record these in `backend/app/config.py` as constants. The crawler is built around these selectors.

> ⚠️ **Selector drift**: If the site redesigns, these selectors break. The crawler logs a `WARNING` and raises `CrawlerParseError` so failures are explicit, never silent.

---

### Step 2 — Backend: Python Crawler (`backend/app/crawler.py`)

**Key implementation rules:**

```python
# Mandatory headers to avoid 403
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Referer": "https://www.xbanxia.cc/",
}

# Rate limiting — NEVER hammer the server
RATE_LIMIT_SECONDS = 1.5   # min delay between requests
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0        # exponential backoff multiplier
```

**Crawler must implement:**

```python
def fetch_booklist_page(page: int) -> list[NovelMeta]:
    """Fetch one page of the novel list. Returns list of (id, title, author, url)."""

def fetch_novel_detail(novel_id: str) -> NovelDetail:
    """Fetch novel detail page. Returns title, author, category, chapter_urls (ordered)."""

def fetch_chapter(url: str) -> ChapterContent:
    """Fetch one chapter. Returns title + body text."""

def crawl_full_novel(novel_id: str) -> str:
    """Orchestrates: fetch detail → fetch all chapters → return assembled text."""
```

**Error handling:**
- `requests.exceptions.HTTPError` (403, 404, 5xx) → retry with backoff, then raise `CrawlerHTTPError`
- `AttributeError` on `.find()` returning `None` → raise `CrawlerParseError` with selector name
- Network timeout → `requests.exceptions.Timeout` → retry

---

### Step 3 — Data Engineering: Traditional→Simplified Converter (`backend/app/converter.py`)

Use `opencc-python-reimplemented` (pure Python, no C extension issues in Docker):

```bash
pip install opencc-python-reimplemented
```

```python
import opencc

_converter = opencc.OpenCC('t2s')   # Traditional → Simplified

def to_simplified(text: str) -> str:
    return _converter.convert(text)
```

**Apply conversion at two points:**
1. Novel title and author (for search index)
2. Full chapter body text (for `.txt` download)

**Test with known Traditional→Simplified pairs:**
- `臺灣` → `台湾`
- `軟體` → `软件`  
- `歡迎` → `欢迎`

---

### Step 4 — Search Index (`backend/app/search.py` + `backend/app/index_store.py`)

**Two-layer design to minimise retrieval overhead:**

#### Layer 1: SQLite metadata store (`index_store.py`)
Persists novel metadata across process restarts (within Render's ephemeral filesystem lifetime):

```sql
CREATE TABLE novels (
    novel_id   TEXT PRIMARY KEY,
    title_sc   TEXT NOT NULL,   -- Simplified Chinese title
    title_tc   TEXT,            -- Original Traditional Chinese title
    author     TEXT,
    category   TEXT,
    url        TEXT NOT NULL,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### Layer 2: In-memory fuzzy search index (`search.py`)
On startup (or after refresh), load all `title_sc` values into a Python list. Use `rapidfuzz` for O(n) fuzzy matching — fast enough for tens of thousands of titles:

```python
from rapidfuzz import process, fuzz

def fuzzy_search(query: str, titles: list[str], limit: int = 20) -> list[SearchResult]:
    results = process.extract(
        query,
        titles,
        scorer=fuzz.WRatio,
        limit=limit,
        score_cutoff=60,
    )
    return results
```

**Index population strategy (on-demand):**
- On first `/search` request: spawn background thread to crawl booklist pages 1–N.
- Return partial results immediately from whatever is already indexed.
- A `/search` response includes `{"results": [...], "index_status": "building|ready", "indexed_count": N}`.

---

### Step 5 — FastAPI Backend (`backend/app/main.py`)

```
GET  /                         → health check {"status": "ok"}
GET  /search?q=<query>&limit=20 → fuzzy search results
GET  /download?novel_id=<id>   → streams assembled .txt file
POST /admin/refresh            → re-crawl book list (header: X-Admin-Token)
GET  /status                   → index build status + count
```

**`/download` endpoint flow:**
1. Check if novel is in SQLite (have its URL).
2. Crawl novel detail page → get ordered chapter URL list.
3. Fetch chapters sequentially (rate-limited). **Stream the response** — do not buffer the entire novel in memory.
4. Convert each chapter to Simplified Chinese as it's fetched.
5. Write formatted output:
   ```
   《书名》
   作者：XXX
   
   第一章 章节标题
   
   [chapter body text]
   
   第二章 章节标题
   
   [chapter body text]
   ...
   ```
6. Set headers: `Content-Disposition: attachment; filename="<title>.txt"`, `Content-Type: text/plain; charset=utf-8`.

> ⚠️ **Render free tier timeout**: Render's free tier has a 30-second request timeout. For long novels (500+ chapters), streaming is essential — and even then, very long downloads may time out. Document this limitation explicitly in the UI.

---

### Step 6 — Frontend (`frontend/index.html`)

Single static HTML file. No build step. No framework dependencies. Deployed to GitHub Pages.

**Features:**
- Search box → debounced `fetch()` to Render backend `/search`
- Results rendered as cards: title, author, category
- "下载 .txt" button per result → opens `/download?novel_id=X` in new tab
- Loading spinner + error states
- Index build progress indicator (polls `/status`)

**CORS**: The FastAPI backend must allow `https://<your-github-username>.github.io` as an allowed origin.

**Config**: The Render backend URL is stored as a `const BACKEND_URL = "https://your-app.onrender.com"` at the top of the HTML file. **Change this before deploying.**

---

### Step 7 — Deployment

#### Backend on Render

1. Push `backend/` to GitHub.
2. Create a new **Web Service** on Render, pointing to the `backend/` directory.
3. Set build command: `pip install -e .`
4. Set start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Set environment variable: `ADMIN_TOKEN=<your-secret>`
6. On first deploy, trigger `POST /admin/refresh` to start index building.

`render.yaml` (Infrastructure-as-Code):
```yaml
services:
  - type: web
    name: banxia-backend
    env: python
    buildCommand: pip install -e .
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: ADMIN_TOKEN
        generateValue: true
```

#### Frontend on GitHub Pages

1. Put `frontend/index.html` in the `docs/` folder (or use a `gh-pages` branch).
2. In GitHub repo Settings → Pages → Source: `docs/` folder on `main` branch.
3. Update `BACKEND_URL` in `index.html` to your Render URL before pushing.

#### Startup crawl (Step 6 requirement)

Add a Render **deploy hook** or a startup event in FastAPI:

```python
@app.on_event("startup")
async def startup_event():
    # Start background index build if DB is empty
    if index_store.count() == 0:
        asyncio.create_task(build_index_background())
```

---

### Step 8 — Testing

All tests live in `backend/tests/`. Run with:

```bash
cd backend
uv run pytest -v
```

**Test coverage required:**

| Module | Test file | What's tested |
|---|---|---|
| `crawler.py` | `test_crawler.py` | Parse booklist/novel/chapter from fixture HTML; HTTP error retry logic |
| `converter.py` | `test_converter.py` | Known TC→SC pairs; idempotency on already-SC text |
| `search.py` | `test_search.py` | Exact match; fuzzy match with typo; empty query; no results |
| `index_store.py` | `test_index_store.py` | Insert, query, upsert, count; in-memory SQLite for tests |
| `main.py` (API) | `test_api.py` | All endpoints with mocked crawler; 200/404/422 status codes |

**Fixture HTML files** in `tests/fixtures/` must accurately reflect the real site's HTML structure (copy from browser DevTools → "Copy outerHTML" on the relevant container).

> ⚠️ You must populate the fixtures from a real browser session. Do not guess the HTML structure.

---

### Step 9 — Docker

```dockerfile
# backend/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install -e ".[dev]"
COPY app/ ./app/
COPY tests/ ./tests/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and test:
```bash
# Build
docker build -t banxia-backend ./backend

# Run tests inside container
docker run --rm banxia-backend uv run pytest -v

# Run server locally
docker run -p 8000:8000 -e ADMIN_TOKEN=test banxia-backend
```

---

### Step 10 — GitHub Actions CI

`.github/workflows/ci.yml`:
```yaml
name: CI
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install uv
        run: pip install uv
      - name: Install dependencies
        run: cd backend && uv sync --dev
      - name: Run tests
        run: cd backend && uv run pytest -v --tb=short
```

> CI tests use only fixture HTML — **no live network calls to xbanxia.cc in CI**. Live integration is done locally only.

---

## Core Implementation Checklist

Before marking the project "ready to deploy", verify each item:

- [ ] `crawler.py` tested against real site locally (not just fixtures)
- [ ] `converter.py` passes Traditional→Simplified unit tests
- [ ] `search.py` fuzzy search returns relevant results
- [ ] `/download` endpoint streams a complete, readable `.txt` for at least one real novel
- [ ] CORS configured for GitHub Pages origin
- [ ] `ADMIN_TOKEN` set as environment variable on Render (never hardcoded)
- [ ] Frontend `BACKEND_URL` updated to real Render URL
- [ ] All `pytest` tests pass (including in Docker)
- [ ] GitHub Actions CI passes on `main`
- [ ] `render.yaml` committed so deployment is reproducible

---

## Dependencies (`pyproject.toml`)

```toml
[project]
name = "banxia-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.29.0",
    "requests>=2.31.0",
    "beautifulsoup4>=4.12.0",
    "lxml>=5.1.0",
    "opencc-python-reimplemented>=0.1.7",
    "rapidfuzz>=3.6.0",
    "aiosqlite>=0.20.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.27.0",       # for FastAPI TestClient
    "pytest-cov>=5.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```
