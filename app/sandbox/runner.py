from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Iterable
from pathlib import Path

IMAGE = "code-review-sandbox:0.1.0"

INSTALL_TIMEOUT_SECS = 180
BUILD_TIMEOUT_SECS = 300
START_TIMEOUT_SECS = 60
CAPTURE_TIMEOUT_SECS = 120


@dataclasses.dataclass
class SandboxResult:
    ok: bool
    screenshots: list[str]
    stage_failed: str | None
    install_log: str
    build_log: str
    start_log: str
    capture_log: str
    error: str | None


def create_tarball(repo_path: Path, dest: Path) -> Path:
    """
    Create a tar.gz of the repo for docker cp, excluding heavy / volatile dirs.
    Arcname is 'repo' so the container always sees /workspace/repo.
    """
    repo_path = repo_path.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    exclude = {"node_modules", ".next", ".git"}

    with tarfile.open(dest, "w:gz") as tf:
        for root, _dirs, files in os.walk(repo_path):
            rel_root = os.path.relpath(root, repo_path)
            # Skip excluded top-level dirs.
            parts = rel_root.split(os.sep)
            if parts[0] in exclude:
                continue

            for fname in files:
                full = Path(root) / fname
                rel = os.path.relpath(full, repo_path)
                arcname = Path("repo") / rel
                tf.add(full, arcname=str(arcname))

    return dest


