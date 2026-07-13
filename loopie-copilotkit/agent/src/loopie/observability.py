"""Shared Weave initialization and op decorator."""

from __future__ import annotations

import functools
import os
import re
import time
from collections.abc import Mapping
from typing import Any, Callable, Literal, TypeVar

from src.loopie.config import get_settings

F = TypeVar("F", bound=Callable[..., Any])

# Connection strings (postgres://user:pass@host, redis://:pass@host, etc.) must never
# reach the Weave dashboard — it can be shared with judges/teammates. Redact credentials
# from any DSN string and replace store objects (which carry a raw `url`) with a label.
_DSN_CREDS_RE = re.compile(r"(?P<scheme>[a-zA-Z][\w+.\-]*://)(?P<creds>[^/@\s]+)@")
_REDACTED_CLASSNAMES = {"BudgetTracker", "Ledger", "RedisStore"}
_MAX_TRACE_STRING = 600
_MAX_TRACE_ITEMS = 20
_SECRET_KEY_PARTS = {
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "passwd",
    "secret",
    "session",
    "token",
}


def _scrub_dsn(value: str) -> str:
    return _DSN_CREDS_RE.sub(lambda m: f"{m.group('scheme')}***@", value)


def _bounded_string(value: str) -> str:
    value = _scrub_dsn(value) if "://" in value else value
    if len(value) <= _MAX_TRACE_STRING:
        return value
    return f"{value[:_MAX_TRACE_STRING]}... <{len(value) - _MAX_TRACE_STRING} chars omitted>"


def _looks_secret_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    if normalized in _SECRET_KEY_PARTS or normalized.endswith("_api_key"):
        return True
    return bool(set(normalized.split("_")) & _SECRET_KEY_PARTS)


def _artifact_trace_view(artifacts: Mapping[str, Any]) -> dict[str, Any]:
    memories = artifacts.get("memories") or artifacts.get("memory") or {}
    if isinstance(memories, Mapping):
        memory_versions = {
            str(key): value.get("version") if isinstance(value, Mapping) else None
            for key, value in memories.items()
        }
    else:
        memory_versions = {}
    return {
        "artifact_hash": artifacts.get("artifact_hash")
        or artifacts.get("content_hash"),
        "memory_versions": memory_versions,
        "routing_rule_count": len(artifacts.get("routing_rules") or []),
        "policy_rule_count": len(artifacts.get("policy_rules") or []),
        "max_transitions": artifacts.get("max_transitions"),
        "action_taxonomy": list(artifacts.get("action_taxonomy") or []),
    }


def _ticket_trace_view(ticket: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "case_id",
        "request",
        "failure_seed",
        "expected_action",
        "days_since_purchase",
        "customer_tier",
        "security_flag",
        "amount",
        "amount_minor",
        "currency",
        "must_check_policy_version",
    )
    return {key: _sanitize(ticket[key]) for key in keys if key in ticket}


def _manifest_trace_view(manifest: Any) -> dict[str, Any] | str:
    if manifest is None:
        return "<none>"
    if isinstance(manifest, Mapping):
        getter = manifest.get
    else:

        def getter(key: str, default: Any = None) -> Any:
            return getattr(manifest, key, default)

    artifacts = getter("artifacts", ()) or ()
    artifact_keys = [
        str(
            item.get("key")
            if isinstance(item, Mapping)
            else getattr(item, "key", "unknown")
        )
        for item in list(artifacts)[:_MAX_TRACE_ITEMS]
    ]
    content_hash = str(getter("content_hash", ""))
    return {
        "id": getter("id"),
        "ticket_id": getter("ticket_id"),
        "content_hash": content_hash[:16] if content_hash else None,
        "artifact_keys": artifact_keys,
    }


