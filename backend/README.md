# Banxia Backend

Backend service for crawling, indexing, and serving Banxia novel downloads.

## Environment

Copy `.env.example` or set the equivalent environment variables in Render:

- `DATABASE_URL`
- `ADMIN_TOKEN`
- `CACHE_STORAGE_BACKEND`
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET`
- `R2_KEY_PREFIX`
- `R2_ENDPOINT_URL`

Set `CACHE_STORAGE_BACKEND=r2` to store finished novel `.txt` files in Cloudflare R2.
When `CACHE_STORAGE_BACKEND=database`, the app keeps using the local database-backed
cache for full novel text.
The production-safe default for `FETCH_BACKENDS` is `requests`.

## Render Service Settings

For the current one-service deployment, the backend expects these settings:

- Root Directory: `backend`
- Build Command: `uv sync --frozen`
- Start Command: `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT`

For a low-cost first launch, keep a single web service and let the app cache novels
on demand as users request them. Add a separate worker later if you want to prefill
the whole catalog in the background.
