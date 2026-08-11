import tarfile
from pathlib import Path

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
        """
        Fetch the unified diff for a PR from the GitHub API.

        Uses the documented v3 diff media type. If GitHub returns a 4xx
        (e.g., 404 or 406), return an empty diff so upstream logic can
        abstain gracefully.
        """
        response = await self._client.get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        if response.status_code >= 400:
            # Log and return empty diff; callers should handle abstention.
            # You can also add structlog logging here if desired.
            return ""
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

    async def fetch_tarball(self, repo: str, sha: str, dest_dir: Path) -> Path:
        """Download and extract the repo at sha. Returns the extracted root
        (tarballs extract into a single <owner>-<repo>-<sha>/ directory)."""
        url = f"{self._client.base_url}/repos/{repo}/tarball/{sha}"
        async with self._client.stream("GET", url) as resp:
            resp.raise_for_status()
            archive = dest_dir / "repo.tar.gz"
            with archive.open("wb") as fh:
                async for chunk in resp.aiter_bytes():
                    fh.write(chunk)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(dest_dir, filter="data")  # path-traversal-safe (3.12+)
        archive.unlink()
        children = [p for p in dest_dir.iterdir() if p.is_dir()]
        if len(children) != 1:
            raise GitHubAPIError(f"unexpected tarball layout for {repo}@{sha}")
        return children[0]
