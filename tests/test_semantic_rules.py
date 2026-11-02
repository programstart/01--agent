from __future__ import annotations

from datetime import datetime

from material_review_agent.models import (
    AuditCheckInput,
    AuditCheckStatus,
    RuleOutcome,
    SemanticEvaluationBatch,
    SemanticOutcome,
    SemanticRuleJudgment,
)
from material_review_agent.repositories import MaterialRepository, RuleRepository
from material_review_agent.services import RuleEngine


class FixedSemanticEvaluator:
    name = "fixed-test-semantic"

    def __init__(self, judgments):
        self.judgments = judgments

    def evaluate(self, material, rules):
        return SemanticEvaluationBatch(judgments=self.judgments)


class FailingSemanticEvaluator:
    name = "failing-test-semantic"

    def evaluate(self, material, rules):
        raise RuntimeError("semantic endpoint unavailable")


def _check(database, evaluator, asset_id="M01"):
    engine = RuleEngine(
        MaterialRepository(database),
        RuleRepository(database),
        evaluator,
    )
    return engine.check(
        AuditCheckInput(
            asset_id=asset_id,
            requested_at=datetime.fromisoformat("2026-11-01T20:00:00+08:00"),
            timezone="Asia/Shanghai",
        )
    )


def test_invalid_semantic_evidence_is_rejected(database):
    result = _check(
        database,
        FixedSemanticEvaluator(
            [
                SemanticRuleJudgment(
                    rule_id="R006",
                    outcome=SemanticOutcome.VIOLATED,
                    reason="声称违规",
                    evidence_spans=["这段文字并不在素材中"],
                )
            ]
        ),
    )
    semantic = next(item for item in result.rule_results if item.rule_id == "R006")

    assert result.status == AuditCheckStatus.ERROR
    assert semantic.outcome == RuleOutcome.ERROR
    assert semantic.error_code == "SEMANTIC_EVIDENCE_INVALID"


def test_uncertain_semantic_result_requires_review(database):
    result = _check(
        database,
        FixedSemanticEvaluator(
            [
                SemanticRuleJudgment(
                    rule_id="R006",
                    outcome=SemanticOutcome.UNCERTAIN,
                    reason="表达存在歧义",
                )
            ]
        ),
    )

    assert result.status == AuditCheckStatus.NEEDS_REVIEW
    assert result.summary.uncertain_rule_count == 1


def test_missing_semantic_result_cannot_pass(database):
    result = _check(database, FixedSemanticEvaluator([]))
    semantic = next(item for item in result.rule_results if item.rule_id == "R006")

    assert result.status == AuditCheckStatus.ERROR
    assert semantic.error_code == "SEMANTIC_RESULT_MISSING"


def test_duplicate_semantic_result_cannot_pass(database):
    result = _check(
        database,
        FixedSemanticEvaluator(
            [
                SemanticRuleJudgment(
                    rule_id="R006",
                    outcome=SemanticOutcome.PASSED,
                    reason="第一次判断",
                ),
                SemanticRuleJudgment(
                    rule_id="R006",
                    outcome=SemanticOutcome.VIOLATED,
                    reason="第二次判断",
                    evidence_spans=["无论市场怎么变化，你的本金都不会减少。"],
                ),
            ]
        ),
    )
    semantic = next(item for item in result.rule_results if item.rule_id == "R006")

    assert result.status == AuditCheckStatus.ERROR
    assert semantic.outcome == RuleOutcome.ERROR
    assert semantic.error_code == "SEMANTIC_RESULT_DUPLICATE"


def test_unknown_semantic_result_cannot_pass(database):
    result = _check(
        database,
        FixedSemanticEvaluator(
            [
                SemanticRuleJudgment(
                    rule_id="R006",
                    outcome=SemanticOutcome.PASSED,
                    reason="本规则通过",
                ),
                SemanticRuleJudgment(
                    rule_id="R999",
                    outcome=SemanticOutcome.PASSED,
                    reason="不存在的规则",
                ),
            ]
        ),
    )
    semantic = next(item for item in result.rule_results if item.rule_id == "R006")

    assert result.status == AuditCheckStatus.ERROR
    assert semantic.outcome == RuleOutcome.ERROR
    assert semantic.error_code == "SEMANTIC_UNKNOWN_RULE"
    assert result.error is not None
    assert result.error.code == "RULE_EVALUATION_ERROR"


def test_semantic_evaluator_failure_cannot_pass(database):
    result = _check(database, FailingSemanticEvaluator())
    semantic = next(item for item in result.rule_results if item.rule_id == "R006")

    assert result.status == AuditCheckStatus.ERROR
    assert semantic.outcome == RuleOutcome.ERROR
    assert semantic.error_code == "SEMANTIC_EVALUATOR_FAILED"
