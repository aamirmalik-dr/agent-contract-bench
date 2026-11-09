# Data

This project uses no external dataset and downloads nothing. Every document, record, task, fault schedule and expected outcome is original synthetic text written for the benchmark and labelled fictional:

- `suites/dev/`, `suites/heldout/` (retired to development after the first live pilot) and `suites/heldout2/`: tasks and documents shown to agents.
- `evaluator/`: hidden fault schedules and expected outcomes, read only by the runner and grader.

`scripts/build_suites.py` regenerates both from the definitions in that file. The files under `/private` in the suites hold placeholder strings, not secrets.
