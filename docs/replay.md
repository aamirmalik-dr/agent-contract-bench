# Event logs and replay

Every episode writes one JSONL event log: a header line (`task_id`, `family`, `arm`, `run`, `suite_version`, `status`) followed by one event per call that reached the harness. Calls refused earlier by agentcore (unknown tool, invalid arguments) appear in the agent trace, not in the event log, because they never reached the environment.

Each event carries `prev_hash` and `hash`, where `hash = sha256(prev_hash + canonical JSON of the event body)` and the first `prev_hash` is 64 zeros.

## Validation

`validate_events` checks, without re-running anything:

- sequence numbers are 1, 2, 3, ... with no gap;
- steps never go backwards;
- each `prev_hash` equals the previous event's `hash`;
- each `hash` matches the event's contents.

An edited argument, a removed event or a reordered pair breaks the chain. A truncated last line fails to parse and the log is rejected. The evaluation writes `trace_validation.json` with the result for every saved log.

## Replay

`python -m agent_contract_bench replay --trace <log>` rebuilds the task's environment, monitor and fault schedule for the arm named in the header, re-issues every recorded call with its recorded step and arguments, and compares tool, violations, enforced gates, decision, fault, executed, transition and error event by event. Every difference is printed as a divergence and the exit code is 1. Nothing is repaired: the recorded log is the evidence, and replay only says whether the current code reproduces it.

Replaying a naive-arm log under the guarded arm's gates is an easy way to see divergence: the calls that were logged only are now blocked.

```bash
python -m agent_contract_bench demo --mode fixture          # writes reports/example_events.jsonl
python -m agent_contract_bench replay                       # validates and replays it
```
