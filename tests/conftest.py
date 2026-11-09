from __future__ import annotations

from pathlib import Path

import pytest

from agent_contract_bench.runner import Suite, load_suite
from agent_contract_bench.schemas import Task

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def dev() -> Suite:
    return load_suite(ROOT, "dev")


@pytest.fixture(scope="session")
def heldout() -> Suite:
    return load_suite(ROOT, "heldout")


def task(suite: Suite, task_id: str) -> Task:
    return next(t for t in suite.tasks if t.task_id == task_id)