def _run(
    args: Iterable[str],
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


def run_pr_in_sandbox(
    repo_path: str | Path,
    routes: Iterable[str],
    workdir: str | Path,
    image: str = IMAGE,
) -> SandboxResult:
    """
    Run the PR-head repo inside the sandbox image, capture screenshots for
    the given routes, and return a SandboxResult summarizing the run.

    Lifecycle (all inside the container, with --network none):

    1. Copy repo tarball and capture.py into /workspace.
    2. npm ci --offline || npm install --offline
    3. npm run build
    4. npm start -- -p 3000 (detached)
    5. curl wait-loop for each route
    6. python capture.py to screenshot all routes
    7. docker cp screenshots back to host
    8. docker rm -f container
    """
    repo_path = Path(repo_path).resolve()
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    container_id = None
    install_log = ""
    build_log = ""
    start_log = ""
    capture_log = ""
    error: str | None = None
    screenshots: list[str] = []
    stage_failed: str | None = None

    tmpdir = Path(tempfile.mkdtemp(prefix="sandbox_tar_"))
    try:
        tar_path = create_tarball(repo_path, tmpdir / "repo.tar.gz")

        # 1. Start container with --network none and resource limits.
        run_proc = _run(
            [
                "docker",
                "run",
                "-d",
                "--network",
                "none",
                "--memory",
                "2g",
                "--cpus",
                "2",
                "--pids-limit",
                "256",
                image,
            ]
        )
        if run_proc.returncode != 0:
            error = f"docker run failed:\n{run_proc.stdout}"
            stage_failed = "run"
            return SandboxResult(
                ok=False,
                screenshots=[],
                stage_failed=stage_failed,
                install_log=install_log,
                build_log=build_log,
                start_log=start_log,
                capture_log=capture_log,
                error=error,
            )

        container_id = run_proc.stdout.strip()

        # 2. Copy repo tarball and capture.py into container.
        # We will later create app/vision/capture.py; for now assume it lives
        # at app/vision/capture.py in the host repo.
        capture_src = repo_path / "app" / "vision" / "capture.py"
        if not capture_src.exists():
            error = f"capture.py not found at {capture_src}"
            stage_failed = "setup"
            return SandboxResult(
                ok=False,
                screenshots=[],
                stage_failed=stage_failed,
                install_log=install_log,
                build_log=build_log,
                start_log=start_log,
                capture_log=capture_log,
                error=error,
            )

        cp_tar = _run(["docker", "cp", str(tar_path), f"{container_id}:/workspace/repo.tar.gz"])
        if cp_tar.returncode != 0:
            error = f"docker cp tarball failed:\n{cp_tar.stdout}"
            stage_failed = "setup"
            return SandboxResult(
                ok=False,
                screenshots=[],
                stage_failed=stage_failed,
                install_log=install_log,
                build_log=build_log,
                start_log=start_log,
                capture_log=capture_log,
                error=error,
            )

        cp_capture = _run(
            ["docker", "cp", str(capture_src), f"{container_id}:/workspace/capture.py"]
        )
        if cp_capture.returncode != 0:
            error = f"docker cp capture.py failed:\n{cp_capture.stdout}"
            stage_failed = "setup"
            return SandboxResult(
                ok=False,
                screenshots=[],
                stage_failed=stage_failed,
                install_log=install_log,
                build_log=build_log,
                start_log=start_log,
                capture_log=capture_log,
                error=error,
            )

        # 3. Untar repo inside container.
        untar = _run(
            [
                "docker",
                "exec",
                container_id,
                "bash",
                "-lc",
                "cd /workspace && tar -xzf repo.tar.gz",
            ],
        )
        if untar.returncode != 0:
            error = f"untar failed:\n{untar.stdout}"
            stage_failed = "setup"
            return SandboxResult(
                ok=False,
                screenshots=[],
                stage_failed=stage_failed,
                install_log=install_log,
                build_log=build_log,
                start_log=start_log,
                capture_log=capture_log,
                error=error,
            )

        # 4. Install dependencies offline.
        install_cmd = (
            "cd /workspace/repo/fixtures/demo-checkout && "
            "(npm ci --offline || npm install --offline)"
        )
        install = _run(
            ["docker", "exec", container_id, "bash", "-lc", install_cmd],
            timeout=INSTALL_TIMEOUT_SECS,
        )
        install_log = install.stdout
        if install.returncode != 0:
            stage_failed = "install"
            error = f"npm install failed:\n{install.stdout}"

            return SandboxResult(
                ok=False,
                screenshots=[],
                stage_failed=stage_failed,
                install_log=install_log,
                build_log=build_log,
                start_log=start_log,
                capture_log=capture_log,
                error=error,
            )

        # 5. Build the Next app.
        build_cmd = "cd /workspace/repo/fixtures/demo-checkout && npm run build"
        build = _run(
            ["docker", "exec", container_id, "bash", "-lc", build_cmd],
            timeout=BUILD_TIMEOUT_SECS,
        )
        build_log = build.stdout
        if build.returncode != 0:
            stage_failed = "build"
            error = f"npm run build failed:\n{build.stdout}"

            return SandboxResult(
                ok=False,
                screenshots=[],
                stage_failed=stage_failed,
                install_log=install_log,
                build_log=build_log,
                start_log=start_log,
                capture_log=capture_log,
                error=error,
            )

        # 6. Start the server in the background on port 3000.
        start_cmd = (
            "cd /workspace/repo/fixtures/demo-checkout && "
            "npm start -- -p 3000 > /workspace/server.log 2>&1 &"
        )
        start = _run(
            ["docker", "exec", container_id, "bash", "-lc", start_cmd],
            timeout=START_TIMEOUT_SECS,
        )
        start_log = start.stdout

        # 7. Wait for each route with a simple curl loop.
        for route in routes:
            wait_cmd = (
                "for i in $(seq 1 30); do "
                f"curl -s -o /dev/null -w '%{{http_code}}' "
                f"http://127.0.0.1:3000{route} | grep -q '^200$' "
                "&& break; "
                "sleep 2; "
                "done"
            )
            _run(
                ["docker", "exec", container_id, "bash", "-lc", wait_cmd],
                timeout=START_TIMEOUT_SECS,
            )
            # We intentionally do not fail hard here; capture will see failures.

        # 8. Run capture.py inside container.
        routes_csv = ",".join(routes)
        capture_cmd = (
            "cd /workspace && "
            "python capture.py "
            f"--url-base http://127.0.0.1:3000 "
            f"--routes {routes_csv} "
            "--out /workspace/screenshots "
            "--viewports mobile,desktop"
        )
        capture = _run(
            ["docker", "exec", container_id, "bash", "-lc", capture_cmd],
            timeout=CAPTURE_TIMEOUT_SECS,
        )
        capture_log = capture.stdout
        if capture.returncode != 0:
            stage_failed = "capture"
            error = f"capture.py failed:\n{capture.stdout}"

            # Try to copy screenshots anyway if they exist.
        # 9. Copy screenshots back to host workdir.
        host_shots = workdir / "screenshots"
        if host_shots.exists():
            shutil.rmtree(host_shots)
        host_shots.mkdir(parents=True, exist_ok=True)

        cp_shots = _run(
            ["docker", "cp", f"{container_id}:/workspace/screenshots", str(host_shots)],
        )
        if cp_shots.returncode != 0:
            # If capture failed, screenshots may not exist; record error but
            # keep stage_failed as capture/build/install as appropriate.
            if error is None:
                error = f"docker cp screenshots failed:\n{cp_shots.stdout}"
        else:
            # Collect screenshot paths.
            for path in host_shots.rglob("*.png"):
                screenshots.append(str(path))

        ok = error is None and (stage_failed is None or stage_failed == "capture")

        return SandboxResult(
            ok=ok,
            screenshots=screenshots,
            stage_failed=stage_failed,
            install_log=install_log,
            build_log=build_log,
            start_log=start_log,
            capture_log=capture_log,
            error=error,
        )
    finally:
        if container_id:
            _run(["docker", "rm", "-f", container_id])
        shutil.rmtree(tmpdir, ignore_errors=True)
