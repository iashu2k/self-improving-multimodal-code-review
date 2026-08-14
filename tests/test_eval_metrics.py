"""Metric tests: precision/recall/F1, abstention accuracy, pass@k, confusion."""

from app.agents.schemas import Severity
from app.evals.metrics import aggregate, routing_confusion, score_example
from app.evals.schemas import MatchRecord, SystemName


class FakeGold:
  def __init__(self, severity=Severity.HIGH):
    self.severity = severity


class FakeComment:
  def __init__(self, severity=Severity.HIGH):
    self.severity = severity


def _match(gold_index, generated_index, matched=True):
  return MatchRecord(
    example_id="ex",
    gold_index=gold_index,
    generated_index=generated_index,
    verdict=None,
    matched=matched,
  )


def test_precision_recall_f1():
  gold = [FakeGold(), FakeGold()]
  gen = [FakeComment(), FakeComment()]
  matches = [_match(0, 0), _match(1, None, matched=False)]
  m = score_example("ex", SystemName.FINAL_AGENT, gold, gen, matches, [True, False], [True, True])
  assert m.tp == 1 and m.fn == 1 and m.fp == 1
  assert m.precision == 0.5
  assert m.recall == 0.5
  assert m.f1 == 0.5
  assert m.grounded_comments == 1


def test_abstention_accuracy():
  neg_correct = score_example("n1", SystemName.FINAL_AGENT, [], [], [], [], [])
  neg_wrong = score_example("n2", SystemName.FINAL_AGENT, [], [FakeComment()], [], [True], [True])
  agg = aggregate(SystemName.FINAL_AGENT, "holdout", [neg_correct, neg_wrong])
  assert agg.negative_examples == 2
  assert agg.correct_abstentions == 1
  assert agg.no_comment_accuracy == 0.5


def test_pass_at_k():
  per_example = [
    score_example(
      "a", SystemName.FINAL_AGENT, [FakeGold()], [FakeComment()], [_match(0, 0)], [True], [True]
    ),
    score_example(
      "b", SystemName.FINAL_AGENT, [FakeGold()], [], [_match(0, None, matched=False)], [], []
    ),
  ]
  pass_attempts = {"a": [True, True], "b": [False, True]}
  agg = aggregate(SystemName.FINAL_AGENT, "holdout", per_example, pass_attempts)
  assert agg.pass_at_1 == 0.5
  assert agg.pass_at_2 == 1.0


def test_routing_confusion():
  rows = [
    score_example(
      "c1", SystemName.FINAL_AGENT, [FakeGold()], [FakeComment()], [_match(0, 0)], [True], [True]
    ),
    score_example("c2", SystemName.FINAL_AGENT, [], [], [], [], []),
    score_example("c3", SystemName.FINAL_AGENT, [], [FakeComment()], [], [False], [True]),
    score_example(
      "c4", SystemName.FINAL_AGENT, [FakeGold()], [], [_match(0, None, matched=False)], [], []
    ),
  ]
  c = routing_confusion(rows)
  assert c.true_comment == 1
  assert c.true_abstain == 1
  assert c.false_comment == 1
  assert c.false_abstain == 1
