"""Website adapter for the provider-neutral Publisher port.

Publishes by pushing a rendered static homepage to a GitHub repo (the site's source of truth,
git-token-authenticated over HTTPS, no SSH key needed), then triggers cPanel's own Git Version
Control feature to pull and deploy it via cPanel's UAPI over HTTPS with a cPanel API token --
also no SSH/shell access needed, since UAPI is a core cPanel API surface separate from actual
shell access.

Honest caveat: the exact UAPI module/function names below (VersionControlDeployment::create /
::retrieve) are the standard cPanel Git Version Control API surface, but have not been verified
against this specific account's live API. Confirm against that account's own self-documented
/execute/ endpoints before depending on this in production -- see docs/model-adapters.md.
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
DEPLOY_POLL_INTERVAL_SECONDS = 2.0
DEPLOY_POLL_MAX_ATTEMPTS = 30


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
    """Render Casey's approved website draft into the site and deploy it via cPanel."""

    def __init__(
        self,
        git_runner: GitRunner,
        http_client: httpx.AsyncClient,
        *,
        working_copy_root: Path,
        github_token: str,
        github_repo: str,
        cpanel_username: str,
        cpanel_api_token: str,
        cpanel_git_repo_name: str,
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
        self._cpanel_username = cpanel_username
        self._cpanel_api_token = cpanel_api_token
        self._cpanel_git_repo_name = cpanel_git_repo_name
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
        cpanel_username = required("CPANEL_USERNAME")
        cpanel_api_token = required("CPANEL_API_TOKEN")
        cpanel_host = required("CPANEL_HOST")
        cpanel_git_repo_name = required("CPANEL_GIT_REPO_NAME")
        canonical_url = (
            environment_value("WEBSITE_CANONICAL_URL", local_values) or DEFAULT_CANONICAL_URL
        )
        branch = environment_value("GITHUB_BRANCH", local_values) or DEFAULT_BRANCH

        http_client = httpx.AsyncClient(
            base_url=f"https://{cpanel_host}:2083",
            timeout=timeout_seconds,
            headers={"Authorization": f"cpanel {cpanel_username}:{cpanel_api_token}"},
        )
        return cls(
            SubprocessGitRunner(),
            http_client,
            working_copy_root=working_copy_root,
            github_token=github_token,
            github_repo=github_repo,
            cpanel_username=cpanel_username,
            cpanel_api_token=cpanel_api_token,
            cpanel_git_repo_name=cpanel_git_repo_name,
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
        if status.stdout.strip():
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

        # Always attempt the deploy, even when the working copy already matched the desired
        # content (nothing to commit) -- that can happen after a prior attempt's git push
        # succeeded but its cPanel deploy failed. Skipping the deploy here would silently turn
        # a deliberate retry into a no-op, defeating the whole point of the retry action.
        await self._deploy_and_wait()

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

    async def _deploy_and_wait(self) -> None:
        deployment_id = await self._trigger_deploy()
        for _ in range(DEPLOY_POLL_MAX_ATTEMPTS):
            status = await self._poll_deploy(deployment_id)
            if status == "complete":
                return
            if status == "failed":
                raise PublisherError(
                    "cPanel deployment reported failed", provider=WEBSITE_PROVIDER
                )
            await asyncio.sleep(DEPLOY_POLL_INTERVAL_SECONDS)
        raise PublisherTimeoutError(
            "cPanel deployment did not complete before the poll budget was exhausted",
            provider=WEBSITE_PROVIDER,
        )

    async def _trigger_deploy(self) -> str:
        payload = await self._call_uapi(
            "VersionControlDeployment",
            "create",
            {"repository_root": self._cpanel_git_repo_name},
        )
        data = payload.get("data")
        deploy_id = data.get("deploy_id") if isinstance(data, dict) else None
        if not isinstance(deploy_id, (str, int)):
            raise PublisherMalformedResponseError(
                "cPanel deployment response did not include a deploy_id",
                provider=WEBSITE_PROVIDER,
            )
        return str(deploy_id)

    async def _poll_deploy(self, deploy_id: str) -> str:
        # retrieve returns every deployment for this repository as a list, not a single
        # object keyed by ID, and reports outcome via which `timestamps` keys are present
        # rather than a literal status string -- confirmed against this account's live API,
        # not assumed from documentation.
        payload = await self._call_uapi(
            "VersionControlDeployment",
            "retrieve",
            {"repository_root": self._cpanel_git_repo_name},
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise PublisherMalformedResponseError(
                "cPanel deployment status response was malformed", provider=WEBSITE_PROVIDER
            )
        matches = [
            entry
            for entry in data
            if isinstance(entry, dict) and str(entry.get("deploy_id")) == deploy_id
        ]
        if not matches:
            return "pending"
        timestamps = matches[0].get("timestamps")
        if not isinstance(timestamps, dict):
            return "pending"
        if any(key in timestamps for key in ("failed", "error", "errored")):
            return "failed"
        if "succeeded" in timestamps:
            return "complete"
        return "pending"

    async def _call_uapi(
        self, module: str, function: str, params: dict[str, str]
    ) -> dict[str, Any]:
        try:
            response = await self._http.post(f"/execute/{module}/{function}", data=params)
        except httpx.TimeoutException:
            raise PublisherTimeoutError(
                "cPanel UAPI request timed out", provider=WEBSITE_PROVIDER
            ) from None
        except httpx.ConnectError:
            raise PublisherUnavailableError(
                "cPanel could not be reached", provider=WEBSITE_PROVIDER
            ) from None

        if response.status_code in (401, 403):
            raise PublisherAuthenticationError(
                f"cPanel rejected the API token ({response.status_code})",
                provider=WEBSITE_PROVIDER,
            )
        if response.status_code >= 400:
            raise PublisherError(
                f"cPanel UAPI request failed ({response.status_code})", provider=WEBSITE_PROVIDER
            )
        try:
            payload = response.json()
        except ValueError:
            raise PublisherMalformedResponseError(
                "cPanel UAPI returned a non-JSON response", provider=WEBSITE_PROVIDER
            ) from None
        if not isinstance(payload, dict):
            raise PublisherMalformedResponseError(
                "cPanel UAPI response was not a JSON object", provider=WEBSITE_PROVIDER
            )
        if payload.get("status") == 0:
            errors = payload.get("errors") or ["unknown error"]
            raise PublisherError(
                f"cPanel UAPI {module}/{function} failed: {'; '.join(map(str, errors))}",
                provider=WEBSITE_PROVIDER,
            )
        return payload
