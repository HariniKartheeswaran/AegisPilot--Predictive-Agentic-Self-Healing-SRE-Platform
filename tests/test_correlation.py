"""Unit tests for Correlation tools covering temporal windows, proximity decay, and edge cases."""
import pytest
from backend.models import Deploy
from backend.tools.correlation import (
    MIN,
    _proximity,
    correlate_changes,
    deploys_in_window,
)


def test_proximity_exponential_decay():
    """Verify proximity scoring respects temporal direction and decay."""
    # Deploys AFTER detection must score 0.0
    assert _proximity(-1.0) == 0.0
    assert _proximity(-10.0) == 0.0

    # Deploy at exact moment of detection ~1.0
    assert pytest.approx(_proximity(0.0), rel=1e-2) == 1.0

    # Deploy 30 minutes before detection (half-life) ~0.5
    assert pytest.approx(_proximity(30.0), rel=1e-2) == 0.5

    # Deploy 60 minutes before detection (two half-lives) ~0.25
    assert pytest.approx(_proximity(60.0), rel=1e-2) == 0.25

    # Long past deploy (>200 minutes) fades near 0
    assert _proximity(240.0) < 0.01


def test_correlate_changes_ranking():
    """Verify changes are ranked in descending order of suspiciousness."""
    detected_at = 1_000_000_000
    d_recent = Deploy(
        id="d1", service="svc", version="v2",
        deployed_at=detected_at - 10 * MIN, deployed_by="dev1", commit_sha="c1"
    )
    d_older = Deploy(
        id="d2", service="svc", version="v1",
        deployed_at=detected_at - 90 * MIN, deployed_by="dev2", commit_sha="c2"
    )
    d_after = Deploy(
        id="d3", service="svc", version="v3",
        deployed_at=detected_at + 1 * MIN, deployed_by="dev3", commit_sha="c3"
    )

    suspects = correlate_changes([d_older, d_after, d_recent], detected_at)
    assert len(suspects) == 3
    # Most suspicious must be d_recent (10 min before)
    assert suspects[0].deploy.id == "d1"
    assert suspects[0].proximity_score > suspects[1].proximity_score
    # d_after must be lowest or zero
    assert suspects[2].deploy.id == "d3"
    assert suspects[2].proximity_score == 0.0


def test_deploys_in_window_boundary_filtering(isolated_storage):
    """Verify window filtering honors the lookback limit and lookahead buffer."""
    detected_at = 500_000_000
    storage = isolated_storage

    # Deploy inside window (30 min before)
    d_in = Deploy(
        id="d_in", service="test-svc", version="v1.1",
        deployed_at=detected_at - 30 * MIN, deployed_by="ci", commit_sha="sha1"
    )
    # Deploy outside window (200 min before)
    d_out = Deploy(
        id="d_out", service="test-svc", version="v1.0",
        deployed_at=detected_at - 200 * MIN, deployed_by="ci", commit_sha="sha0"
    )
    # Deploy slightly after detection (within 5m buffer)
    d_buffer = Deploy(
        id="d_buf", service="test-svc", version="v1.2",
        deployed_at=detected_at + 2 * MIN, deployed_by="ci", commit_sha="sha2"
    )
    # Deploy far after detection (10m after)
    d_future = Deploy(
        id="d_fut", service="test-svc", version="v1.3",
        deployed_at=detected_at + 10 * MIN, deployed_by="ci", commit_sha="sha3"
    )

    for d in [d_in, d_out, d_buffer, d_future]:
        storage.add_deploy(d)

    candidates = deploys_in_window(storage, "test-svc", detected_at, lookback_min=180)
    candidate_ids = {c.id for c in candidates}

    assert "d_in" in candidate_ids
    assert "d_buf" in candidate_ids
    assert "d_out" not in candidate_ids
    assert "d_fut" not in candidate_ids


def test_correlate_empty_changes():
    """Verify empty deploy list returns empty suspects without crashing."""
    suspects = correlate_changes([], 1_000_000_000)
    assert suspects == []
