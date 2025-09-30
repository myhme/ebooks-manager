# tests/test_booklore_client.py

import pytest
from src.api.booklore_client import BookloreClient, DEFAULT_MATCHING


@pytest.mark.asyncio
async def test_best_string_match_basic():
    # identical text, different case
    score = BookloreClient.best_string_match("Harry Potter", "harry potter")
    assert 0.95 <= score <= 1.0

    # unrelated text
    score2 = BookloreClient.best_string_match("Harry Potter", "The Hobbit")
    assert score2 < 0.5

    # diacritics should normalize away
    score3 = BookloreClient.best_string_match("Café", "Cafe")
    assert score3 > 0.9


def test_score_candidate_match_id_priority():
    client = BookloreClient("http://dummy")
    gr = {"goodreads_id": "123", "title": "Book", "author": "Author"}
    bl = {"metadata": {"goodreadsId": "123", "title": "Book", "authors": ["Author"]}}
    score = client.score_candidate_match(gr, bl)
    assert score >= client.matching_config["goodreads_id"]


def test_score_candidate_match_isbn_priority():
    client = BookloreClient("http://dummy")
    gr = {"isbn": "9781234567890"}
    bl = {"metadata": {"isbn13": "9781234567890"}}
    score = client.score_candidate_match(gr, bl)
    assert score >= client.matching_config["isbn"]


def test_score_candidate_match_fuzzy_logic():
    client = BookloreClient("http://dummy")
    gr = {"title": "The Great Adventure", "author": "John Smith"}
    bl = {"metadata": {"title": "Great Adventure", "authors": ["J. Smith"]}}
    score = client.score_candidate_match(gr, bl)
    assert 0 < score < client.matching_config["goodreads_id"]


def test_find_best_match_respects_threshold():
    client = BookloreClient("http://dummy")
    gr = {"title": "Test Book", "author": "Someone"}
    bl = [{"metadata": {"title": "Completely Different", "authors": ["Another"]}}]
    match, score = client.find_best_match_for_book(gr, bl, threshold=9999)
    assert match is None
    assert isinstance(score, float)


def test_match_goodreads_against_booklore_finds_id_match():
    client = BookloreClient("http://dummy")
    gr_books = [{"goodreads_id": "111", "title": "Title1", "author": "Author1"}]
    bl_books = [{"metadata": {"goodreadsId": "111", "title": "Title1", "authors": ["Author1"]}}]
    results = client.match_goodreads_against_booklore(gr_books, bl_books)
    assert results[0]["match"] is not None
    assert results[0]["reason"] == "id"


def test_match_goodreads_against_booklore_detects_isbn_match():
    client = BookloreClient("http://dummy")
    gr_books = [{"isbn": "9780000000001", "title": "Foo"}]
    bl_books = [{"metadata": {"isbn13": "9780000000001", "title": "Bar"}}]
    results = client.match_goodreads_against_booklore(gr_books, bl_books)
    assert results[0]["match"] is not None
    assert results[0]["reason"] in ("isbn", "fuzzy")  # isbn should dominate


def test_match_goodreads_against_booklore_returns_none_for_no_match():
    client = BookloreClient("http://dummy")
    gr_books = [{"goodreads_id": "999", "title": "Unmatched", "author": "Nobody"}]
    bl_books = [{"metadata": {"title": "Completely Different", "authors": ["Someone Else"]}}]
    results = client.match_goodreads_against_booklore(gr_books, bl_books, threshold=9000)
    assert results[0]["match"] is None
    assert results[0]["reason"] == "none"


def test_matching_config_override_changes_scores():
    # override weights so fuzzy author matching has huge influence
    cfg = dict(DEFAULT_MATCHING)
    cfg["author_fuzzy"] = 5000
    client = BookloreClient("http://dummy", matching_config=cfg)

    gr = {"title": "Some Title", "author": "Johnathan Doe"}
    bl = {"metadata": {"title": "Some Title", "authors": ["J Doe"]}}

    score = client.score_candidate_match(gr, bl)
    # score should be boosted significantly
    assert score > 2000