def _run_trace_view(run: Mapping[str, Any]) -> dict[str, Any]:
    tool_calls = [
        call.get("name") if isinstance(call, Mapping) else str(call)
        for call in run.get("tool_calls") or []
    ]
    evidence_calls = [
        {
            "name": call.get("name"),
            "iteration": call.get("iteration"),
            "result_hash": call.get("result_hash"),
        }
        for call in run.get("evidence_calls") or []
        if isinstance(call, Mapping)
    ]
    audit = run.get("audit_payload") or {}
    return {
        "run_id": run.get("run_id"),
        "case_id": run.get("case_id"),
        "action": run.get("action"),
        "oracle_action": run.get("oracle_action"),
        "mode": run.get("mode"),
        "decided_by": run.get("decided_by"),
        "fallback_used": bool(run.get("fallback_used", False)),
        "stop_reason": run.get("stop_reason"),
        "tool_calls": tool_calls,
        "policy_result": audit.get("policy_result")
        if isinstance(audit, Mapping)
        else None,
        "blocked_tools": audit.get("blocked_tools", [])
        if isinstance(audit, Mapping)
        else [],
        "policy_checked": bool(run.get("policy_checked", False)),
        "memory_version": run.get("memory_version"),
        "transitions": run.get("transitions"),
        "max_transitions": run.get("max_transitions"),
        "cache_hit": bool(run.get("cache_hit", False)),
        "artifact_hash": run.get("artifact_hash"),
        "read_set": _sanitize(run.get("read_set") or []),
        "evidence_calls": evidence_calls,
        "decision_iterations": run.get("decision_iterations"),
        "wall_clock_ms": run.get("wall_clock_ms"),
        "swarm_nodes": list(run.get("swarm_nodes") or []),
        "execution_engine": run.get("execution_engine"),
        "budget": _sanitize(run.get("budget") or {}),
        "weave": _sanitize(run.get("weave") or {}),
    }


def _sanitize(value: Any) -> Any:
    if type(value).__name__ in _REDACTED_CLASSNAMES:
        return f"<{type(value).__name__}>"
    if isinstance(value, str):
        return _bounded_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        items = list(value.items())
        result = {
            str(k): "***" if _looks_secret_key(k) else _sanitize(v)
            for k, v in items[:_MAX_TRACE_ITEMS]
        }
        if len(items) > _MAX_TRACE_ITEMS:
            result["_omitted_fields"] = len(items) - _MAX_TRACE_ITEMS
        return result
    if isinstance(value, (list, tuple)):
        result = [_sanitize(v) for v in value[:_MAX_TRACE_ITEMS]]
        if len(value) > _MAX_TRACE_ITEMS:
            result.append(f"<{len(value) - _MAX_TRACE_ITEMS} items omitted>")
        return result
    # Weave can serialize arbitrary objects recursively. Keep clients, settings,
    # models, and other runtime objects out of a dashboard that may be shared.
    return f"<{type(value).__name__}>"


def _compact_input(name: str, value: Any) -> Any:
    if name == "ticket" and isinstance(value, Mapping):
        return _ticket_trace_view(value)
    if name == "tickets" and isinstance(value, Mapping):
        case_ids = list(value)[:_MAX_TRACE_ITEMS]
        return {"count": len(value), "case_ids": case_ids}
    if name == "artifacts" and isinstance(value, Mapping):
        return _artifact_trace_view(value)
    if name == "manifest":
        return _manifest_trace_view(value)
    if name == "run" and isinstance(value, Mapping):
        return _run_trace_view(value)
    if name == "context" and isinstance(value, Mapping):
        return {str(k): _compact_input(str(k), v) for k, v in value.items()}
    if name == "self":
        return f"<{type(value).__name__}>"
    return _sanitize(value)


