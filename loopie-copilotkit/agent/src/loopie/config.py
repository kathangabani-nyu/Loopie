"""Environment and budget configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


@dataclass(frozen=True)
class LoopieSettings:
    llm_mode: str
    require_live_confirmation: bool
    llm_seed: int
    max_llm_calls_per_run: int
    max_llm_calls_per_eval: int
    max_agent_transitions: int
    max_eval_cases_per_dev_run: int
    max_estimated_cost_usd: float
    enable_replay_cache: bool
    full_agentic: bool
    hosted: bool
    persistence_mode: str
    redis_url: str
    postgres_url: str
    weave_project: str
    openai_model: str

    @property
    def is_mock(self) -> bool:
        return self.llm_mode != "live"

    @property
    def requires_durable_stores(self) -> bool:
        return self.hosted or self.persistence_mode == "hosted"


@lru_cache(maxsize=1)
def get_settings() -> LoopieSettings:
    return LoopieSettings(
        llm_mode=os.getenv("LOOPIE_LLM_MODE", "mock").strip().lower(),
        require_live_confirmation=_env_bool("LOOPIE_REQUIRE_LIVE_LLM_CONFIRMATION", True),
        llm_seed=_env_int("LOOPIE_LLM_SEED", 42),
        max_llm_calls_per_run=_env_int("LOOPIE_MAX_LLM_CALLS_PER_RUN", 8),
        max_llm_calls_per_eval=_env_int("LOOPIE_MAX_LLM_CALLS_PER_EVAL", 40),
        max_agent_transitions=_env_int("LOOPIE_MAX_AGENT_TRANSITIONS", 6),
        max_eval_cases_per_dev_run=_env_int("LOOPIE_MAX_EVAL_CASES_PER_DEV_RUN", 6),
        max_estimated_cost_usd=_env_float("LOOPIE_MAX_ESTIMATED_COST_USD", 0.25),
        enable_replay_cache=_env_bool("LOOPIE_ENABLE_REPLAY_CACHE", True),
        full_agentic=_env_bool("LOOPIE_FULL_AGENTIC", False),
        hosted=_env_bool("LOOPIE_HOSTED", False),
        persistence_mode=os.getenv("LOOPIE_PERSISTENCE_MODE", "auto").strip().lower(),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/1"),
        postgres_url=os.getenv(
            "POSTGRES_URL",
            "postgresql://intelligence:intelligence@localhost:5432/intelligence_app",
        ),
        weave_project=os.getenv("WEAVE_PROJECT", "loopie"),
        openai_model=os.getenv("LOOPIE_OPENAI_MODEL", "gpt-4o-mini"),
    )
