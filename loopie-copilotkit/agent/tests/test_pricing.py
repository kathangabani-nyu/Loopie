import pytest

from src.loopie.pricing import estimate_text_cost


def test_gpt_4o_mini_uses_separate_input_and_output_rates() -> None:
    assert estimate_text_cost("gpt-4o-mini", 1_000_000, 1_000_000) == pytest.approx(0.75)


def test_gpt_5_6_luna_price_card() -> None:
    assert estimate_text_cost("gpt-5.6-luna", 2_000, 500) == pytest.approx(0.005)


def test_unknown_model_requires_explicit_price_card() -> None:
    with pytest.raises(ValueError, match="No versioned price card"):
        estimate_text_cost("mystery-model", 1, 1)
