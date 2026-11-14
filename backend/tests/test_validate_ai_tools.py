"""Exercise the acceptance script with real business writes and a local agent.

No model network call: these tests validate lifecycle orchestration, not DeepSeek.
"""

import pytest

from app.platform import LocalSimulator
from scripts import validate_ai_tools as validator


@pytest.mark.parametrize("automatic", [False, True], ids=["manual", "automatic"])
def test_full_acceptance_handles_both_approval_paths(tmp_path, monkeypatch, automatic):
    private_config = tmp_path / ".env"
    private_config.write_text(
        f"AUTO_APPROVAL_ENABLED={str(automatic).lower()}\n", encoding="utf-8"
    )
    monkeypatch.setattr(validator, "DeepSeekPlatform", lambda *a, **kw: LocalSimulator())
    report = validator.validate(live=False, env_file=private_config, full=True)

    assert report["passed"], report.get("failure_type", report.get("failure"))
    assert report["incident_count"] == 1
    assert report["final_incident_status"] == "RESOLVED"
    assert report["turns"][-1]["job"]["result"]["business_state"] == "DONE"
    approval_step = "automatic_approval" if automatic else "approval"
    approvals = [step[approval_step] for step in report["steps"] if approval_step in step]
    assert len(approvals) == 1
    assert approvals[0]["status"] == "APPROVED_PENDING_ACK"
    acceptances = [step["driver_acceptance"] for step in report["steps"] if "driver_acceptance" in step]
    assert len(acceptances) == 1
    assert acceptances[0]["status"] == "ACTIVE"
