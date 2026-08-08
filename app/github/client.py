import httpx


class GitHubAPIError(RuntimeError):
    """Raised when the GitHub API rejects a request, with response detail."""


class GitHubClient:
    """Installation-token-scoped GitHub REST client."""

    def __init__(
        self,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        response = await self._client.get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        response.raise_for_status()
        return response.text

    async def get_pr_head_sha(self, owner: str, repo: str, pr_number: int) -> str:
        """Fetch the CURRENT head SHA of the PR.

        The webhook payload's head SHA can be stale (e.g., after a
        force-push); always comment against the live head.
        """
        response = await self._client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
        response.raise_for_status()
        return response.json()["head"]["sha"]

    async def create_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        commit_id: str,
        body: str,
        comments: list[dict],
    ) -> dict:
        payload = {
            "commit_id": commit_id,
            "body": body,
            "event": "COMMENT",
            "comments": comments,
        }
        response = await self._client.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            json=payload,
        )

        if response.status_code >= 400:
            raise GitHubAPIError(
                f"GitHub review creation failed with {response.status_code}: "
                f"{response.text}. Payload: {payload}"
            )

        return response.json()

    async def get_repo_archive(self, owner: str, repo: str, sha: str) -> bytes:
        """Download a tarball of the repo at a specific SHA."""
        response = await self._client.get(
            f"/repos/{owner}/{repo}/tarball/{sha}",
            follow_redirects=True,
            timeout=httpx.Timeout(60.0),
        )
        response.raise_for_status()
        return response.content
