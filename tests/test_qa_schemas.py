from app.agents.qa_schemas import QAResult, RouteDecision, Verdict


def test_route_decision_matches_router_json_contract() -> None:
  decision = RouteDecision.model_validate(
    {
      "risk_level": "medium",
      "review_focus": ["correctness", "security"],
      "use_rag": True,
      "use_vision": False,
      "abstain": False,
      "reason": "Authentication middleware behavior changed",
    }
  )
  assert decision.risk_level.value == "medium"
  assert decision.use_vision is False


def test_qa_verdict_matches_critic_json_contract() -> None:
  result = QAResult.model_validate(
    {
      "verdicts": [
        {
          "comment_index": 0,
          "verdict": "repair",
          "grounded": False,
          "actionable": True,
          "duplicate": False,
          "policy_safe": True,
          "reason": "Assumes a transaction the context doesn't establish.",
          "repair_instruction": "Limit the claim to missing input validation on line 83.",
        }
      ]
    }
  )
  assert result.verdicts[0].verdict is Verdict.REPAIR
  assert result.verdicts[0].repair_instruction is not None
