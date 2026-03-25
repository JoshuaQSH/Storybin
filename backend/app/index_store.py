"""Database-backed storage for indexed novel metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from app.converter import to_simplified
from app.crawler import NovelDetail, NovelMeta
from app.search import SearchDocument


def resolve_database_url(db_path: str = ":memory:", database_url: str | None = None) -> str:
    if database_url:
        normalized = database_url.strip()
        if normalized.startswith("postgres://"):
            return normalized.replace("postgres://", "postgresql+psycopg://", 1)
        if normalized.startswith("postgresql://"):
            return normalized.replace("postgresql://", "postgresql+psycopg://", 1)
        return normalized

    if db_path == ":memory:":
        return "sqlite+pysqlite:///:memory:"

    db_file = Path(db_path).expanduser()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{db_file}"


class IndexStore:
    def __init__(self, db_path: str = ":memory:", *, database_url: str | None = None):
        self.database_url = resolve_database_url(db_path=db_path, database_url=database_url)
        self.storage_backend = "postgres" if self.database_url.startswith("postgresql+") else "sqlite"
        self.engine = self._create_engine(self.database_url)
        self.init_db()

    def _create_engine(self, database_url: str) -> Engine:
        if database_url == "sqlite+pysqlite:///:memory:":
            return create_engine(
                database_url,
                future=True,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        if database_url.startswith("sqlite+"):
            return create_engine(
                database_url,
                future=True,
                connect_args={"check_same_thread": False},
                pool_pre_ping=True,
            )
        return create_engine(database_url, future=True, pool_pre_ping=True)

    def init_db(self):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS novels (
                        novel_id           TEXT PRIMARY KEY,
                        title_sc           TEXT NOT NULL,
                        title_tc           TEXT,
                        author_tc          TEXT,
                        author_sc          TEXT,
                        category_tc        TEXT,
                        category_sc        TEXT,
                        url                TEXT NOT NULL,
                        latest_update      TEXT,
                        detail_checked_at  TIMESTAMP,
                        last_accessed_at   TIMESTAMP,
                        indexed_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS novel_download_cache (
                        novel_id          TEXT PRIMARY KEY,
                        title_sc          TEXT NOT NULL,
                        content_txt       TEXT NOT NULL DEFAULT '',
                        storage_backend   TEXT NOT NULL DEFAULT 'database',
                        object_key        TEXT,
                        content_bytes     INTEGER,
                        content_sha256    TEXT,
                        chapter_count     INTEGER NOT NULL DEFAULT 0,
                        cached_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_accessed_at  TIMESTAMP
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS uploaded_documents (
                        upload_id          TEXT PRIMARY KEY,
                        source_filename    TEXT NOT NULL,
                        title_tc           TEXT,
                        title_sc           TEXT NOT NULL,
                        author_tc          TEXT,
                        author_sc          TEXT,
                        content_txt        TEXT NOT NULL,
                        content_bytes      INTEGER,
                        content_sha256     TEXT,
                        created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_accessed_at   TIMESTAMP
                    )
                    """
                )
            )
        self._ensure_column("author_tc", "TEXT")
        self._ensure_column("author_sc", "TEXT")
        self._ensure_column("category_tc", "TEXT")
        self._ensure_column("category_sc", "TEXT")
        self._ensure_column("latest_update", "TEXT")
        self._ensure_column("detail_checked_at", "TIMESTAMP")
        self._ensure_column("last_accessed_at", "TIMESTAMP")
        self._ensure_column("storage_backend", "TEXT", table_name="novel_download_cache")
        self._ensure_column("object_key", "TEXT", table_name="novel_download_cache")
        self._ensure_column("content_bytes", "INTEGER", table_name="novel_download_cache")
        self._ensure_column("content_sha256", "TEXT", table_name="novel_download_cache")

    def upsert_novels(self, novels: list[NovelMeta]):
        rows = [
            {
                "novel_id": novel.novel_id,
                "title_sc": to_simplified(novel.title),
                "title_tc": novel.title,
                "author_tc": novel.author,
                "author_sc": to_simplified(novel.author),
                "category_tc": novel.category,
                "category_sc": to_simplified(novel.category),
                "url": novel.url,
                "latest_update": novel.latest_update,
            }
            for novel in novels
        ]
        if not rows:
            return
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO novels (
                        novel_id,
                        title_sc,
                        title_tc,
                        author_tc,
                        author_sc,
                        category_tc,
                        category_sc,
                        url,
                        latest_update
                    )
                    VALUES (
                        :novel_id,
                        :title_sc,
                        :title_tc,
                        :author_tc,
                        :author_sc,
                        :category_tc,
                        :category_sc,
                        :url,
                        :latest_update
                    )
                    ON CONFLICT (novel_id) DO UPDATE SET
                        title_sc = excluded.title_sc,
                        title_tc = excluded.title_tc,
                        author_tc = COALESCE(NULLIF(excluded.author_tc, ''), novels.author_tc),
                        author_sc = COALESCE(NULLIF(excluded.author_sc, ''), novels.author_sc),
                        category_tc = COALESCE(NULLIF(excluded.category_tc, ''), novels.category_tc),
                        category_sc = COALESCE(NULLIF(excluded.category_sc, ''), novels.category_sc),
                        url = excluded.url,
                        latest_update = COALESCE(NULLIF(excluded.latest_update, ''), novels.latest_update),
                        indexed_at = CURRENT_TIMESTAMP
                    """
                ),
                rows,
            )

    def update_novel_detail(self, detail: NovelDetail, url: str | None = None):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE novels
                    SET title_sc = :title_sc,
                        title_tc = :title_tc,
                        author_tc = :author_tc,
                        author_sc = :author_sc,
                        category_tc = :category_tc,
                        category_sc = :category_sc,
                        url = COALESCE(:url, url),
                        latest_update = :latest_update,
                        detail_checked_at = CURRENT_TIMESTAMP
                    WHERE novel_id = :novel_id
                    """
                ),
                {
                    "title_sc": to_simplified(detail.title),
                    "title_tc": detail.title,
                    "author_tc": detail.author,
                    "author_sc": to_simplified(detail.author),
                    "category_tc": detail.category,
                    "category_sc": to_simplified(detail.category),
                    "url": url,
                    "latest_update": detail.latest_update,
                    "novel_id": detail.novel_id,
                },
            )

    def touch_novels(self, novel_ids: list[str]):
        unique_novel_ids = list(dict.fromkeys(novel_ids))
        if not unique_novel_ids:
            return
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE novels
                    SET last_accessed_at = CURRENT_TIMESTAMP
                    WHERE novel_id = :novel_id
                    """
                ),
                [{"novel_id": novel_id} for novel_id in unique_novel_ids],
            )

    def get_search_documents(self) -> list[SearchDocument]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT novel_id, title_sc, title_tc, author_sc, author_tc, category_sc, category_tc
                    FROM novels
                    ORDER BY novel_id
                    """
                )
            ).mappings().all()
        return [
            SearchDocument(
                novel_id=row["novel_id"],
                title_sc=row["title_sc"] or "",
                title_tc=row["title_tc"] or "",
                author_sc=row["author_sc"] or "",
                author_tc=row["author_tc"] or "",
                category_sc=row["category_sc"] or "",
                category_tc=row["category_tc"] or "",
            )
            for row in rows
        ]

    def get_all_titles(self) -> list[tuple[str, str]]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT novel_id, title_sc FROM novels ORDER BY novel_id")
            ).mappings().all()
        return [(row["novel_id"], row["title_sc"]) for row in rows]

    def get_recent_novels(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT novel_id, title_sc, title_tc, author_tc, author_sc, category_tc, category_sc,
                           url, latest_update, indexed_at
                    FROM novels
                    ORDER BY CASE WHEN latest_update IS NULL OR latest_update = '' THEN 1 ELSE 0 END,
                             latest_update DESC,
                             indexed_at DESC,
                             novel_id DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).mappings().all()
        return [dict(row) for row in rows]

    def get_novel_by_id(self, novel_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT novel_id, title_sc, title_tc, author_tc, author_sc, category_tc, category_sc,
                           url, latest_update, detail_checked_at, last_accessed_at, indexed_at
                    FROM novels
                    WHERE novel_id = :novel_id
                    """
                ),
                {"novel_id": novel_id},
            ).mappings().first()
        if row is None:
            return None
        return dict(row)

    def upsert_cached_novel(
        self,
        *,
        novel_id: str,
        title_sc: str,
        content_txt: str,
        chapter_count: int,
        storage_backend: str = "database",
        object_key: str | None = None,
        content_bytes: int | None = None,
        content_sha256: str | None = None,
    ):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO novel_download_cache (
                        novel_id,
                        title_sc,
                        content_txt,
                        storage_backend,
                        object_key,
                        content_bytes,
                        content_sha256,
                        chapter_count,
                        last_accessed_at
                    )
                    VALUES (
                        :novel_id,
                        :title_sc,
                        :content_txt,
                        :storage_backend,
                        :object_key,
                        :content_bytes,
                        :content_sha256,
                        :chapter_count,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (novel_id) DO UPDATE SET
                        title_sc = excluded.title_sc,
                        content_txt = excluded.content_txt,
                        storage_backend = excluded.storage_backend,
                        object_key = excluded.object_key,
                        content_bytes = excluded.content_bytes,
                        content_sha256 = excluded.content_sha256,
                        chapter_count = excluded.chapter_count,
                        cached_at = CURRENT_TIMESTAMP,
                        last_accessed_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "novel_id": novel_id,
                    "title_sc": title_sc,
                    "content_txt": content_txt,
                    "storage_backend": storage_backend,
                    "object_key": object_key,
                    "content_bytes": content_bytes,
                    "content_sha256": content_sha256,
                    "chapter_count": chapter_count,
                },
            )

    def get_cached_novel(self, novel_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT novel_id, title_sc, content_txt, storage_backend, object_key,
                           content_bytes, content_sha256, chapter_count, cached_at, last_accessed_at
                    FROM novel_download_cache
                    WHERE novel_id = :novel_id
                    """
                ),
                {"novel_id": novel_id},
            ).mappings().first()
        if row is None:
            return None
        return dict(row)

    def touch_cached_novel(self, novel_id: str):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE novel_download_cache
                    SET last_accessed_at = CURRENT_TIMESTAMP
                    WHERE novel_id = :novel_id
                    """
                ),
                {"novel_id": novel_id},
            )

    def upsert_uploaded_document(
        self,
        *,
        upload_id: str,
        source_filename: str,
        title_tc: str,
        title_sc: str,
        author_tc: str,
        author_sc: str,
        content_txt: str,
        content_bytes: int,
        content_sha256: str,
    ):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO uploaded_documents (
                        upload_id,
                        source_filename,
                        title_tc,
                        title_sc,
                        author_tc,
                        author_sc,
                        content_txt,
                        content_bytes,
                        content_sha256,
                        last_accessed_at
                    )
                    VALUES (
                        :upload_id,
                        :source_filename,
                        :title_tc,
                        :title_sc,
                        :author_tc,
                        :author_sc,
                        :content_txt,
                        :content_bytes,
                        :content_sha256,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (upload_id) DO UPDATE SET
                        source_filename = excluded.source_filename,
                        title_tc = excluded.title_tc,
                        title_sc = excluded.title_sc,
                        author_tc = excluded.author_tc,
                        author_sc = excluded.author_sc,
                        content_txt = excluded.content_txt,
                        content_bytes = excluded.content_bytes,
                        content_sha256 = excluded.content_sha256,
                        last_accessed_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "upload_id": upload_id,
                    "source_filename": source_filename,
                    "title_tc": title_tc,
                    "title_sc": title_sc,
                    "author_tc": author_tc,
                    "author_sc": author_sc,
                    "content_txt": content_txt,
                    "content_bytes": content_bytes,
                    "content_sha256": content_sha256,
                },
            )

    def get_uploaded_document(self, upload_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT upload_id, source_filename, title_tc, title_sc, author_tc, author_sc,
                           content_txt, content_bytes, content_sha256, created_at, last_accessed_at
                    FROM uploaded_documents
                    WHERE upload_id = :upload_id
                    """
                ),
                {"upload_id": upload_id},
            ).mappings().first()
        if row is None:
            return None
        return dict(row)

    def touch_uploaded_document(self, upload_id: str):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE uploaded_documents
                    SET last_accessed_at = CURRENT_TIMESTAMP
                    WHERE upload_id = :upload_id
                    """
                ),
                {"upload_id": upload_id},
            )

    def cached_novel_count(self) -> int:
        with self.engine.begin() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) AS count FROM novel_download_cache")
            ).scalar_one()
        return int(count)

    def count(self) -> int:
        with self.engine.begin() as conn:
            count = conn.execute(text("SELECT COUNT(*) AS count FROM novels")).scalar_one()
        return int(count)

    def cache_stats(self) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        COUNT(*) AS count,
                        MIN(indexed_at) AS oldest_indexed_at,
                        MAX(indexed_at) AS newest_indexed_at,
                        MIN(last_accessed_at) AS oldest_accessed_at,
                        MAX(last_accessed_at) AS newest_accessed_at
                    FROM novels
                    """
                )
            ).mappings().one()
        return dict(row)

    def prune_oldest_novels(
        self,
        *,
        max_novels: int,
        prune_to_novels: int | None = None,
    ) -> int:
        if max_novels <= 0:
            return 0

        total = self.count()
        if total <= max_novels:
            return 0

        target = prune_to_novels if prune_to_novels is not None else max_novels
        if target <= 0 or target >= total:
            target = max_novels

        delete_count = total - target
        if delete_count <= 0:
            return 0

        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT novel_id
                    FROM novels
                    ORDER BY
                        CASE WHEN last_accessed_at IS NULL THEN 0 ELSE 1 END ASC,
                        COALESCE(last_accessed_at, detail_checked_at, indexed_at) ASC,
                        CASE WHEN latest_update IS NULL OR latest_update = '' THEN 0 ELSE 1 END ASC,
                        latest_update ASC,
                        indexed_at ASC,
                        novel_id ASC
                    LIMIT :limit
                    """
                ),
                {"limit": delete_count},
            ).mappings().all()
            novel_ids = [row["novel_id"] for row in rows]
            if not novel_ids:
                return 0
            delete_stmt = text(
                "DELETE FROM novels WHERE novel_id IN :novel_ids"
            ).bindparams(bindparam("novel_ids", expanding=True))
            conn.execute(delete_stmt, {"novel_ids": novel_ids})
            delete_cache_stmt = text(
                "DELETE FROM novel_download_cache WHERE novel_id IN :novel_ids"
            ).bindparams(bindparam("novel_ids", expanding=True))
            conn.execute(delete_cache_stmt, {"novel_ids": novel_ids})
        return len(novel_ids)

    def close(self):
        self.engine.dispose()

    def _ensure_column(self, column_name: str, column_type: str, *, table_name: str = "novels"):
        if column_name in self._existing_columns(table_name=table_name):
            return
        with self.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))

    def _existing_columns(self, *, table_name: str = "novels") -> set[str]:
        with self.engine.begin() as conn:
            if self.storage_backend == "sqlite":
                rows = conn.execute(text(f"PRAGMA table_info({table_name})")).mappings().all()
                return {row["name"] for row in rows}
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = :table_name
                    """
                ),
                {"table_name": table_name},
            ).mappings().all()
        return {row["column_name"] for row in rows}
