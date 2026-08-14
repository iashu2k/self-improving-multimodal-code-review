"""Matcher layer tests: path, line tolerance, category equivalence, binding."""

from app.agents.schemas import ReviewCategory, Severity
from app.evals.matcher import deterministic_candidates, resolve_matches, severity_agrees
from app.evals.schemas import JudgeDecision, JudgeVerdict


class FakeGold:
  def __init__(self, file_path, line, category, severity=Severity.HIGH):
    self.file_path = file_path
    self.line = line
    self.category = category
    self.severity = severity
    self.issue_summary = "summary"
    self.evidence_requirement = "evidence"
    self.must_not_claim = []
    self.rationale = "rationale"


class FakeComment:
  def __init__(self, file_path, line, category, severity=Severity.HIGH):
    self.file_path = file_path
    self.line = line
    self.category = category
    self.severity = severity
    self.title = "t"
    self.body = "b"
    self.evidence = "e"


def test_path_must_match():
  gold = [FakeGold("a.py", 10, ReviewCategory.BUG_RISK)]
  gen = [FakeComment("b.py", 10, ReviewCategory.BUG_RISK)]
  assert deterministic_candidates(gold, gen) == []


def test_line_within_tolerance():
  gold = [FakeGold("a.py", 10, ReviewCategory.BUG_RISK)]
  inside = [FakeComment("a.py", 20, ReviewCategory.BUG_RISK)]
  outside = [FakeComment("a.py", 21, ReviewCategory.BUG_RISK)]

  assert len(deterministic_candidates(gold, inside)) == 1
  assert deterministic_candidates(gold, outside) == []


def test_category_equivalence():
  gold = [FakeGold("a.py", 10, ReviewCategory.SECURITY)]
  accepted = [FakeComment("a.py", 10, ReviewCategory.BUG_RISK)]
  rejected = [FakeComment("a.py", 10, ReviewCategory.STYLE)]
  assert len(deterministic_candidates(gold, accepted)) == 1
  assert deterministic_candidates(gold, rejected) == []


def test_one_to_one_binding():
  gold = [FakeGold("a.py", 10, ReviewCategory.BUG_RISK)]
  gen = [
    FakeComment("a.py", 10, ReviewCategory.BUG_RISK),
    FakeComment("a.py", 11, ReviewCategory.BUG_RISK),
  ]
  pairs = deterministic_candidates(gold, gen)
  decisions = [
    JudgeDecision(
      gold_index=0,
      generated_index=p.generated_index,
      verdict=JudgeVerdict.EQUIVALENT,
      rationale="same issue",
    )
    for p in pairs
  ]
  records = resolve_matches("ex", 1, 2, pairs, decisions)
  assert sum(1 for r in records if r.matched) == 1


def test_severity_within_one_level():
  assert severity_agrees(Severity.HIGH, Severity.CRITICAL)
  assert severity_agrees(Severity.MEDIUM, Severity.HIGH)
  assert not severity_agrees(Severity.LOW, Severity.HIGH)
  assert severity_agrees(Severity.LOW, Severity.LOW)
