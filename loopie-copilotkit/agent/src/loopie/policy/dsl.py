"""Small, deterministic Policy DSL for refund, billing, and security controls.

Policy documents and LLM output are untrusted authoring inputs. Only a rule that
parses through these models can become an approved runtime artifact. Evaluation
does not call an LLM and records every fact path it reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list[JsonScalar]
Operator = Literal["eq", "neq", "gt", "gte", "lt", "lte", "in", "contains", "exists"]

_ALLOWED_ROOTS = frozenset({"ticket", "context", "artifacts", "decision"})


def _validate_path(path: str) -> str:
    segments = path.split(".")
    if len(segments) < 2 or segments[0] not in _ALLOWED_ROOTS:
        raise ValueError(f"path must start with one of {sorted(_ALLOWED_ROOTS)}")
    if any(not segment or not segment.replace("_", "").isalnum() for segment in segments):
        raise ValueError("path segments may contain only letters, numbers, and underscores")
    return path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Predicate(StrictModel):
    kind: Literal["predicate"] = "predicate"
    path: str
    operator: Operator
    value: JsonValue = None

    _path_is_safe = field_validator("path")(_validate_path)


class AllCondition(StrictModel):
    kind: Literal["all"] = "all"
    conditions: list["Condition"] = Field(min_length=1, max_length=20)


class AnyCondition(StrictModel):
    kind: Literal["any"] = "any"
    conditions: list["Condition"] = Field(min_length=1, max_length=20)


class NotCondition(StrictModel):
    kind: Literal["not"] = "not"
    condition: "Condition"


Condition: TypeAlias = Annotated[
    Union[Predicate, AllCondition, AnyCondition, NotCondition],
    Field(discriminator="kind"),
]


class RequireEffect(StrictModel):
    kind: Literal["require"] = "require"
    assertion: Predicate
    message: str = Field(min_length=1, max_length=300)


class BlockEffect(StrictModel):
    kind: Literal["block"] = "block"
    path: str
    contains_any: list[JsonScalar] = Field(min_length=1, max_length=50)
    message: str = Field(min_length=1, max_length=300)

    _path_is_safe = field_validator("path")(_validate_path)


class EscalateEffect(StrictModel):
    kind: Literal["escalate_to"] = "escalate_to"
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    message: str = Field(min_length=1, max_length=300)


Effect: TypeAlias = Annotated[
    Union[RequireEffect, BlockEffect, EscalateEffect],
    Field(discriminator="kind"),
]


class PolicyRule(StrictModel):
    schema_version: Literal["1"] = "1"
    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    version: int = Field(ge=1)
    name: str = Field(min_length=3, max_length=120)
    status: Literal["proposed", "approved", "retired"] = "proposed"
    when: Condition
    effects: list[Effect] = Field(min_length=1, max_length=20)


AllCondition.model_rebuild(_types_namespace={"Condition": Condition})
AnyCondition.model_rebuild(_types_namespace={"Condition": Condition})
NotCondition.model_rebuild(_types_namespace={"Condition": Condition})
_RULE_ADAPTER = TypeAdapter(PolicyRule)


@dataclass(frozen=True)
class PolicyViolation:
    rule_id: str
    effect: str
    message: str
    actual: Any
    expected: Any


@dataclass(frozen=True)
class PolicyEvaluation:
    rule_id: str
    applies: bool
    passed: bool
    violations: tuple[PolicyViolation, ...]
    read_set: tuple[str, ...]


def parse_policy_rule(value: dict[str, Any]) -> PolicyRule:
    """Validate an untrusted rule payload into an immutable runtime artifact."""

    return _RULE_ADAPTER.validate_python(value)


def _resolve(facts: dict[str, Any], path: str, reads: set[str]) -> Any:
    reads.add(path)
    current: Any = facts
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _compare(actual: Any, operator: Operator, expected: JsonValue) -> bool:
    if operator == "exists":
        return (actual is not None) is bool(expected)
    if operator == "eq":
        return actual == expected
    if operator == "neq":
        return actual != expected
    if operator in {"gt", "gte", "lt", "lte"}:
        if isinstance(actual, bool) or isinstance(expected, bool):
            return False
        if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
            return False
        return {
            "gt": actual > expected,
            "gte": actual >= expected,
            "lt": actual < expected,
            "lte": actual <= expected,
        }[operator]
    if operator == "in":
        return isinstance(expected, list) and actual in expected
    if operator == "contains":
        if isinstance(actual, str):
            return isinstance(expected, str) and expected.casefold() in actual.casefold()
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        return False
    raise ValueError(f"unsupported operator: {operator}")


def _evaluate_condition(condition: Condition, facts: dict[str, Any], reads: set[str]) -> bool:
    if isinstance(condition, Predicate):
        return _compare(_resolve(facts, condition.path, reads), condition.operator, condition.value)
    if isinstance(condition, AllCondition):
        # Do not short-circuit: the complete deterministic read-set is evidence.
        return all(_evaluate_condition(item, facts, reads) for item in condition.conditions)
    if isinstance(condition, AnyCondition):
        return any(_evaluate_condition(item, facts, reads) for item in condition.conditions)
    if isinstance(condition, NotCondition):
        return not _evaluate_condition(condition.condition, facts, reads)
    raise TypeError(f"unknown condition type: {type(condition).__name__}")


def _contains_any(actual: Any, forbidden: list[JsonScalar]) -> bool:
    if isinstance(actual, str):
        normalized = actual.casefold()
        return any(isinstance(item, str) and item.casefold() in normalized for item in forbidden)
    if isinstance(actual, (list, tuple, set)):
        return any(item in actual for item in forbidden)
    return False


def evaluate_policy(rule: PolicyRule, facts: dict[str, Any]) -> PolicyEvaluation:
    """Evaluate one approved rule against an immutable run fact bundle."""

    reads: set[str] = set()
    applies = _evaluate_condition(rule.when, facts, reads)
    violations: list[PolicyViolation] = []

    if applies:
        for effect in rule.effects:
            if isinstance(effect, RequireEffect):
                actual = _resolve(facts, effect.assertion.path, reads)
                if not _compare(actual, effect.assertion.operator, effect.assertion.value):
                    violations.append(
                        PolicyViolation(
                            rule_id=rule.rule_id,
                            effect=effect.kind,
                            message=effect.message,
                            actual=actual,
                            expected={
                                "operator": effect.assertion.operator,
                                "value": effect.assertion.value,
                            },
                        )
                    )
            elif isinstance(effect, BlockEffect):
                actual = _resolve(facts, effect.path, reads)
                if _contains_any(actual, effect.contains_any):
                    violations.append(
                        PolicyViolation(
                            rule_id=rule.rule_id,
                            effect=effect.kind,
                            message=effect.message,
                            actual=actual,
                            expected={"contains_none": effect.contains_any},
                        )
                    )
            elif isinstance(effect, EscalateEffect):
                actual = _resolve(facts, "decision.action", reads)
                if actual != effect.action:
                    violations.append(
                        PolicyViolation(
                            rule_id=rule.rule_id,
                            effect=effect.kind,
                            message=effect.message,
                            actual=actual,
                            expected=effect.action,
                        )
                    )

    return PolicyEvaluation(
        rule_id=rule.rule_id,
        applies=applies,
        passed=not violations,
        violations=tuple(violations),
        read_set=tuple(sorted(reads)),
    )
