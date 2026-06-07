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


def weave_eval_url(eval_id: str, *, entity: str | None = None, project: str | None = None) -> str:
    """Deep-link into Weave Compare/Leaderboard for cockpit surfacing."""
    settings = get_settings()
    entity = entity or os.getenv("WANDB_ENTITY", "")
    project = project or settings.weave_project
    if entity:
        return f"https://wandb.ai/{entity}/{project}/weave/evaluations/{eval_id}"
    return f"https://wandb.ai/{project}/weave/evaluations/{eval_id}"


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
