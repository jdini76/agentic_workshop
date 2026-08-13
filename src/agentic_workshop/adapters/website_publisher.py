"""Website adapter for the provider-neutral Publisher port.

Publishes by pushing a rendered static homepage to a GitHub repo (the site's source of truth,
git-token-authenticated over HTTPS, no SSH key needed from this adapter). A GitHub Actions
workflow in that repo (a separate repo from this one -- see jatf_website's own
.github/workflows/deploy.yml), triggered directly by that push, rsyncs or SFTPs the files to the
server -- bypassing cPanel's own Git Version Control "pull from remote" feature entirely.

That's a deliberate design change, not the original one: cPanel's own pull-from-GitHub tracking
was confirmed broken for this repo during live testing -- both its UI button and the identical
UAPI call this adapter used to make reported the deployed content was current when it demonstrably
wasn't. Most likely cause: the repo was originally cloned by hand in a terminal rather than through
cPanel's own repo-creation flow, leaving its internal tracking permanently out of sync with the
real repository state on disk. See docs/model-adapters.md and STATUS.md for the full
investigation.

Since GitHub Actions runs asynchronously, deploy success is confirmed by polling the GitHub
Actions API for a workflow run tied to the pushed commit, not by any direct call to the hosting
provider. When there's nothing new to commit (a deliberate retry after a prior deploy failure),
a fresh run is force-started via `workflow_dispatch` instead, since GitHub's own `on: push`
trigger will not fire a second time for identical content.

Honest caveat: whether this cPanel account has genuine full-shell or SFTP SSH access (distinct
from the git-shell-restricted deploy key already proven to work only for git clone/push) was not
confirmed as of this adapter's writing -- see the deploy workflow's rsync/SFTP variants and
STATUS.md.
"""

import asyncio
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import httpx

from agentic_workshop.adapters.env_credentials import (
    environment_value,
    is_invalid_credential,
    local_environment,
)
from agentic_workshop.domain.website_content import WebsiteStaticContent
from agentic_workshop.ports.publishing import (
    Publisher,
    PublisherAuthenticationError,
    PublisherError,
    PublisherMalformedResponseError,
    PublisherTimeoutError,
    PublisherUnavailableError,
    PublishRequest,
    PublishResponse,
)
from agentic_workshop.presentation.website_site import render_homepage

WEBSITE_PROVIDER = "website"
DEFAULT_CANONICAL_URL = "https://jordanandthefosters.fun"
DEFAULT_BRANCH = "main"
GIT_TIMEOUT_SECONDS = 60.0
GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_WORKFLOW_FILE = "deploy.yml"
ACTIONS_POLL_INTERVAL_SECONDS = 3.0
ACTIONS_POLL_MAX_ATTEMPTS = 60


