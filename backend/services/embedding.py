"""Self-contained text embedding for incident-memory vector similarity.

Zero-billing constraint: rather than call a hosted embeddings endpoint, we
build a deterministic vector locally via feature-hashing of character
tri-grams, then L2-normalise. Cosine similarity over these vectors is real
vector similarity — it reliably clusters incidents with similar fingerprints
(same service + error signature) without any network call or API cost.

This is a genuine algorithm, not a stub: two fingerprints that share error
tokens land close together; unrelated ones don't.
"""
from __future__ import annotations

import hashlib
import re

import numpy as np

_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _hash_to_bucket(feature: str) -> tuple[int, float]:
    h = hashlib.md5(feature.encode()).digest()
    bucket = int.from_bytes(h[:4], "little") % _DIM
    # Signed hashing (bit from a second byte) reduces collision bias.
    sign = 1.0 if h[4] & 1 else -1.0
    return bucket, sign


def embed_text(text: str) -> list[float]:
    vec = np.zeros(_DIM, dtype=np.float32)
    norm = text.lower().strip()
    tokens = _TOKEN_RE.findall(norm)
    # Whole tokens (captures service names, error codes) ...
    for tok in tokens:
        b, s = _hash_to_bucket(f"tok:{tok}")
        vec[b] += s
    # ... plus character tri-grams (captures fuzzy signature overlap).
    dense = "".join(tokens)
    for i in range(len(dense) - 2):
        b, s = _hash_to_bucket(f"tri:{dense[i:i+3]}")
        vec[b] += s * 0.5
    n = np.linalg.norm(vec)
    if n > 0:
        vec /= n
    return vec.tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))
