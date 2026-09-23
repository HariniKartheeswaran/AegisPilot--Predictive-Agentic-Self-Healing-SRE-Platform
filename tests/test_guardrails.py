"""Unit tests for Guardrails: Approval Gate and PII Scrubber."""
import asyncio
import pytest
from backend.guardrails import ApprovalDecision, ApprovalGate, scrub_pii


def test_approval_gate_resolve_flow():
    """Verify standard open -> resolve handshake."""
    async def _run():
        gate = ApprovalGate()
        inc_id = "inc_gate_test_1"

        assert gate.is_open(inc_id) is False
        gate.open_gate(inc_id)
        assert gate.is_open(inc_id) is True

        # Resolving gate
        decision = ApprovalDecision(approved=True, approver="alice@sre", note="LGTM")
        resolved = gate.resolve(inc_id, decision)
        assert resolved is True
        assert gate.is_open(inc_id) is False

        # Second resolution must fail (atomic resolution)
        second = gate.resolve(inc_id, ApprovalDecision(approved=False, approver="bob@sre"))
        assert second is False

        # wait_for returns immediately because event is set
        res = await gate.wait_for(inc_id, timeout=1.0)
        assert res.approved is True
        assert res.approver == "alice@sre"

    asyncio.run(_run())


def test_approval_gate_timeout_fails_safe():
    """Verify that a timeout fails safe and never auto-approves."""
    async def _run():
        gate = ApprovalGate()
        inc_id = "inc_gate_timeout"
        gate.open_gate(inc_id)

        # Short timeout to simulate lack of response
        decision = await gate.wait_for(inc_id, timeout=0.05)
        assert decision.approved is False
        assert decision.approver == "system"
        assert "timed out" in decision.note

    asyncio.run(_run())


def test_approval_gate_unopened_raises():
    """Verify wait_for on nonexistent gate raises KeyError."""
    async def _run():
        gate = ApprovalGate()
        with pytest.raises(KeyError):
            await gate.wait_for("inc_nonexistent", timeout=0.1)

    asyncio.run(_run())


def test_pii_scrubber():
    """Verify PII scrubber redacts credentials, cards, tokens, emails, and phone numbers."""
    raw = (
        "Contact dev at alice@corp.com or 555-123-4567. "
        "Failed token: ghp_1234567890abcdef. "
        "Customer IP 192.168.1.1 used card 4111-2222-3333-4444."
    )
    res = scrub_pii(raw)
    assert res.redactions >= 4
    assert "alice@corp.com" not in res.text
    assert "[REDACTED_EMAIL]" in res.text
    assert "192.168.1.1" not in res.text
    assert "[REDACTED_IP]" in res.text
    assert "ghp_1234567890abcdef" not in res.text
    assert "[REDACTED_TOKEN]" in res.text


def test_pii_scrubber_clean_text():
    """Verify clean text passes through without alteration."""
    clean = "Everything is operational and within normal latency."
    res = scrub_pii(clean)
    assert res.redactions == 0
    assert res.text == clean
