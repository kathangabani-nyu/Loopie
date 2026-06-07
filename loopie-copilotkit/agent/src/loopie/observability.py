"""Shared Weave initialization and op decorator."""

from __future__ import annotations

import functools
import os
from typing import Any, Callable, TypeVar

from src.loopie.config import get_settings

F = TypeVar("F", bound=Callable[..., Any])

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
                    weave_fn = _weave.op(name=name)(fn)
                return weave_fn(*args, **kwargs)
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
