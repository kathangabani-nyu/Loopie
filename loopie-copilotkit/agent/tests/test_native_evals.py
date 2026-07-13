from __future__ import annotations

from src.loopie.native_evals import (
    NativeEvaluationSession,
    compact_evaluation_output,
    evaluation_row,
    flatten_correctness,
)


class FakePrediction:
    def __init__(self) -> None:
        self.output = None
        self.scores: dict[str, bool] = {}
        self.finished = False

    def __enter__(self) -> "FakePrediction":
        return self

    def __exit__(self, *_args) -> None:
        self.finished = True

    def log_score(self, scorer: str, score: bool) -> None:
        self.scores[scorer] = score


class FakeEvaluationLogger:
    def __init__(self) -> None:
        self.prediction = FakePrediction()
        self.inputs = None
        self.summary = None
        self.ui_url = "https://wandb.ai/entity/loopie/weave/evaluations/eval-1"

    def log_prediction(self, *, inputs):
        self.inputs = inputs
        return self.prediction

    def log_summary(self, summary) -> None:
        self.summary = summary

    def fail(self, _exception) -> None:
        raise AssertionError("successful evaluation must not fail")


def test_native_evaluation_session_records_existing_prediction_without_rerun() -> None:
    logger = FakeEvaluationLogger()
    session = NativeEvaluationSession(
        name="Loopie Golden baseline v1",
        logger=logger,
        status="recording",
    )
    inputs = {"case_id": "security_001"}
    with session.prediction(inputs) as prediction:
        session.record(
            prediction,
            output={"action": "escalate_security"},
            scores={"passed": False, "golden_required_policy_rules_present": False},
        )
    evidence = session.finish({"total": 1, "passed": 0})

    assert logger.inputs == inputs
    assert logger.prediction.output == {"action": "escalate_security"}
    assert logger.prediction.scores == {
        "passed": False,
        "golden_required_policy_rules_present": False,
    }
    assert logger.prediction.finished is True
    assert logger.summary == {"total": 1, "passed": 0}
    assert evidence == {
        "name": "Loopie Golden baseline v1",
        "status": "published",
        "url": "https://wandb.ai/entity/loopie/weave/evaluations/eval-1",
        "error": None,
    }


def test_native_evaluation_payloads_are_stable_compact_and_deterministic() -> None:
    ticket = {
        "case_id": "security_001",
        "body": "sensitive ticket body must not enter the dataset row",
        "expected_action": "escalate_security",
        "required_policy_rule_ids": ["security_flag_requires_escalation"],
    }
    assert evaluation_row(ticket) == {
        "case_id": "security_001",
        "case_family": "security",
        "expected_action": "escalate_security",
        "required_policy_rule_ids": ["security_flag_requires_escalation"],
    }

    correctness = {
        "passed": False,
        "policy": {"passed": True, "rules": []},
        "structural": {
            "passed": True,
            "scores": {"action_in_taxonomy": True},
        },
        "golden": {
            "passed": False,
            "scores": {"required_policy_rules_present": False},
        },
    }
    assert flatten_correctness(correctness) == {
        "passed": False,
        "policy_passed": True,
        "structural_passed": True,
        "golden_passed": False,
        "structural_action_in_taxonomy": True,
        "golden_required_policy_rules_present": False,
    }

    output = compact_evaluation_output(
        {
            "run_id": "run-1",
            "case_id": "security_001",
            "phase": "baseline",
            "action": "escalate_security",
            "body": "must not be logged",
            "fallback_used": False,
        }
    )
    assert output["run_id"] == "run-1"
    assert output["action"] == "escalate_security"
    assert "body" not in output
