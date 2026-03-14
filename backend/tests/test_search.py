from app.search import SearchDocument, fuzzy_search

DOCUMENTS = [
    SearchDocument(
        novel_id="001",
        title_sc="台湾娱乐",
        title_tc="臺灣娛樂",
        author_sc="作者甲",
        author_tc="作者甲",
        category_sc="现代情感",
        category_tc="現代情感",
    ),
    SearchDocument(
        novel_id="002",
        title_sc="重生在台湾",
        title_tc="重生在臺灣",
        author_sc="作者乙",
        author_tc="作者乙",
        category_sc="台湾言情",
        category_tc="臺灣言情",
    ),
    SearchDocument(
        novel_id="003",
        title_sc="我们台湾这些",
        title_tc="我們臺灣這些",
        author_sc="作者丙",
        author_tc="作者丙",
        category_sc="其他言情",
        category_tc="其他言情",
    ),
    SearchDocument(
        novel_id="004",
        title_sc="台湾甜心",
        title_tc="臺灣甜心",
        author_sc="作者丁",
        author_tc="作者丁",
        category_sc="现代情感",
        category_tc="現代情感",
    ),
    SearchDocument(
        novel_id="005",
        title_sc="失婚",
        title_tc="失婚",
        author_sc="作者戊",
        author_tc="作者戊",
        category_sc="现代情感",
        category_tc="現代情感",
    ),
]


def test_exact_match():
    results = fuzzy_search("失婚", DOCUMENTS)
    assert results[0]["novel_id"] == "005"
    assert results[0]["match_type"] == "keyword"


def test_keyword_search_returns_related_taiwan_titles():
    results = fuzzy_search("台湾", DOCUMENTS, limit=10)
    assert {result["novel_id"] for result in results[:4]} == {"001", "002", "003", "004"}


def test_fuzzy_match_with_typo():
    results = fuzzy_search("失昏", DOCUMENTS)
    assert any(result["novel_id"] == "005" for result in results)


def test_associative_search_matches_non_contiguous_chars():
    results = fuzzy_search("台甜", DOCUMENTS)
    assert results[0]["novel_id"] == "004"


def test_empty_query_returns_empty():
    assert fuzzy_search("", DOCUMENTS) == []


def test_limit_respected():
    results = fuzzy_search("台湾", DOCUMENTS * 3, limit=3)
    assert len(results) <= 3
