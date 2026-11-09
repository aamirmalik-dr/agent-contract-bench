# Changes to the vendored agentcore

`src/agent_contract_bench/agentcore/` is copied from evidence-ledger-agent at agentcore 0.1.2 (commit 45f6ff8). Differences from that copy:

| File | Change | Reason |
|---|---|---|
| `schemas.py` | `ErrorCode` gains `blocked_by_contract`, `timeout` and `rate_limited` | The harness returns these as typed, recoverable errors so the agent can tell a contract block from a transient fault and retry only the latter. |
| `__init__.py` | Docstring says the module is vendored | Provenance. |

No behavioural change to the loop, policy, adapters or traces.
