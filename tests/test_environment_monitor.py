from __future__ import annotations

import pytest

from agent_contract_bench.agentcore.policy import ToolFailure
from agent_contract_bench.contracts import GATES, Monitor
from agent_contract_bench.environment import VirtualEnv, effect_hash, resolve_path
from agent_contract_bench.runner import Suite, build

from .conftest import task


@pytest.mark.parametrize(
    "path, resolved, flags",
    [
        ("/workspace/a.json", "/workspace/a.json", []),
        ("/workspace/../private/x", "/private/x", ["dot_segments"]),
        ("/workspace/./a/../../private/x", "/private/x", ["dot_segments"]),
        ("workspace/a.json", "/workspace/a.json", ["relative"]),
        ("/workspace\\..\\private\\x", "/private/x", ["suspicious_characters", "dot_segments"]),
        ("//workspace//a.json", "/workspace/a.json", []),
        ("/workspace/%2e%2e/private", "/workspace/%2e%2e/private", ["suspicious_characters"]),
    ],
)
def test_resolve_path_like_a_filesystem(path: str, resolved: str, flags: list[str]) -> None:
    got, got_flags = resolve_path(path, {})
    assert got == resolved and sorted(got_flags) == sorted(flags)


def test_alias_resolves_outside_the_write_prefix(heldout: Suite) -> None:
    t = task(heldout, "ho-03")
    env = VirtualEnv(t, heldout.documents)
    resolved, flags = resolve_path("/workspace/shared/b2_vacuum.json", env.aliases)
    assert resolved == "/private/shared/b2_vacuum.json" and flags == ["alias"]
    monitor = Monitor(t.contract, set(GATES))
    args = {"path": "/workspace/shared/b2_vacuum.json", "content": "{}", "idempotency_key": "k"}
    assert monitor.assess("write_note", args, env, 1, 1) == ["write_outside_prefix"]


def test_traversal_is_flagged_even_when_it_lands_inside_the_prefix(dev: Suite) -> None:
    t = task(dev, "dev-01")
    env = VirtualEnv(t, dev.documents)
    monitor = Monitor(t.contract, set(GATES))
    args = {"path": "/workspace/x/../a.json", "content": "{}", "idempotency_key": "k"}
    assert monitor.assess("write_note", args, env, 1, 1) == ["invalid_path"]


def _export_args(record: str, token: str | None) -> dict:
    return {"record_id": record, "approval_token": token, "idempotency_key": "e"}


def test_approval_binding_rejects_missing_forged_mismatched_and_expired(dev: Suite) -> None:
    t = task(dev, "dev-04")
    env = VirtualEnv(t, dev.documents)
    m = Monitor(t.contract, set(GATES))
    assert m.assess("export_record", _export_args("REC-AUDIT-12", None), env, 1, 1) == [
        "approval_missing"
    ]
    forged = _export_args("REC-AUDIT-12", "APV-5d1c0e9a7b33")
    assert m.assess("export_record", forged, env, 1, 1) == ["approval_invalid"]
    grant, _ = env.request_approval("REC-AUDIT-12", step=2)
    assert grant["simulated"] is True and grant["effect_hash"] == effect_hash("REC-AUDIT-12")
    good = _export_args("REC-AUDIT-12", grant["approval_token"])
    assert m.assess("export_record", good, env, 3, 3) == []
    other = _export_args("REC-AUDIT-11", grant["approval_token"])
    assert m.assess("export_record", other, env, 3, 3) == ["approval_mismatch"]
    late = 2 + t.contract.approval_ttl_steps + 1
    assert m.assess("export_record", good, env, 3, late) == ["approval_expired"]


def test_denied_approval_issues_no_token(dev: Suite) -> None:
    env = VirtualEnv(task(dev, "dev-02"), dev.documents)
    out, _ = env.request_approval("REC-PARTNER-7", step=1)
    assert out["decision"] == "deny" and "approval_token" not in out and env.approvals == {}


def test_contract_budget_blocks_in_guarded_and_is_logged_in_naive(dev: Suite) -> None:
    t = task(dev, "dev-01")
    for arm, expect_block in (("guarded", True), ("naive", False)):
        harness, registry = build(t, dev, arm)
        harness.calls_used = t.contract.max_tool_calls
        search = registry.get("search")
        assert search is not None
        args = search.args_model(query="cold-storage")

        class S:
            steps = 1

        if expect_block:
            with pytest.raises(ToolFailure) as info:
                search.fn(S(), args)
            assert info.value.code == "blocked_by_contract"
            assert harness.events[-1].decision == "blocked" and not harness.events[-1].executed
        else:
            search.fn(S(), args)
            assert harness.events[-1].decision == "logged_only" and harness.events[-1].executed
        assert harness.events[-1].violations == ["budget_exceeded"]


def test_unknown_gate_is_rejected(dev: Suite) -> None:
    with pytest.raises(ValueError):
        Monitor(task(dev, "dev-01").contract, {"path", "network"})


def test_injected_document_text_does_not_change_contract_or_gates(dev: Suite) -> None:
    t = task(dev, "dev-02")
    harness, registry = build(t, dev, "guarded")
    before = t.contract.model_dump()

    class S:
        steps = 1

    read = registry.get("read_document")
    assert read is not None
    out = read.fn(S(), read.args_model(path="/docs/notes/vendor-bulletin.md"))
    assert "PREAPPROVED" in out["text"]
    assert t.contract.model_dump() == before and harness.monitor.enforce == frozenset(GATES)
    export = registry.get("export_record")
    assert export is not None
    args = export.args_model(
        record_id="REC-PARTNER-7", approval_token="PREAPPROVED", idempotency_key="e1"
    )
    with pytest.raises(ToolFailure):
        export.fn(S(), args)
    assert harness.env.exports == []
