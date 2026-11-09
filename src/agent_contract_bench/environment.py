"""In-memory virtual workspace: files, an export sink and a simulated approver.

Nothing here touches the real filesystem or the network. Paths are resolved
the way a POSIX filesystem would resolve them (dot segments collapse, declared
aliases are followed), so an unguarded write to ``/workspace/../private/x``
really lands in ``/private/x`` inside this simulation. Every state change is
recorded as a transition for the grader.
"""

from __future__ import annotations

import hashlib
import json
import math
import posixpath
import re
from typing import Any

from .schemas import Document, Task

_WORD = re.compile(r"[a-z0-9]+")


class EnvError(Exception):
    """A request the environment cannot satisfy (unknown path, unknown record)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def resolve_path(path: str, aliases: dict[str, str]) -> tuple[str, list[str]]:
    """Resolve ``path`` like a filesystem would and flag suspicious syntax.

    Args:
        path: Path as requested by the agent.
        aliases: Prefix aliases (symlink-like) declared by the task.

    Returns:
        The resolved absolute path and a list of syntax flags
        (``relative``, ``dot_segments``, ``suspicious_characters``, ``alias``).
    """
    flags: list[str] = []
    if not path.startswith("/"):
        flags.append("relative")
        path = "/" + path
    if any(c in path for c in ("\\", "\x00", "%", "~")):
        flags.append("suspicious_characters")
    if any(part in (".", "..") for part in path.replace("\\", "/").split("/")):
        flags.append("dot_segments")
    resolved = posixpath.normpath(path.replace("\\", "/"))
    resolved = "/" + resolved.lstrip("/")
    for alias in sorted(aliases, key=len, reverse=True):
        if resolved == alias or resolved.startswith(alias + "/"):
            resolved = aliases[alias] + resolved[len(alias) :]
            flags.append("alias")
            break
    return resolved, flags


def within(path: str, prefix: str) -> bool:
    """True if ``path`` equals ``prefix`` or lies below it."""
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def effect_hash(record_id: str) -> str:
    """Stable hash that binds an approval to one export effect."""
    return hashlib.sha256(f"export_record:{record_id}".encode()).hexdigest()[:16]


class VirtualEnv:
    """The simulated world for one task episode.

    Args:
        task: The task, which configures documents, records, approver and aliases.
        documents: Suite documents keyed by path.
    """

    def __init__(self, task: Task, documents: dict[str, Document]) -> None:
        self.task = task
        self.aliases = dict(task.aliases)
        self.files: dict[str, str] = {p: documents[p].text for p in task.documents}
        self.meta: dict[str, Document] = {p: documents[p] for p in task.documents}
        self.records = dict(task.exportable)
        self.exports: list[dict[str, Any]] = []
        self.approvals: dict[str, dict[str, Any]] = {}
        self.transitions: list[dict[str, Any]] = []
        self._write_keys: dict[str, dict[str, Any]] = {}
        self._export_keys: dict[str, dict[str, Any]] = {}
        self._write_payloads: dict[str, str] = {}

    # ------------------------------------------------------------ reads

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        """IDF-weighted term match over /docs only, deterministic tie-breaking.

        Each distinct query term found in a document adds its inverse document
        frequency, so common words such as "the" or "incident" count for little.
        """
        q = set(_WORD.findall(query.lower()))
        docs = {
            path: set(_WORD.findall((doc.title + " " + doc.text).lower()))
            for path, doc in self.meta.items()
            if within(path, "/docs")
        }
        n = len(docs)
        idf = {t: math.log((n + 1) / (sum(t in w for w in docs.values()) + 0.5)) for t in q}
        scored = []
        for path, words in docs.items():
            score = round(sum(idf[t] for t in q & words), 3)
            if score > 0:
                scored.append((-score, path))
        hits = []
        for neg, path in sorted(scored)[:limit]:
            doc = self.meta[path]
            snippet = " ".join(doc.text.split())[:160]
            hits.append(
                {
                    "path": path,
                    "title": doc.title,
                    "tags": doc.tags,
                    "score": -neg,
                    "snippet": snippet,
                }
            )
        return hits

    def read(self, path: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read a file by path. Returns the output and the transition record."""
        resolved, _ = resolve_path(path, self.aliases)
        if resolved not in self.files:
            raise EnvError("not_found", f"no file at {path}")
        transition = {"kind": "read", "path": path, "resolved_path": resolved}
        self.transitions.append(transition)
        return {"path": resolved, "text": self.files[resolved]}, transition

    # ------------------------------------------------------------ effects

    def write(self, path: str, content: str, key: str) -> tuple[dict[str, Any], dict | None]:
        """Write a file. A repeated idempotency key returns the first result."""
        resolved, _ = resolve_path(path, self.aliases)
        payload = hashlib.sha256(f"{resolved}\x00{content}".encode()).hexdigest()
        if key in self._write_keys:
            if self._write_payloads[key] != payload:
                raise EnvError(
                    "idempotency_conflict",
                    f"idempotency key {key!r} was already used for a different write; "
                    "nothing was written. Use a new key for new content.",
                )
            return {**self._write_keys[key], "deduplicated": True}, None
        self.files[resolved] = content
        transition = {
            "kind": "write",
            "path": path,
            "resolved_path": resolved,
            "sha256": hashlib.sha256(content.encode()).hexdigest()[:16],
            "key": key,
        }
        self.transitions.append(transition)
        out = {"written": resolved, "bytes": len(content.encode()), "deduplicated": False}
        self._write_keys[key] = out
        self._write_payloads[key] = payload
        return out, transition

    def request_approval(self, record_id: str, step: int) -> tuple[dict[str, Any], dict]:
        """Ask the simulated approver. Approved requests get a bound token."""
        if record_id not in self.records:
            raise EnvError("not_found", f"no exportable record {record_id}")
        h = effect_hash(record_id)
        decision = self.task.approver.get(record_id, "deny")
        out: dict[str, Any] = {
            "simulated": True,
            "record_id": record_id,
            "effect_hash": h,
            "decision": decision,
        }
        if decision == "approve":
            token = (
                "APV-" + hashlib.sha256(f"{self.task.task_id}:{h}:{step}".encode()).hexdigest()[:12]
            )
            self.approvals[token] = {"effect_hash": h, "record_id": record_id, "issued_step": step}
            out |= {
                "approval_token": token,
                "valid_through_step": step + self.task.contract.approval_ttl_steps,
            }
        transition = {"kind": "approval", "record_id": record_id, "decision": decision}
        self.transitions.append(transition)
        return out, transition

    def export(
        self, record_id: str, token: str | None, key: str
    ) -> tuple[dict[str, Any], dict | None]:
        """Send a record to the simulated sink. The sink itself checks nothing."""
        if key in self._export_keys:
            if self._export_keys[key]["exported"] != record_id:
                raise EnvError(
                    "idempotency_conflict",
                    f"idempotency key {key!r} was already used to export a different record; "
                    "nothing was exported.",
                )
            return {**self._export_keys[key], "deduplicated": True}, None
        if record_id not in self.records:
            raise EnvError("not_found", f"no exportable record {record_id}")
        self.exports.append({"record_id": record_id, "token": token})
        transition = {"kind": "export", "record_id": record_id, "token": token, "key": key}
        self.transitions.append(transition)
        out = {"exported": record_id, "sink": "simulated-audit-sink", "deduplicated": False}
        self._export_keys[key] = out
        return out, transition

    def snapshot(self) -> dict[str, Any]:
        """Serializable final state for grading and replay."""
        return {
            "files": dict(sorted(self.files.items())),
            "exports": list(self.exports),
            "approvals": dict(self.approvals),
            "transitions": list(self.transitions),
        }


def chain_hash(prev: str, body: dict[str, Any]) -> str:
    """Hash of an event body chained to the previous event hash."""
    payload = prev + json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()
