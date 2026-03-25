from sqlalchemy import text

from app.crawler import NovelDetail, NovelMeta
from app.index_store import IndexStore, resolve_database_url


def make_novel(novel_id: str, title: str, author: str = "作者甲", category: str = "分類") -> NovelMeta:
    return NovelMeta(
        novel_id=novel_id,
        title=title,
        author=author,
        category=category,
        url=f"https://www.xbanxia.cc/books/{novel_id}.html",
    )


def test_insert_and_count():
    store = IndexStore(":memory:")

    store.upsert_novels([make_novel("001", "臺灣戀曲"), make_novel("002", "歡迎光臨")])

    assert store.count() == 2


def test_upsert_is_idempotent():
    store = IndexStore(":memory:")

    store.upsert_novels([make_novel("001", "舊書名")])
    store.upsert_novels([make_novel("001", "新書名", author="作者乙")])

    record = store.get_novel_by_id("001")
    assert store.count() == 1
    assert record is not None
    assert record["title_tc"] == "新書名"
    assert record["author_tc"] == "作者乙"


def test_get_all_titles_returns_simplified():
    store = IndexStore(":memory:")

    store.upsert_novels([make_novel("001", "臺灣戀曲"), make_novel("002", "歡迎光臨")])

    assert store.get_all_titles() == [("001", "台湾恋曲"), ("002", "欢迎光临")]


def test_get_novel_by_id_returns_none_for_missing():
    store = IndexStore(":memory:")

    assert store.get_novel_by_id("missing") is None


def test_update_novel_detail_and_get_recent_novels():
    store = IndexStore(":memory:")
    store.upsert_novels([make_novel("001", "臺灣戀曲"), make_novel("002", "歡迎光臨")])

    store.update_novel_detail(
        NovelDetail(
            novel_id="001",
            title="臺灣戀曲",
            author="作者甲",
            category="臺灣言情",
            chapter_urls=[],
            latest_update="2026-03-13",
        )
    )
    store.update_novel_detail(
        NovelDetail(
            novel_id="002",
            title="歡迎光臨",
            author="作者甲",
            category="其他言情",
            chapter_urls=[],
            latest_update="2026-03-11",
        )
    )

    recent = store.get_recent_novels(limit=2)

    assert recent[0]["novel_id"] == "001"
    assert recent[0]["latest_update"] == "2026-03-13"


def test_file_backed_store_persists_cached_index(tmp_path):
    db_path = tmp_path / "cache.sqlite3"

    store = IndexStore(str(db_path))
    store.upsert_novels([make_novel("001", "臺灣戀曲")])
    store.close()

    reopened = IndexStore(str(db_path))
    try:
        record = reopened.get_novel_by_id("001")
        assert record is not None
        assert record["title_sc"] == "台湾恋曲"
    finally:
        reopened.close()


def test_file_backed_store_persists_cached_novel(tmp_path):
    db_path = tmp_path / "cache.sqlite3"

    store = IndexStore(str(db_path))
    store.upsert_cached_novel(
        novel_id="001",
        title_sc="台湾恋曲",
        content_txt="《台湾恋曲》\n作者：作者甲\n",
        chapter_count=2,
    )
    store.close()

    reopened = IndexStore(str(db_path))
    try:
        record = reopened.get_cached_novel("001")
        assert record is not None
        assert record["title_sc"] == "台湾恋曲"
        assert record["chapter_count"] == 2
    finally:
        reopened.close()


def test_file_backed_store_persists_uploaded_document(tmp_path):
    db_path = tmp_path / "cache.sqlite3"

    store = IndexStore(str(db_path))
    store.upsert_uploaded_document(
        upload_id="upload-001",
        source_filename="taiwan-love.txt",
        title_tc="臺灣戀曲",
        title_sc="台湾恋曲",
        author_tc="作者甲",
        author_sc="作者甲",
        content_txt="《台湾恋曲》\n作者：作者甲\n",
        content_bytes=36,
        content_sha256="abc123",
    )
    store.close()

    reopened = IndexStore(str(db_path))
    try:
        record = reopened.get_uploaded_document("upload-001")
        assert record is not None
        assert record["title_sc"] == "台湾恋曲"
        assert record["source_filename"] == "taiwan-love.txt"
    finally:
        reopened.close()


def test_prune_oldest_novels_prefers_unaccessed_rows():
    store = IndexStore(":memory:")
    store.upsert_novels(
        [
            make_novel("001", "臺灣戀曲"),
            make_novel("002", "歡迎光臨"),
            make_novel("003", "臺灣甜心"),
        ]
    )
    with store.engine.begin() as conn:
        conn.execute(text("UPDATE novels SET indexed_at = '2026-03-10 00:00:00' WHERE novel_id = '001'"))
        conn.execute(text("UPDATE novels SET indexed_at = '2026-03-11 00:00:00' WHERE novel_id = '002'"))
        conn.execute(text("UPDATE novels SET indexed_at = '2026-03-12 00:00:00' WHERE novel_id = '003'"))
        conn.execute(text("UPDATE novels SET last_accessed_at = '2026-03-14 09:00:00' WHERE novel_id = '001'"))

    deleted = store.prune_oldest_novels(max_novels=2, prune_to_novels=2)

    assert deleted == 1
    assert store.get_novel_by_id("001") is not None
    assert store.get_novel_by_id("002") is None
    assert store.get_novel_by_id("003") is not None


def test_prune_oldest_novels_removes_cached_downloads():
    store = IndexStore(":memory:")
    store.upsert_novels(
        [
            make_novel("001", "臺灣戀曲"),
            make_novel("002", "歡迎光臨"),
            make_novel("003", "臺灣甜心"),
        ]
    )
    store.upsert_cached_novel(
        novel_id="002",
        title_sc="欢迎光临",
        content_txt="cached",
        chapter_count=1,
    )
    with store.engine.begin() as conn:
        conn.execute(text("UPDATE novels SET indexed_at = '2026-03-10 00:00:00' WHERE novel_id = '001'"))
        conn.execute(text("UPDATE novels SET indexed_at = '2026-03-11 00:00:00' WHERE novel_id = '002'"))
        conn.execute(text("UPDATE novels SET indexed_at = '2026-03-12 00:00:00' WHERE novel_id = '003'"))
        conn.execute(text("UPDATE novels SET last_accessed_at = '2026-03-14 09:00:00' WHERE novel_id = '001'"))

    store.prune_oldest_novels(max_novels=2, prune_to_novels=2)

    assert store.get_cached_novel("002") is None


def test_resolve_database_url_uses_sqlite_memory_by_default():
    assert resolve_database_url() == "sqlite+pysqlite:///:memory:"


def test_resolve_database_url_normalizes_postgres_scheme():
    assert resolve_database_url(database_url="postgres://user:pass@host/db") == "postgresql+psycopg://user:pass@host/db"