class GitRunner(Protocol):
    """Thin injectable wrapper around the system git binary, for testability."""

    async def run(
        self, args: Sequence[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]: ...


class SubprocessGitRunner:
    """Default GitRunner: shells out to the real git executable."""

    async def run(
        self, args: Sequence[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )

        return await asyncio.to_thread(_run)


class WebsitePublisher(Publisher):
    """Render Casey's approved website draft into the site and deploy it via GitHub Actions."""

    def __init__(
        self,
        git_runner: GitRunner,
        http_client: httpx.AsyncClient,
        *,
        working_copy_root: Path,
        github_token: str,
        github_repo: str,
        static_content: WebsiteStaticContent,
        approved_destinations: tuple[str, ...],
        canonical_url: str = DEFAULT_CANONICAL_URL,
        branch: str = DEFAULT_BRANCH,
    ) -> None:
        self._git = git_runner
        self._http = http_client
        self._working_copy_root = working_copy_root
        self._github_token = github_token
        self._github_repo = github_repo
        self._static_content = static_content
        self._approved_destinations = approved_destinations
        self._canonical_url = canonical_url
        self._branch = branch

    @classmethod
    def from_environment(
        cls,
        *,
        working_copy_root: Path,
        static_content: WebsiteStaticContent,
        approved_destinations: tuple[str, ...],
        timeout_seconds: float = 30.0,
        load_dotenv: bool = True,
        env_file: Path | None = None,
    ) -> "WebsitePublisher":
        local_values = local_environment(env_file, load_dotenv=load_dotenv)

        def required(name: str) -> str:
            value = environment_value(name, local_values)
            if is_invalid_credential(value):
                raise PublisherAuthenticationError(
                    f"{name} is absent, empty, or a placeholder; no website deploy was made",
                    provider=WEBSITE_PROVIDER,
                )
            assert value is not None
            return value

        github_token = required("GITHUB_TOKEN")
        github_repo = required("GITHUB_REPO")
        if "://" in github_repo or github_repo.startswith("git@"):
            raise PublisherAuthenticationError(
                "GITHUB_REPO must be in 'owner/repo' form (e.g. 'jdini76/jatf_website'), not a "
                "full clone URL; no website deploy was made",
                provider=WEBSITE_PROVIDER,
            )
        canonical_url = (
            environment_value("WEBSITE_CANONICAL_URL", local_values) or DEFAULT_CANONICAL_URL
        )
        branch = environment_value("GITHUB_BRANCH", local_values) or DEFAULT_BRANCH

        http_client = httpx.AsyncClient(
            base_url=GITHUB_API_BASE_URL,
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        return cls(
            SubprocessGitRunner(),
            http_client,
            working_copy_root=working_copy_root,
            github_token=github_token,
            github_repo=github_repo,
            static_content=static_content,
            approved_destinations=approved_destinations,
            canonical_url=canonical_url,
            branch=branch,
        )

    async def publish(self, request: PublishRequest) -> PublishResponse:
        if request.title is None:
            raise ValueError("WebsitePublisher requires PublishRequest.title")
        if request.image_path is None:
            raise ValueError("WebsitePublisher requires PublishRequest.image_path")

        await self._ensure_working_copy()
        self._write_homepage(request.title, request.text, request.image_path)

        status = await self._run_git(["status", "--porcelain"])
        pushed_new_commit = bool(status.stdout.strip())
        if pushed_new_commit:
            await self._run_git(["add", "-A"])
            await self._run_git(
                [
                    "-c",
                    "user.name=agentic-workshop",
                    "-c",
                    "user.email=agentic-workshop@localhost",
                    "commit",
                    "-m",
                    f"Publish: {request.title}",
                ]
            )
            await self._run_git(["push", "origin", f"HEAD:{self._branch}"])
        head = await self._run_git(["rev-parse", "HEAD"])
        commit_sha = head.stdout.strip()

        if pushed_new_commit:
            # A brand-new commit SHA cannot have any pre-existing workflow runs, so there is
            # nothing to disambiguate -- the push's own `on: push` trigger creates the run.
            seen_run_ids: frozenset[int] = frozenset()
        else:
            # Nothing new to commit -- this is the retry path: a prior push already succeeded
            # but its deploy failed, or an operator is deliberately retrying identical content.
            # `git push` will not happen again, so `on: push` will not fire again either --
            # force a fresh run instead, snapshotting existing runs for this commit first so
            # polling can tell the new run apart from any stale prior run for the same SHA.
            seen_run_ids = await self._existing_run_ids(commit_sha)
            await self._dispatch_workflow()

        await self._await_deploy(commit_sha, seen_run_ids=seen_run_ids)

        return PublishResponse(
            external_post_id=commit_sha,
            external_url=self._canonical_url,
            provider_metadata={"provider": WEBSITE_PROVIDER, "deployed": True},
        )

    def _write_homepage(self, title: str, body: str, image_path: Path) -> None:
        cover_relative = "assets/cover.png"
        cover_target = self._working_copy_root / cover_relative
        cover_target.parent.mkdir(parents=True, exist_ok=True)
        cover_target.write_bytes(image_path.read_bytes())
        html_output = render_homepage(
            static_content=self._static_content,
            pitch_title=title,
            pitch_body=body,
            cover_image_relative_path=cover_relative,
            approved_destinations=self._approved_destinations,
        )
        (self._working_copy_root / "index.html").write_text(html_output, encoding="utf-8")

    async def _ensure_working_copy(self) -> None:
        git_dir = self._working_copy_root / ".git"
        if git_dir.is_dir():
            await self._run_git(["fetch", "origin", self._branch])
            await self._run_git(["reset", "--hard", f"origin/{self._branch}"])
            return
        self._working_copy_root.mkdir(parents=True, exist_ok=True)
        remote_url = (
            f"https://x-access-token:{self._github_token}@github.com/{self._github_repo}.git"
        )
        await self._run_git(["clone", remote_url, "."], cwd=self._working_copy_root)

    async def _run_git(
        self, args: Sequence[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        action = args[0]
        try:
            result = await self._git.run(args, cwd=cwd or self._working_copy_root)
        except subprocess.TimeoutExpired:
            raise PublisherTimeoutError(
                f"git {action} timed out", provider=WEBSITE_PROVIDER
            ) from None
        except FileNotFoundError:
            raise PublisherError(
                "git executable not found; is git installed and on PATH?",
                provider=WEBSITE_PROVIDER,
            ) from None
        if result.returncode != 0:
            raise self._normalized_git_error(action, result.stderr)
        return result

    def _normalized_git_error(self, action: str, stderr: str) -> PublisherError:
        sanitized = stderr.replace(self._github_token, "***")
        lowered = sanitized.lower()
        if (
            "authentication failed" in lowered
            or "403" in lowered
            or "permission denied" in lowered
        ):
            return PublisherAuthenticationError(
                f"git {action} failed: {sanitized}", provider=WEBSITE_PROVIDER
            )
        if (
            "could not resolve host" in lowered
            or "could not read from remote" in lowered
            or "connection" in lowered
            or "timed out" in lowered
        ):
            return PublisherUnavailableError(
                f"git {action} failed: {sanitized}", provider=WEBSITE_PROVIDER
            )
        return PublisherError(f"git {action} failed: {sanitized}", provider=WEBSITE_PROVIDER)

    async def _await_deploy(self, commit_sha: str, *, seen_run_ids: frozenset[int]) -> None:
        for _ in range(ACTIONS_POLL_MAX_ATTEMPTS):
            run = await self._find_new_run(commit_sha, seen_run_ids)
            if run is not None and run.get("status") == "completed":
                if run.get("conclusion") == "success":
                    return
                run_url = run.get("html_url", "no run URL available")
                raise PublisherError(
                    f"GitHub Actions deploy run concluded {run.get('conclusion')!r} ({run_url})",
                    provider=WEBSITE_PROVIDER,
                )
            await asyncio.sleep(ACTIONS_POLL_INTERVAL_SECONDS)
        raise PublisherTimeoutError(
            "GitHub Actions deploy run did not complete before the poll budget was exhausted",
            provider=WEBSITE_PROVIDER,
        )

    async def _find_new_run(
        self, commit_sha: str, seen_run_ids: frozenset[int]
    ) -> dict[str, Any] | None:
        runs = await self._list_workflow_runs(commit_sha)
        candidates = [
            run
            for run in runs
            if isinstance(run.get("id"), int) and run["id"] not in seen_run_ids
        ]
        return candidates[0] if candidates else None

    async def _existing_run_ids(self, commit_sha: str) -> frozenset[int]:
        runs = await self._list_workflow_runs(commit_sha)
        return frozenset(run["id"] for run in runs if isinstance(run.get("id"), int))

    async def _list_workflow_runs(self, commit_sha: str) -> list[dict[str, Any]]:
        response = await self._github_request(
            "GET",
            f"/repos/{self._github_repo}/actions/workflows/{GITHUB_WORKFLOW_FILE}/runs",
            params={"head_sha": commit_sha, "per_page": "10"},
        )
        try:
            payload = response.json()
        except ValueError:
            raise PublisherMalformedResponseError(
                "GitHub Actions returned a non-JSON response", provider=WEBSITE_PROVIDER
            ) from None
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            raise PublisherMalformedResponseError(
                "GitHub Actions run list response was malformed", provider=WEBSITE_PROVIDER
            )
        return runs

    async def _dispatch_workflow(self) -> None:
        await self._github_request(
            "POST",
            f"/repos/{self._github_repo}/actions/workflows/{GITHUB_WORKFLOW_FILE}/dispatches",
            json_body={"ref": self._branch},
        )

    async def _github_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._http.request(method, path, params=params, json=json_body)
        except httpx.TimeoutException:
            raise PublisherTimeoutError(
                "GitHub Actions request timed out", provider=WEBSITE_PROVIDER
            ) from None
        except httpx.ConnectError:
            raise PublisherUnavailableError(
                "GitHub could not be reached", provider=WEBSITE_PROVIDER
            ) from None

        if response.status_code in (401, 403):
            raise PublisherAuthenticationError(
                "GitHub rejected the request -- confirm GITHUB_TOKEN has Actions read/write "
                f"permission ({response.status_code})",
                provider=WEBSITE_PROVIDER,
            )
        if response.status_code == 404:
            raise PublisherError(
                "GitHub returned 404 -- confirm GITHUB_REPO and the deploy workflow file exist",
                provider=WEBSITE_PROVIDER,
            )
        if response.status_code >= 400:
            raise PublisherError(
                f"GitHub Actions request failed ({response.status_code})",
                provider=WEBSITE_PROVIDER,
            )
        return response