def _postprocess_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets from Weave op inputs before they are logged to the dashboard."""
    return {k: _compact_input(k, v) for k, v in inputs.items()}


def _postprocess_output(output: Any) -> Any:
    """Bound arbitrary outputs so nested SDK/runtime objects never reach Weave."""
    return _sanitize(output)


def compact_run_output(output: Any) -> Any:
    """Keep the decision proof visible without logging the full evidence ledger twice."""
    if not isinstance(output, Mapping):
        return _postprocess_output(output)
    return _run_trace_view(output)


def compact_episode_output(output: Any) -> dict[str, Any]:
    """Summarize an LLM episode while retaining decision, cost, and evidence proof."""
    getter = (
        output.get
        if isinstance(output, Mapping)
        else lambda key, default=None: getattr(output, key, default)
    )
    proposed = getter("proposed_tools", []) or []
    evidence = getter("evidence_calls", []) or []
    return {
        "action": getter("action"),
        "proposed_tools": [
            item.get("name") if isinstance(item, Mapping) else str(item)
            for item in proposed
        ],
        "evidence_calls": [
            {
                "name": item.get("name"),
                "iteration": item.get("iteration"),
                "result_hash": item.get("result_hash"),
            }
            for item in evidence
            if isinstance(item, Mapping)
        ],
        "iterations": getter("iterations"),
        "mode": getter("mode"),
        "model": getter("model"),
        "decided_by": getter("decided_by"),
        "fallback_used": bool(getter("fallback_used", False)),
        "security_guard_observed": bool(getter("security_guard_observed", False)),
        "artifact_basis": _sanitize(getter("artifact_basis", []) or []),
        "reason": _sanitize(getter("reason", "")),
        "prompt_tokens": getter("prompt_tokens"),
        "completion_tokens": getter("completion_tokens"),
        "total_tokens": getter("total_tokens"),
        "estimated_cost_usd": getter("estimated_cost_usd"),
        "stop_reason": getter("stop_reason"),
        "cache_hit": bool(getter("from_cache", False)),
    }


def compact_shadow_output(output: Any) -> Any:
    if not isinstance(output, Mapping):
        return _postprocess_output(output)
    cases = list(output.get("cases") or [])
    return {
        "id": output.get("id"),
        "artifact_key": output.get("artifact_key"),
        "case_count": len(cases),
        "passed_count": sum(
            bool(case.get("passed")) for case in cases if isinstance(case, Mapping)
        ),
        "regressions": [
            case.get("case_id")
            for case in cases
            if isinstance(case, Mapping) and case.get("regressed")
        ],
        "failed_cases": [
            case.get("case_id")
            for case in cases
            if isinstance(case, Mapping) and not case.get("passed")
        ],
        "hero_improved": bool(output.get("hero_improved")),
        "no_regressions": bool(output.get("no_regressions")),
        "passed": bool(output.get("passed")),
        "mode": output.get("mode"),
        "samples_per_case": output.get("samples_per_case"),
    }


_weave_initialized = False
_weave_retry_after = 0.0
_weave_init_error: str | None = None

try:
    import weave as _weave

    _weave_available = True
except ImportError:
    _weave = None  # type: ignore[assignment]
    _weave_available = False


def weave_tracing_enabled() -> bool:
    """True when Weave ops/evals should run (independent of LOOPIE_LLM_MODE)."""
    settings = get_settings()
    if not settings.weave_enabled:
        return False
    return _weave_available and bool(os.getenv("WANDB_API_KEY"))


def ensure_weave() -> bool:
    """Initialize Weave once, with a cooldown after an unavailable project."""
    global _weave_initialized, _weave_retry_after, _weave_init_error
    if _weave_initialized:
        return True
    if not weave_tracing_enabled() or time.monotonic() < _weave_retry_after:
        return False
    try:
        entity = os.getenv("WANDB_ENTITY", "").strip()
        project = get_settings().weave_project
        _weave.init(f"{entity}/{project}" if entity else project)
        _weave_initialized = True
        _weave_retry_after = 0.0
        _weave_init_error = None
        return True
    except Exception as exc:
        _weave_initialized = False
        _weave_init_error = f"{type(exc).__name__}: {' '.join(str(exc).split())[:500]}"
        # A bad entity/project previously added one network timeout per shadow
        # case. Retry later, not for every decorated operation in the same run.
        _weave_retry_after = time.monotonic() + 60.0
        return False


def weave_runtime_status() -> dict[str, Any]:
    """Return verified initialization state and bounded, non-secret diagnostics."""
    ready = ensure_weave()
    return {
        "ready": ready,
        "error": None if ready else _weave_init_error,
    }


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)


def _looks_like_weave_eval_id(value: str) -> bool:
    text = value.strip()
    if not text or len(text) > 160:
        return False
    if text.startswith(("{", "[", "'", '"')):
        return False
    if "'output'" in text or "true_count" in text or "true_fraction" in text:
        return False
    if _UUID_RE.fullmatch(text.split("/")[-1]):
        return True
    return len(text) <= 64 and "/" not in text and " " not in text and "=" not in text


def extract_weave_eval_id(eval_result: Any) -> str | None:
    """Return a Weave evaluation object id suitable for dashboard deep-linking."""
    if eval_result is None:
        return None

    candidates: list[Any] = []
    for attr in ("id", "evaluation_id", "eval_id", "object_id", "digest", "ref", "uri"):
        candidates.append(getattr(eval_result, attr, None))
    if isinstance(eval_result, dict):
        for key in ("id", "evaluation_id", "eval_id", "object_id", "digest", "ref", "uri"):
            candidates.append(eval_result.get(key))

    for raw in candidates:
        if raw is None:
            continue
        text = str(raw).strip()
        if _looks_like_weave_eval_id(text):
            return text.split("/")[-1]
        match = _UUID_RE.search(text)
        if match:
            return match.group(0)
    return None


def weave_eval_url(eval_id: str, *, entity: str | None = None, project: str | None = None) -> str | None:
    """Deep-link into Weave Compare/Leaderboard for cockpit surfacing."""
    if not _looks_like_weave_eval_id(eval_id):
        return None
    settings = get_settings()
    entity = entity or os.getenv("WANDB_ENTITY", "")
    project = project or settings.weave_project
    clean_id = eval_id.strip().split("/")[-1]
    if entity:
        return f"https://wandb.ai/{entity}/{project}/weave/evaluations/{clean_id}"
    return f"https://wandb.ai/{project}/weave/evaluations/{clean_id}"


def weave_eval_browse_url(*, evaluation_name: str, entity: str | None = None, project: str | None = None) -> str | None:
    """Fallback when Weave returns an aggregate summary without a stable eval object id."""
    from urllib.parse import quote

    entity = entity or os.getenv("WANDB_ENTITY", "")
    if not entity:
        return None
    settings = get_settings()
    project = project or settings.weave_project
    return f"https://wandb.ai/{entity}/{project}/weave/evaluations?search={quote(evaluation_name)}"


def weave_traces_url(*, entity: str | None = None, project: str | None = None) -> str | None:
    """Link to the live Weave traces dashboard (ops as they fire), independent of any eval.

    Returns None when no entity is known, so the cockpit never renders a dead link.
    """
    settings = get_settings()
    entity = entity or os.getenv("WANDB_ENTITY", "")
    project = project or settings.weave_project
    if not entity:
        return None
    return f"https://wandb.ai/{entity}/{project}/weave/traces"


def current_weave_call_evidence() -> dict[str, str] | None:
    """Capture durable call identifiers while executing inside a Weave op."""

    if not weave_tracing_enabled() or _weave is None:
        return None
    try:
        call = _weave.get_current_call()
        if call is None:
            return None
        evidence = {
            "call_id": str(call.id),
            "trace_id": str(call.trace_id),
        }
        dashboard = getattr(call, "ui_url", None) or weave_traces_url()
        if dashboard:
            evidence["dashboard_url"] = str(dashboard)
        return evidence
    except Exception:
        return None


def op(
    name: str,
    *,
    call_display_name: str | Callable[[Any], str] | None = None,
    postprocess_output: Callable[[Any], Any] | None = None,
    kind: Literal["agent", "llm", "tool", "search"] | None = None,
) -> Callable[[F], F]:
    """Decorate with weave.op when LOOPIE_WEAVE_ENABLED and W&B creds are present."""

    def decorator(fn: F) -> F:
        weave_fn: Callable[..., Any] | None = None

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal weave_fn
            if weave_tracing_enabled():
                if not ensure_weave():
                    return fn(*args, **kwargs)
                if weave_fn is None:
                    weave_fn = _weave.op(
                        name=name,
                        call_display_name=call_display_name,
                        postprocess_inputs=_postprocess_inputs,
                        postprocess_output=postprocess_output or _postprocess_output,
                        kind=kind,
                    )(fn)
                return weave_fn(*args, **kwargs)
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
