import httpx
import pytest

from app.github.client import GitHubAPIError, GitHubClient


@pytest.mark.asyncio
async def test_get_pr_review_comment_returns_comment_payload() -> None:
  async def handler(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    assert request.url.path == "/repos/owner/repo/pulls/comments/987"
    assert request.headers["Authorization"] == "Bearer test-token"

    return httpx.Response(
      200,
      json={
        "id": 987,
        "body": (
          "A marked bot comment.\n\n"
          '<!-- review-forge {"file":"src/client.py","line":24,"run_id":17} -->'
        ),
        "path": "src/client.py",
        "line": 24,
      },
    )

  github = GitHubClient(
    "test-token",
    transport=httpx.MockTransport(handler),
  )

  try:
    comment = await github.get_pr_review_comment("owner", "repo", 987)
  finally:
    await github.aclose()

  assert comment["id"] == 987
  assert comment["path"] == "src/client.py"
  assert comment["line"] == 24
  assert "review-forge" in comment["body"]


@pytest.mark.asyncio
async def test_get_pr_review_comment_raises_with_github_error_detail() -> None:
  async def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, text="Not Found")

  github = GitHubClient(
    "test-token",
    transport=httpx.MockTransport(handler),
  )

  try:
    with pytest.raises(
      GitHubAPIError,
      match="GitHub review comment fetch failed with 404: Not Found",
    ):
      await github.get_pr_review_comment("owner", "repo", 987)
  finally:
    await github.aclose()
