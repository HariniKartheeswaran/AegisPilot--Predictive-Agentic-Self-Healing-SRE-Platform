
import math

import pytest

from backend.services.embedding import (
    _DIM,
    _hash_to_bucket,
    cosine_similarity,
    embed_text,
)


def test_hash_to_bucket_returns_valid_bucket_and_signed_value():
    bucket, sign = _hash_to_bucket("tok:database")

    assert 0 <= bucket < _DIM
    assert sign in (-1.0, 1.0)


def test_hash_to_bucket_is_deterministic():
    first = _hash_to_bucket("tok:database")
    second = _hash_to_bucket("tok:database")

    assert first == second


def test_embed_text_returns_fixed_dimension():
    vector = embed_text("database connection timeout")

    assert len(vector) == _DIM
    assert all(isinstance(value, float) for value in vector)


def test_embed_text_is_deterministic():
    first = embed_text("database connection timeout")
    second = embed_text("database connection timeout")

    assert first == second


def test_embed_text_is_l2_normalized():
    vector = embed_text("database connection timeout")

    norm = math.sqrt(sum(value * value for value in vector))

    assert norm == pytest.approx(1.0, abs=1e-6)


def test_embed_text_normalizes_case_and_whitespace():
    first = embed_text("  Database CONNECTION timeout  ")
    second = embed_text("database connection timeout")

    assert first == second


def test_embed_empty_text_returns_zero_vector():
    vector = embed_text("")

    assert len(vector) == _DIM
    assert all(value == 0.0 for value in vector)


def test_embed_whitespace_only_returns_zero_vector():
    vector = embed_text("   ")

    assert len(vector) == _DIM
    assert all(value == 0.0 for value in vector)


def test_embed_text_uses_tokens_and_character_trigrams():
    token_only = embed_text("abc")
    longer_text = embed_text("abc xyz")

    assert any(value != 0.0 for value in token_only)
    assert any(value != 0.0 for value in longer_text)
    assert token_only != longer_text


def test_cosine_similarity_identical_vectors_is_one():
    vector = embed_text("database connection timeout")

    similarity = cosine_similarity(vector, vector)

    assert similarity == pytest.approx(1.0, abs=1e-6)


def test_cosine_similarity_is_symmetric():
    first = embed_text("database connection timeout")
    second = embed_text("database connection refused")

    assert cosine_similarity(first, second) == pytest.approx(
        cosine_similarity(second, first),
        abs=1e-6,
    )


def test_cosine_similarity_returns_zero_for_zero_vector():
    vector = embed_text("database connection timeout")
    zero = [0.0] * _DIM

    assert cosine_similarity(vector, zero) == 0.0
    assert cosine_similarity(zero, vector) == 0.0


def test_cosine_similarity_of_two_zero_vectors_is_zero():
    zero = [0.0] * _DIM

    assert cosine_similarity(zero, zero) == 0.0


def test_cosine_similarity_returns_valid_range_for_normalized_embeddings():
    first = embed_text("database connection timeout")
    second = embed_text("database connection refused")

    similarity = cosine_similarity(first, second)

    assert -1.0 <= similarity <= 1.0


def test_similar_incident_fingerprints_have_positive_similarity():
    first = embed_text("payment-service database connection timeout")
    second = embed_text("payment-service database connection timeout retry")

    similarity = cosine_similarity(first, second)

    assert similarity > 0.0


def test_different_incident_fingerprints_are_not_identical():
    first = embed_text("payment-service database connection timeout")
    second = embed_text("frontend image processing failure")

    assert first != second
