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


def ensure_weave() -> None:
    """Idempotent weave.init for pipeline and eval paths."""
    global _weave_initialized
    if _weave_initialized or not _weave_available or not os.getenv("WANDB_API_KEY"):
        return
    if get_settings().is_mock:
        return
    try:
        _weave.init(get_settings().weave_project)
        _weave_initialized = True
    except Exception:
        _weave_initialized = False


def _tracing_enabled() -> bool:
    return _weave_available and bool(os.getenv("WANDB_API_KEY")) and not get_settings().is_mock


def op(name: str) -> Callable[[F], F]:
    """Decorate with weave.op only when live + W&B creds are present."""

    def decorator(fn: F) -> F:
        weave_fn: Callable[..., Any] | None = None

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal weave_fn
            if _tracing_enabled():
                ensure_weave()
                if weave_fn is None:
                    weave_fn = _weave.op(name=name)(fn)
                return weave_fn(*args, **kwargs)
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
