"""Search helpers for indexed novel metadata."""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from app.converter import to_simplified


@dataclass(frozen=True, slots=True)
class SearchDocument:
    novel_id: str
    title_sc: str
    title_tc: str
    author_sc: str
    author_tc: str
    category_sc: str
    category_tc: str

    @property
    def search_blob(self) -> str:
        return " ".join(
            part
            for part in (
                self.title_sc,
                self.title_tc,
                self.author_sc,
                self.author_tc,
                self.category_sc,
                self.category_tc,
            )
            if part
        )


def fuzzy_search(
    query: str,
    documents: list[SearchDocument],
    limit: int = 20,
    score_cutoff: int = 45,
) -> list[dict]:
    """Rank search results using keyword, associative, and fuzzy matching."""

    raw_query = query.strip()
    normalized_query = to_simplified(raw_query)
    if not normalized_query or not documents:
        return []

    scored_results: list[dict] = []
    for document in documents:
        score, match_type = _score_document(raw_query, normalized_query, document)
        if score < score_cutoff:
            continue
        scored_results.append(
            {
                "novel_id": document.novel_id,
                "title": document.title_sc,
                "score": score,
                "match_type": match_type,
            }
        )

    scored_results.sort(key=lambda result: (-result["score"], result["title"], result["novel_id"]))
    return scored_results[:limit]


def _score_document(
    raw_query: str,
    normalized_query: str,
    document: SearchDocument,
) -> tuple[float, str]:
    keyword_score = _keyword_score(raw_query, normalized_query, document)
    associative_score = _associative_score(normalized_query, document)
    fuzzy_score = _fuzzy_score(normalized_query, document)

    candidates = [
        (keyword_score, "keyword"),
        (associative_score, "associative"),
        (fuzzy_score, "fuzzy"),
    ]
    return max(candidates, key=lambda item: item[0])


def _keyword_score(raw_query: str, normalized_query: str, document: SearchDocument) -> float:
    title_length = max(len(document.title_sc), 1)
    if normalized_query == document.title_sc or raw_query == document.title_tc:
        return 220.0
    if normalized_query in document.title_sc or (document.title_tc and raw_query in document.title_tc):
        coverage = min(len(normalized_query) / title_length, 1.0)
        return 180.0 + (coverage * 20.0)
    if normalized_query in document.author_sc or raw_query in document.author_tc:
        return 155.0
    if normalized_query in document.category_sc or raw_query in document.category_tc:
        return 145.0
    if normalized_query in document.search_blob:
        return 140.0
    return 0.0


def _associative_score(normalized_query: str, document: SearchDocument) -> float:
    if _is_subsequence(normalized_query, document.title_sc):
        return 128.0 + min(float(len(normalized_query)), 12.0)
    if _all_chars_present(normalized_query, document.title_sc):
        return 118.0 + (_overlap_ratio(normalized_query, document.title_sc) * 10.0)
    if _is_subsequence(normalized_query, document.search_blob):
        return 105.0 + min(float(len(normalized_query)), 10.0)
    return 0.0


def _fuzzy_score(normalized_query: str, document: SearchDocument) -> float:
    return max(
        fuzz.WRatio(normalized_query, document.title_sc),
        fuzz.partial_ratio(normalized_query, document.title_sc),
        fuzz.WRatio(normalized_query, document.search_blob),
        fuzz.partial_ratio(normalized_query, document.search_blob),
    )


def _is_subsequence(query: str, text: str) -> bool:
    if not query:
        return False
    iterator = iter(text)
    return all(char in iterator for char in query)


def _all_chars_present(query: str, text: str) -> bool:
    if not query:
        return False
    return all(char in text for char in query)


def _overlap_ratio(query: str, text: str) -> float:
    if not query:
        return 0.0
    matched = sum(1 for char in query if char in text)
    return matched / len(query)
