"""The three systems under evaluation, behind one interface.

  baseline_a: one-shot LLM with diff only
  baseline_b: diff + repository RAG
  final_agent: router + RAG + critic/retry + safe suppression

Each returns the comments the system would publish for one golden example,
plus the retrieved context it used (for groundedness judging) and the number
of generator attempts (for pass@k). The final agent is wrapped with repair
capped at 1 or 2 to produce pass@1 and pass@2 respectively.
"""

from dataclasses import dataclass, field

from app.agents.schemas import ReviewComment
from app.evals.schemas import SystemName


@dataclass
class SystemOutput:
  system: SystemName
  comments: list[ReviewComment]
  retrieved_context: str = ""
  attempts: int = 1
  # raw model output, kept for failure analysis
  raw: dict = field(default_factory=dict)
