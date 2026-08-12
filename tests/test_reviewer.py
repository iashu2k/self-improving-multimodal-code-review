from unittest.mock import AsyncMock

import pytest

from app.agents.schemas import ReviewResult
from app.github.diff_parser import parse_unified_diff
from app.llm.openrouter_client import StructuredResponse, Usage
from app.llm.reviewer import review_diff
from tests.test_diff_parser import SAMPLE_DIFF


@pytest.mark.asyncio
async def test_review_diff_returns_validated_result() -> None:
  fake = AsyncMock()
  fake.chat_structured.return_value = StructuredResponse(
    content={
      "summary": "Admin bypass in login path.",
      "comments": [
        {
          "file_path": "app/auth.py",
          "line": 12,
          "side": "RIGHT",
          "severity": "critical",
          "category": "security",
          "title": "Admin check bypasses credential verification",
          "body": "Returning True for admins skips check(user).",
          "evidence": ["if user.is_admin:", "return True"],
          "suggested_fix": "Call check(user) before the admin branch.",
          "confidence": 0.9,
        }
      ],
      "should_post_review": True,
      "abstain_reason": None,
    },
    usage=Usage(prompt_tokens=100, completion_tokens=50),
    model="test-model",
  )

  generated = await review_diff(
    files=parse_unified_diff(SAMPLE_DIFF),
    pr_title="test",
    pr_body="",
    client=fake,
    model="test-model",
  )

  assert isinstance(generated.result, ReviewResult)
  assert generated.result.comments[0].line == 12
  assert len(generated.accepted) == 1
