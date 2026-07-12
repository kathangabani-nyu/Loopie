"""Shared Weave initialization and op decorator."""

from __future__ import annotations

import functools
import os
import re
from typing import Any, Callable, TypeVar

from src.loopie.config import get_settings

F = TypeVar("F", bound=Callable[..., Any])

# Connection strings (postgres://user:pass@host, redis://:pass@host, etc.) must never
# reach the Weave dashboard — it can be shared with judges/teammates. Redact credentials
# from any DSN string and replace store objects (which carry a raw `url`) with a label.
_DSN_CREDS_RE = re.compile(r"(?P<scheme>[a-zA-Z][\w+.\-]*://)(?P<creds>[^/@\s]+)@")
_REDACTED_CLASSNAMES = {"Ledger", "RedisStore"}


def _scrub_dsn(value: str) -> str:
    return _DSN_CREDS_RE.sub(lambda m: f"{m.group('scheme')}***@", value)


def _sanitize(value: Any) -> Any:
    if type(value).__name__ in _REDACTED_CLASSNAMES:
        return f"<{type(value).__name__}>"
    if isinstance(value, str):
        return _scrub_dsn(value) if "://" in value else value
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


def _postprocess_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets from Weave op inputs before they are logged to the dashboard."""
    return {k: _sanitize(v) for k, v in inputs.items()}

_weave_initialized = False

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


def ensure_weave() -> None:
    """Idempotent weave.init for pipeline and eval paths."""
    global _weave_initialized
    if _weave_initialized or not weave_tracing_enabled():
        return
    try:
        _weave.init(get_settings().weave_project)
        _weave_initialized = True
    except Exception:
        _weave_initialized = False


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


def op(name: str) -> Callable[[F], F]:
    """Decorate with weave.op when LOOPIE_WEAVE_ENABLED and W&B creds are present."""

    def decorator(fn: F) -> F:
        weave_fn: Callable[..., Any] | None = None

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal weave_fn
            if weave_tracing_enabled():
                ensure_weave()
                if weave_fn is None:
                    weave_fn = _weave.op(name=name, postprocess_inputs=_postprocess_inputs)(fn)
                return weave_fn(*args, **kwargs)
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
