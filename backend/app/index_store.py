"""SQLite-backed storage for indexed novel metadata."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from app.converter import to_simplified
from app.crawler import NovelDetail, NovelMeta
from app.search import SearchDocument


class IndexStore:
    def __init__(self, db_path: str = ":memory:"):
        resolved_db_path = db_path
        if db_path != ":memory:" and not db_path.startswith("file:"):
            db_file = Path(db_path).expanduser()
            db_file.parent.mkdir(parents=True, exist_ok=True)
            resolved_db_path = str(db_file)

        self.db_path = resolved_db_path
        self.conn = sqlite3.connect(resolved_db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        self.conn.execute(
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
        self._ensure_column("author_tc", "TEXT")
        self._ensure_column("author_sc", "TEXT")
        self._ensure_column("category_tc", "TEXT")
        self._ensure_column("category_sc", "TEXT")
        self._ensure_column("latest_update", "TEXT")
        self._ensure_column("detail_checked_at", "TIMESTAMP")
        self._ensure_column("last_accessed_at", "TIMESTAMP")
        self.conn.commit()

    def upsert_novels(self, novels: list[NovelMeta]):
        rows = [
            (
                novel.novel_id,
                to_simplified(novel.title),
                novel.title,
                novel.author,
                to_simplified(novel.author),
                novel.category,
                to_simplified(novel.category),
                novel.url,
                novel.latest_update,
            )
            for novel in novels
        ]
        self.conn.executemany(
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(novel_id) DO UPDATE SET
                title_sc = excluded.title_sc,
                title_tc = excluded.title_tc,
                author_tc = COALESCE(NULLIF(excluded.author_tc, ''), novels.author_tc),
                author_sc = COALESCE(NULLIF(excluded.author_sc, ''), novels.author_sc),
                category_tc = COALESCE(NULLIF(excluded.category_tc, ''), novels.category_tc),
                category_sc = COALESCE(NULLIF(excluded.category_sc, ''), novels.category_sc),
                url = excluded.url,
                latest_update = COALESCE(NULLIF(excluded.latest_update, ''), novels.latest_update),
                indexed_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
        self.conn.commit()

    def update_novel_detail(self, detail: NovelDetail, url: str | None = None):
        self.conn.execute(
            """
            UPDATE novels
            SET title_sc = ?,
                title_tc = ?,
                author_tc = ?,
                author_sc = ?,
                category_tc = ?,
                category_sc = ?,
                url = COALESCE(?, url),
                latest_update = ?,
                detail_checked_at = CURRENT_TIMESTAMP
            WHERE novel_id = ?
            """,
            (
                to_simplified(detail.title),
                detail.title,
                detail.author,
                to_simplified(detail.author),
                detail.category,
                to_simplified(detail.category),
                url,
                detail.latest_update,
                detail.novel_id,
            ),
        )
        self.conn.commit()

    def touch_novels(self, novel_ids: list[str]):
        if not novel_ids:
            return
        self.conn.executemany(
            """
            UPDATE novels
            SET last_accessed_at = CURRENT_TIMESTAMP
            WHERE novel_id = ?
            """,
            [(novel_id,) for novel_id in dict.fromkeys(novel_ids)],
        )
        self.conn.commit()

    def get_search_documents(self) -> list[SearchDocument]:
        rows = self.conn.execute(
            """
            SELECT novel_id, title_sc, title_tc, author_sc, author_tc, category_sc, category_tc
            FROM novels
            ORDER BY novel_id
            """
        ).fetchall()
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
        rows = self.conn.execute("SELECT novel_id, title_sc FROM novels ORDER BY novel_id").fetchall()
        return [(row["novel_id"], row["title_sc"]) for row in rows]

    def get_recent_novels(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT novel_id, title_sc, title_tc, author_tc, author_sc, category_tc, category_sc,
                   url, latest_update, indexed_at
            FROM novels
            ORDER BY CASE WHEN latest_update IS NULL OR latest_update = '' THEN 1 ELSE 0 END,
                     latest_update DESC,
                     indexed_at DESC,
                     novel_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_novel_by_id(self, novel_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT novel_id, title_sc, title_tc, author_tc, author_sc, category_tc, category_sc,
                   url, latest_update, detail_checked_at, last_accessed_at, indexed_at
            FROM novels
            WHERE novel_id = ?
            """,
            (novel_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS count FROM novels").fetchone()
        return int(row["count"])

    def cache_stats(self) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                MIN(indexed_at) AS oldest_indexed_at,
                MAX(indexed_at) AS newest_indexed_at,
                MIN(last_accessed_at) AS oldest_accessed_at,
                MAX(last_accessed_at) AS newest_accessed_at
            FROM novels
            """
        ).fetchone()
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

        rows = self.conn.execute(
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
            LIMIT ?
            """,
            (delete_count,),
        ).fetchall()
        novel_ids = [row["novel_id"] for row in rows]
        if not novel_ids:
            return 0

        placeholders = ", ".join("?" for _ in novel_ids)
        self.conn.execute(
            f"DELETE FROM novels WHERE novel_id IN ({placeholders})",
            novel_ids,
        )
        self.conn.commit()
        if self.db_path != ":memory:" and not self.db_path.startswith("file:"):
            self.conn.execute("VACUUM")
        return len(novel_ids)

    def close(self):
        self.conn.close()

    def _ensure_column(self, column_name: str, column_type: str):
        existing_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(novels)").fetchall()
        }
        if column_name in existing_columns:
            return
        self.conn.execute(f"ALTER TABLE novels ADD COLUMN {column_name} {column_type}")
