"""Unit tests for Correlation tools and CorrelationAgent behavior."""
import pytest
from backend.models import Deploy
from backend.tools.correlation import (
    MIN,
    _proximity,
    correlate_changes,
    deploys_in_window,
)

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents.correlation import CorrelationAgent
from backend.models import Alert, Deploy, Incident, IncidentStatus

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

def test_correlation_agent_grounds_confidence_in_proximity_score():
    incident = Incident(
        status=IncidentStatus.DIAGNOSED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
        ),
    )

    ctx = MagicMock()
    ctx.incident = incident
    ctx.deps.storage = MagicMock()
    ctx.incident.findings = {
        "diagnosis": {
            "summary": "5xx errors increased",
            "primary_symptom": "gateway timeout",
        }
    }

    ctx.tool = AsyncMock()
    ctx.think = AsyncMock(
        return_value=(
            {
                "probable_cause": "Recent payment-api deployment caused the regression",
                "confidence": 0.95,
                "reasoning": "The deployment occurred immediately before the errors.",
            },
            None,
        )
    )
    ctx.emit = AsyncMock()
    ctx.remember = MagicMock()

    deploy = SimpleNamespace(
        service="payment-api",
        version="v2.4.1",
        deployed_by="alice",
        commit_sha="abc123",
        rollback_target="v2.4.0",
    )

    suspect = SimpleNamespace(
        deploy=deploy,
        minutes_before=5,
        proximity_score=0.70,
    )

    with patch(
        "backend.agents.correlation.C.deploys_in_window",
        return_value=[deploy],
    ) as mock_deploys, patch(
        "backend.agents.correlation.C.correlate_changes",
        return_value=[suspect],
    ) as mock_correlate:
        asyncio.run(CorrelationAgent().execute(ctx))

    mock_deploys.assert_called_once_with(
        ctx.deps.storage,
        "payment-api",
        incident.detected_at,
    )
    mock_correlate.assert_called_once_with(
        [deploy],
        incident.detected_at,
    )

    # 0.70 + 0.15 = 0.85, so Gemini's 0.95 must be capped.
    assert incident.confidence == 0.85
    assert incident.probable_cause == (
        "Recent payment-api deployment caused the regression"
    )

    ctx.deps.storage.save_incident.assert_called_once_with(incident)
    ctx.remember.assert_called_once()

    findings = ctx.remember.call_args.args
    assert findings[0] == "correlation"
    assert findings[1]["suspect"]["version"] == "v2.4.1"
    assert findings[1]["ranked"][0]["proximity_score"] == 0.70

    ctx.emit.assert_awaited_once_with(
        "correlation_result",
        agent="Correlation",
        probable_cause=incident.probable_cause,
        confidence=0.85,
    )

def test_correlation_agent_caps_confidence_when_no_recent_deploys():
    incident = Incident(
        status=IncidentStatus.DIAGNOSED,
        service="payment-api",
        alert=Alert(
            alert="High error rate detected",
            service="payment-api",
            error_rate="25%",
        ),
    )

    ctx = MagicMock()
    ctx.incident = incident
    ctx.deps.storage = MagicMock()
    ctx.incident.findings = {
        "diagnosis": {
            "summary": "5xx errors increased",
            "primary_symptom": "gateway timeout",
        }
    }

    ctx.tool = AsyncMock()
    ctx.think = AsyncMock(
        return_value=(
            {
                "probable_cause": "Possible external dependency failure",
                "confidence": 0.95,
                "reasoning": "No recent deployment explains the incident.",
            },
            None,
        )
    )
    ctx.emit = AsyncMock()
    ctx.remember = MagicMock()

    with patch(
        "backend.agents.correlation.C.deploys_in_window",
        return_value=[],
    ) as mock_deploys, patch(
        "backend.agents.correlation.C.correlate_changes",
        return_value=[],
    ) as mock_correlate:
        asyncio.run(CorrelationAgent().execute(ctx))

    mock_deploys.assert_called_once()
    mock_correlate.assert_called_once_with(
        [],
        incident.detected_at,
    )

    # Model says 0.95, but with no recent deployment
    # deterministic correlation caps confidence at 0.2.
    assert incident.confidence == 0.2
    assert incident.probable_cause == "Possible external dependency failure"

    findings = ctx.remember.call_args.args[1]
    assert findings["suspect"] is None
    assert findings["ranked"] == []
    assert findings["confidence"] == 0.2

    ctx.deps.storage.save_incident.assert_called_once_with(incident)