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
- `CRAWLER_HTTP_PROXY`
- `CRAWLER_HTTPS_PROXY`

Set `CACHE_STORAGE_BACKEND=r2` to store finished novel `.txt` files in Cloudflare R2.
When `CACHE_STORAGE_BACKEND=database`, the app keeps using the local database-backed
cache for full novel text.
The default `FETCH_BACKENDS` chain is `requests,curl_cffi`. Add `playwright` only if
your runtime includes the required browser system libraries.

## Render Service Settings

For the current one-service deployment, the backend expects these settings:

- Root Directory: `backend`
- Build Command: `uv sync --frozen`
- Start Command: `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT`

For a low-cost first launch, keep a single web service and let the app cache novels
on demand as users request them. Add a separate worker later if you want to prefill
the whole catalog in the background.

## Recommended Production Workflow

Render may be unable to reach `xbanxia.cc` directly from some regions. When that
happens, seed the remote cache from a machine that can access the source site:

```bash
uv run python -m app.seed_remote \
  --backend-url https://storybin.onrender.com \
  --admin-token "$ADMIN_TOKEN" \
  --novel-id 410113 \
  --novel-id 410182
```

You can also crawl list pages locally and import a batch:

```bash
uv run python -m app.seed_remote \
  --backend-url https://storybin.onrender.com \
  --admin-token "$ADMIN_TOKEN" \
  --page-start 1 \
  --page-end 2 \
  --limit 20
```

To discover the whole category automatically and run a larger batch with a few workers:

```bash
uv run python -m app.seed_remote \
  --backend-url https://storybin.onrender.com \
  --admin-token "$ADMIN_TOKEN" \
  --all-pages \
  --workers 4
```

### Best workflow for Free Render

If you stay on the Free Render web service, the safest workflow is:

1. Crawl on your local machine.
2. Upload finished `.txt` files to R2 from your local machine.
3. Send only lightweight metadata to Render so it can index and serve downloads.

That avoids making the Free Render web service upload the full novel bodies itself.

```bash
uv run python -m app.seed_remote \
  --backend-url https://storybin.onrender.com \
  --admin-token "$ADMIN_TOKEN" \
  --all-pages \
  --upload-to-r2 \
  --workers 2
```

If you want a resumable local crawl, save each converted novel as a JSON payload first:

```bash
uv run python -m app.seed_remote \
  --backend-url https://storybin.onrender.com \
  --admin-token "$ADMIN_TOKEN" \
  --all-pages \
  --spool-dir ./data/seed_spool \
  --spool-only
```

Then import those saved payloads later without crawling the source site again:

```bash
uv run python -m app.seed_remote \
  --backend-url https://storybin.onrender.com \
  --admin-token "$ADMIN_TOKEN" \
  --import-from-spool ./data/seed_spool \
  --upload-to-r2 \
  --workers 2
```

For deployments where Render cannot reach `xbanxia.cc` directly, you can point the
crawler at an HTTP/HTTPS proxy by setting `CRAWLER_HTTP_PROXY` and
`CRAWLER_HTTPS_PROXY`.

## Upload Conversion

The frontend now supports uploading a Traditional Chinese `.txt` novel exported from
Tampermonkey or another browser tool. The backend converts it to Simplified Chinese,
stores the converted text, and exposes two download formats:

- Simplified Chinese `.txt`
- Simplified Chinese `.epub`
