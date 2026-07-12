"""Versioned text-token price card (USD per one million tokens)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenPrice:
    input_per_million: float
    output_per_million: float
    source_version: str


# Verified against official model pages on 2026-07-11. Keep this code-owned so
# historical run costs remain reproducible when vendor pages change.
PRICE_CARD: dict[str, TokenPrice] = {
    "gpt-4o-mini": TokenPrice(0.15, 0.60, "openai-2026-07-11"),
    "gpt-4o-mini-2024-07-18": TokenPrice(0.15, 0.60, "openai-2026-07-11"),
    "gpt-4o": TokenPrice(2.50, 10.00, "openai-2026-07-11"),
    "gpt-5": TokenPrice(1.25, 10.00, "openai-2026-07-11"),
    "gpt-5.6": TokenPrice(5.00, 30.00, "openai-2026-07-11"),
    "gpt-5.6-sol": TokenPrice(5.00, 30.00, "openai-2026-07-11"),
    "gpt-5.6-terra": TokenPrice(2.50, 15.00, "openai-2026-07-11"),
    "gpt-5.6-luna": TokenPrice(1.00, 6.00, "openai-2026-07-11"),
    # Verified against developers.openai.com/api/docs/pricing on 2026-07-11.
    "gpt-5.4": TokenPrice(2.50, 15.00, "openai-2026-07-11"),
    "gpt-5.4-mini": TokenPrice(0.75, 4.50, "openai-2026-07-11"),
    "gpt-5.4-nano": TokenPrice(0.20, 1.25, "openai-2026-07-11"),
}


def estimate_text_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICE_CARD.get(model)
    if price is None:
        raise ValueError(f"No versioned price card exists for model {model!r}")
    return (
        input_tokens * price.input_per_million
        + output_tokens * price.output_per_million
    ) / 1_000_000
