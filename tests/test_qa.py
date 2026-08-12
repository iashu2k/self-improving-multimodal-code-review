# tests/test_qa.py
"""Deterministic content-QA tests (Step 6): cheap checks that run before any
LLM critique. Placement is delegated to the validator — monkeypatched here so
these tests isolate the content layer."""

from types import SimpleNamespace

import pytest

from app.agents.qa import run_deterministic_qa
from app.agents.qa_schemas import (
  QA_DUPLICATE,
  QA_EMPTY_EVIDENCE,
  QA_FIX_TOO_LONG,
  QA_NO_RATIONALE,
)
from tests.test_critic_qa import make_candidate
from tests.test_triage import CALC


@pytest.fixture
def passthrough_validator(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(
    "app.agents.validator.validate_review_comments",
    lambda *, result, files: SimpleNamespace(
      accepted_comments=result.comments, suppressed_comments=[]
    ),
  )


def test_clean_comment_survives(passthrough_validator) -> None:
  survivors, suppressed = run_deterministic_qa([make_candidate()], files=CALC)
  assert len(survivors) == 1
  assert suppressed == []


def test_empty_evidence_suppressed(passthrough_validator) -> None:
  comment = make_candidate()
  comment.evidence = []
  survivors, suppressed = run_deterministic_qa([comment], files=CALC)
  assert survivors == []
  assert [s.reason for s in suppressed] == [QA_EMPTY_EVIDENCE]


def test_body_without_claim_and_rationale_suppressed(passthrough_validator) -> None:
  comment = make_candidate(body="This is bad.")
  survivors, suppressed = run_deterministic_qa([comment], files=CALC)
  assert survivors == []
  assert [s.reason for s in suppressed] == [QA_NO_RATIONALE]


def test_oversized_suggested_fix_suppressed(passthrough_validator) -> None:
  comment = make_candidate()
  comment.suggested_fix = "x" * 400
  survivors, suppressed = run_deterministic_qa([comment], files=CALC)
  assert survivors == []
  assert [s.reason for s in suppressed] == [QA_FIX_TOO_LONG]


def test_validator_suppression_propagates_with_reason(monkeypatch: pytest.MonkeyPatch) -> None:
  comment = make_candidate()
  monkeypatch.setattr(
    "app.agents.validator.validate_review_comments",
    lambda *, result, files: SimpleNamespace(
      accepted_comments=[],
      suppressed_comments=[SimpleNamespace(comment=comment, reason="line_not_in_diff")],
    ),
  )
  survivors, suppressed = run_deterministic_qa([comment], files=CALC)
  assert survivors == []
  assert len(suppressed) == 1
  # validator contract string preserved
  assert suppressed[0].reason == "line_not_in_diff"
  assert suppressed[0].comment is comment


def test_near_duplicate_suppressed_first_survives(passthrough_validator) -> None:
  first = make_candidate(
    title="Float truncation",
    body="int() truncates the result because it floors toward zero, breaking callers.",
  )
  second = make_candidate(
    title="Truncation issue",
    line=2,
    body="int() truncates the result because it floors toward zero, breaking callers entirely.",
  )
  survivors, suppressed = run_deterministic_qa([first, second], files=CALC)
  assert survivors == [first]
  assert len(suppressed) == 1
  assert suppressed[0].reason == QA_DUPLICATE
  assert suppressed[0].comment is second


def test_realistic_body_without_marker_words_survives(passthrough_validator) -> None:
  """Regression: the first live run suppressed two real findings because
  the rationale-marker list didn't match how the model actually phrases
  things. Semantic quality is the critic's job, not a keyword list's."""
  comment = make_candidate(
    body="Dividing by zero produces ZeroDivisionError which the existing callers do not handle."
  )
  survivors, suppressed = run_deterministic_qa([comment], files=CALC)
  assert survivors == [comment]
  assert suppressed == []
