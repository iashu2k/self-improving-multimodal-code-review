from app.db.models.feedback import FeedbackLabel
from app.diagnosis.taxonomy import (
  FailureCategory,
  classify_eval_match_failure,
  classify_feedback_failure,
)


def test_helpful_feedback_is_not_a_failure() -> None:
  assert classify_feedback_failure(FeedbackLabel.HELPFUL) is None


def test_false_positive_maps_to_generator_precision_failure() -> None:
  result = classify_feedback_failure(FeedbackLabel.FALSE_POSITIVE)

  assert result is not None
  assert result.category == FailureCategory.FALSE_POSITIVE
  assert result.agent_node == "review_generator"


def test_wrong_severity_maps_to_generator_calibration_failure() -> None:
  result = classify_feedback_failure(FeedbackLabel.WRONG_SEVERITY)

  assert result is not None
  assert result.category == FailureCategory.WRONG_SEVERITY
  assert result.agent_node == "review_generator"


def test_missing_context_maps_to_retrieval_failure() -> None:
  result = classify_feedback_failure(FeedbackLabel.MISSING_CONTEXT)

  assert result is not None
  assert result.category == FailureCategory.MISSING_CONTEXT
  assert result.agent_node == "rag_retriever"


def test_duplicate_maps_to_critic_duplicate_failure() -> None:
  result = classify_feedback_failure(FeedbackLabel.DUPLICATE)

  assert result is not None
  assert result.category == FailureCategory.DUPLICATE
  assert result.agent_node == "critic_qa"


def test_not_actionable_maps_to_generator_actionability_failure() -> None:
  result = classify_feedback_failure(FeedbackLabel.NOT_ACTIONABLE)

  assert result is not None
  assert result.category == FailureCategory.NOT_ACTIONABLE
  assert result.agent_node == "review_generator"


def test_unmatched_eval_generated_comment_is_false_positive() -> None:
  result = classify_eval_match_failure(
    matched=False,
    generated_index=0,
    judge_rationale="The generated comment identifies a different issue.",
  )

  assert result is not None
  assert result.category == FailureCategory.FALSE_POSITIVE
  assert result.agent_node == "review_generator"


def test_unmatched_eval_gold_comment_is_false_negative() -> None:
  result = classify_eval_match_failure(
    matched=False,
    generated_index=None,
    judge_rationale=None,
  )

  assert result is not None
  assert result.category == FailureCategory.FALSE_NEGATIVE
  assert result.agent_node == "review_generator"


def test_grounding_judge_language_maps_to_grounding_failure() -> None:
  result = classify_eval_match_failure(
    matched=False,
    generated_index=1,
    judge_rationale="The generated comment is not grounded in the shown diff hunk.",
  )

  assert result is not None
  assert result.category == FailureCategory.GROUNDING_FAILURE
  assert result.agent_node == "review_generator"


def test_matched_eval_pair_is_not_a_failure() -> None:
  assert (
    classify_eval_match_failure(
      matched=True,
      generated_index=0,
      judge_rationale="Semantically equivalent.",
    )
    is None
  )
