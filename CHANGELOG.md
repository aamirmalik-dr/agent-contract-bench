# Changelog

All live results were produced through OpenRouter at temperature 0. Counts are completed tasks over episodes on held-out families. Every version keeps the full history of saved outputs and event logs.

## 0.2.0 (2026-11-10)

v2 round, pre-registered in `docs/v2_preregistration.md`:
- Agent prompt `contract-prompts-v3` adds one rule: find documents with search, and read only paths that search returned.
- New held-out family `heldout3`, written after the rule was frozen.
- Live results with `openai/gpt-4.1-mini`, 10 tasks x 2 runs:
  - guarded agent 17/20 (5/20 in v1 pilot 2)
  - naive agent 17/20
  - single-call planner 2/20
  - rule-based baseline 20/20
  - Zero executed forbidden effects in every arm.
- Live results with `anthropic/claude-sonnet-4.6`, 1 run: guarded agent 10/10.

Figures:
- `scripts/make_figures.py` draws every figure from saved task results, event logs and traces only.
- The current set is publication-style: Figure 1 (completion and executed-effect rates, flagged-call accounting, completion by fault family, v1 to v2 dumbbell), Figure S1 (agent failure modes) and Figure S2 (tokens per episode).
  - Exported as SVG and 300 dpi PNG.
  - Wilson intervals; paired task-level bootstrap for differences.
  - Every plotted value is in `docs/figures/figure_stats.json`.
  - The typeface, Liberation Sans, is vendored under the SIL Open Font License.
- README:
  - "Main result" section, Statistical methods paragraph and a Mermaid architecture diagram.
  - Text corrections to match the event logs. No result number changed.
    - In v1 pilot 2, 12 and 13 episodes read a guessed path, and 9 and 10 of those then abstained.
    - The v2 single-call planner had 6 flagged calls, not 4. The extra 2 were exports without a token on the ambiguous task.
- `tests/test_figures.py` checks that the figures are produced, stay within size limits and are reproducible.

## 0.1.0 (2026-11-09)

- agentcore 0.1.2, vendored from evidence-ledger-agent, with contract error codes added.
- Components:
  - an in-memory virtual workspace;
  - a contract monitor (path, budget and approval checks);
  - deterministic fault injection: timeouts before and after commit, rate limits, duplicate search hits;
  - a hash-chained event log with replay;
  - a grader that re-derives forbidden effects from the environment.
- Arms:
  - guarded agent (gates enforced);
  - naive agent (gates only logged);
  - single-call planner;
  - rule-based baseline.
- Suites: dev and held-out families, with fixture scripts and tests.
- Live pilots with `openai/gpt-4.1-mini`, 10 tasks x 2 runs each.
  - Pilot 1 (`heldout`): guarded 9/20, naive 11/20, single call 10/20, rule-based 20/20.
  - Two harness flaws found in pilot 1 were fixed:
    - idempotency-key reuse with a different payload is now a typed error;
    - search weights terms by inverse document frequency.
  - The pilot-1 family then retired to development.
  - Pilot 2 (`heldout2`): guarded 5/20, naive 4/20, single call 2/20, rule-based 20/20.
- Safety: only the naive agent executed forbidden effects (2 and 3 calls past the contract budget).
- Design, threat-model, grading, replay and data documents.
