"""Regression checks for every schema sent to OpenAI strict output mode."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.loopie.policy.compiler import CompiledPolicyWire
from src.loopie.reliability.classifier import FailureClassificationWire
from src.loopie.reliability.correction_gen import GeneratedCorrectionWire
from src.loopie.reliability.judge import JudgeVerdict


def _schema_has_key(value, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_schema_has_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_schema_has_key(item, key) for item in value)
    return False


def _assert_strict_objects(value) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
            assert set(value.get("required", [])) == set(value.get("properties", {}))
        for item in value.values():
            _assert_strict_objects(item)
    elif isinstance(value, list):
        for item in value:
            _assert_strict_objects(item)


@pytest.mark.parametrize(
    "model",
    [
        GeneratedCorrectionWire,
        CompiledPolicyWire,
        FailureClassificationWire,
        JudgeVerdict,
    ],
)
def test_openai_wire_schema_uses_supported_strict_structure(model: type[BaseModel]) -> None:
    schema = model.model_json_schema()

    assert not _schema_has_key(schema, "oneOf")
    assert not _schema_has_key(schema, "anyOf")
    assert not _schema_has_key(schema, "default")
    _assert_strict_objects(schema)
