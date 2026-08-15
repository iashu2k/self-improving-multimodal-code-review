from app.llm.prompts.review import EVAL_RELAXED_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.llm.reviewer import EVAL_RELAXED_GENERATOR_POLICY, GENERATOR_POLICY


def test_relaxed_system_prompt_allows_concrete_maintainability() -> None:
  assert "maintainability" in EVAL_RELAXED_SYSTEM_PROMPT
  assert "duplication" in EVAL_RELAXED_SYSTEM_PROMPT
  assert "purely subjective" in EVAL_RELAXED_SYSTEM_PROMPT
  assert "Do not produce subjective style nitpicks." in SYSTEM_PROMPT


def test_relaxed_policy_keeps_grounding_and_allows_maintainability() -> None:
  assert "exact changed line" in EVAL_RELAXED_GENERATOR_POLICY
  assert "Never invent runtime behavior" in EVAL_RELAXED_GENERATOR_POLICY
  assert "Concrete maintainability findings are allowed" in EVAL_RELAXED_GENERATOR_POLICY
  assert "No subjective style comments." in GENERATOR_POLICY
