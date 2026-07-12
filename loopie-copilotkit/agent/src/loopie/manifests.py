"""Immutable run manifests and authoritative artifact read sets.

Redis is sampled exactly once at run start.  LangGraph nodes receive only the
materialized values below, so an artifact mutation cannot change an in-flight
decision or make its replay evidence ambiguous.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.loopie.taxonomy import DEFAULT_ACTIONS, parse_taxonomy

DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-000000000001"
SCORER_VERSION = "v2"
KNOWN_MEMORY_KEYS = ("policy:refund_window", "policy:vat_reverse_charge")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def snapshot_content_hash(value: Any) -> str:
    return _content_hash(value)


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    key: str
    version: str
    content_json: str
    content_hash: str

    @classmethod
    def capture(cls, key: str, value: Any, *, version: str | int) -> "ArtifactSnapshot":
        return cls(
            key=key,
            version=str(version),
            content_json=_canonical_json(value),
            content_hash=_content_hash(value),
        )

    def value(self) -> Any:
        """Return a fresh value so callers cannot mutate the manifest."""
        return json.loads(self.content_json)


@dataclass(frozen=True, slots=True)
class RunManifest:
    id: str
    project_id: str
    ticket_id: str
    ticket_version: int
    ticket_snapshot: dict[str, Any]
    ticket_content_hash: str
    evaluation_snapshot: dict[str, Any] | None
    scorer_version: str
    artifacts: tuple[ArtifactSnapshot, ...]
    prompt_version: str
    schema_version: str
    model_version: str
    tool_version: str
    code_version: str
    created_at: str

    @property
    def content_hash(self) -> str:
        return _content_hash(
            {
                "project_id": self.project_id,
                "ticket_id": self.ticket_id,
                "ticket_version": self.ticket_version,
                "ticket_content_hash": self.ticket_content_hash,
                "evaluation_snapshot": self.evaluation_snapshot,
                "scorer_version": self.scorer_version,
                "artifacts": [
                    {"key": item.key, "version": item.version, "hash": item.content_hash}
                    for item in self.artifacts
                ],
                "prompt_version": self.prompt_version,
                "schema_version": self.schema_version,
                "model_version": self.model_version,
                "tool_version": self.tool_version,
                "code_version": self.code_version,
            }
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "ticket_id": self.ticket_id,
            "ticket_version": self.ticket_version,
            "ticket_snapshot": self.ticket_snapshot,
            "ticket_content_hash": self.ticket_content_hash,
            "evaluation_snapshot": self.evaluation_snapshot,
            "scorer_version": self.scorer_version,
            "artifacts": [
                {
                    "key": item.key,
                    "version": item.version,
                    "content_hash": item.content_hash,
                    "value": item.value(),
                }
                for item in self.artifacts
            ],
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "model_version": self.model_version,
            "tool_version": self.tool_version,
            "code_version": self.code_version,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class ReadSetEntry:
    key: str
    version: str
    content_hash: str

    def to_record(self) -> dict[str, str]:
        return {"key": self.key, "version": self.version, "content_hash": self.content_hash}


@dataclass(slots=True)
class ManifestReader:
    manifest: RunManifest
    _reads: dict[str, ReadSetEntry] = field(default_factory=dict, init=False)

    def read(self, key: str) -> Any:
        for item in self.manifest.artifacts:
            if item.key == key:
                self._reads[key] = ReadSetEntry(key, item.version, item.content_hash)
                return item.value()
        raise KeyError(f"Artifact {key!r} is absent from run manifest {self.manifest.id}")

    def legacy_artifacts(self) -> dict[str, Any]:
        memory: dict[str, Any] = {}
        for snapshot in self.manifest.artifacts:
            if snapshot.key.startswith("memory:"):
                document = self.read(snapshot.key)
                memory[snapshot.key.removeprefix("memory:")] = document.get("value", "")
        return {
            "memory": memory,
            "routing_rules": self.read("routing:rules"),
            "policy_rules": self.read("policy:rules"),
            "max_transitions": int(self.read("config:max_transitions")),
            "action_taxonomy": self.read("config:action_taxonomy"),
        }

    def read_set(self) -> list[dict[str, str]]:
        return [self._reads[key].to_record() for key in sorted(self._reads)]

    def ticket_input(self) -> dict[str, Any]:
        """Return the immutable agent input captured when the run was admitted."""
        facts = dict(self.manifest.ticket_snapshot.get("facts") or {})
        return {
            "case_id": self.manifest.ticket_snapshot["external_id"],
            "version": int(self.manifest.ticket_snapshot.get("version", 1)),
            "request": self.manifest.ticket_snapshot["body"],
            **facts,
        }


def build_ticket_snapshot(ticket: dict[str, Any]) -> dict[str, Any]:
    """Normalize persisted tickets and fixture inputs into one immutable shape."""
    metadata = dict(ticket.get("metadata") or {})
    facts = dict(ticket.get("facts") or {})
    for key in (
        "customer_tier",
        "days_since_purchase",
        "security_flag",
        "amount_minor",
        "currency",
        "amount_source",
        "amount",
        "must_check_policy_version",
    ):
        if key not in facts and key in ticket:
            facts[key] = ticket[key]
        if key not in facts and key in metadata:
            facts[key] = metadata[key]
    if facts.get("amount_minor") is not None and "amount" not in facts:
        facts["amount"] = int(facts["amount_minor"]) / 100
    if facts.get("amount") is not None and facts.get("amount_minor") is None:
        facts["amount_minor"] = round(float(facts["amount"]) * 100)
        facts.setdefault("currency", "USD")
        facts.setdefault("amount_source", "explicit")
    external_id = str(ticket.get("external_id") or ticket.get("case_id"))
    body = str(ticket.get("body") or ticket.get("request") or "")
    return {
        "id": str(ticket["id"]) if ticket.get("id") else None,
        "external_id": external_id,
        "version": int(ticket.get("version", 1)),
        "subject": str(ticket.get("subject") or body[:120]),
        "body": body,
        "channel": str(ticket.get("channel") or "fixture"),
        "customer_ref": ticket.get("customer_ref"),
        "facts": facts,
        "metadata": metadata,
        "tags": list(ticket.get("tags") or []),
    }


def build_run_manifest(
    redis: Any,
    ticket: dict[str, Any],
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    prompt_version: str,
    schema_version: str,
    model_version: str,
    tool_version: str = "v1",
    code_version: str | None = None,
    evaluation_snapshot: dict[str, Any] | None = None,
) -> RunManifest:
    """Materialize every artifact a run may read, before graph execution."""
    memories = {
        key: redis.get_memory(key)
        for key in KNOWN_MEMORY_KEYS
    }
    memories["policy:refund_window"] = memories["policy:refund_window"] or {
        "value": "",
        "version": 1,
    }
    routing_rules = redis.get_routing_rules()
    policy_rules = redis.get_policy_rules()
    max_transitions = int(redis.get_config("max_transitions", "6") or "6")
    action_taxonomy = list(
        parse_taxonomy(
            redis.get_config(
                "action_taxonomy",
                _canonical_json(list(DEFAULT_ACTIONS)),
            )
        )
    )
    snapshots = (
        *(
            ArtifactSnapshot.capture(
                f"memory:{key}",
                memory,
                version=int(memory.get("version", 1)),
            )
            for key, memory in memories.items()
            if memory is not None
        ),
        ArtifactSnapshot.capture(
            "routing:rules",
            routing_rules,
            version=f"sha256:{_content_hash(routing_rules)[:16]}",
        ),
        ArtifactSnapshot.capture(
            "policy:rules",
            policy_rules,
            version=f"sha256:{_content_hash(policy_rules)[:16]}",
        ),
        ArtifactSnapshot.capture(
            "config:max_transitions",
            max_transitions,
            version=f"sha256:{_content_hash(max_transitions)[:16]}",
        ),
        ArtifactSnapshot.capture(
            "config:action_taxonomy",
            action_taxonomy,
            version=f"sha256:{_content_hash(action_taxonomy)[:16]}",
        ),
    )
    ticket_snapshot = build_ticket_snapshot(ticket)
    ticket_content_hash = _content_hash(ticket_snapshot)
    return RunManifest(
        id=str(uuid.uuid4()),
        project_id=project_id,
        ticket_id=ticket_snapshot["external_id"],
        ticket_version=int(ticket_snapshot["version"]),
        ticket_snapshot=ticket_snapshot,
        ticket_content_hash=ticket_content_hash,
        evaluation_snapshot=evaluation_snapshot,
        scorer_version=SCORER_VERSION,
        artifacts=snapshots,
        prompt_version=prompt_version,
        schema_version=schema_version,
        model_version=model_version,
        tool_version=tool_version,
        code_version=code_version or os.getenv("RENDER_GIT_COMMIT", "local"),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
