import asyncio
import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentic_workshop.adapters.website_publisher import WebsitePublisher
from agentic_workshop.domain.website_content import WebsiteStaticContent
from agentic_workshop.ports.publishing import (
    PublisherAuthenticationError,
    PublisherError,
    PublisherMalformedResponseError,
    PublisherTimeoutError,
    PublisherUnavailableError,
    PublishRequest,
)

STATIC_CONTENT_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "agentic_workshop"
    / "resources"
    / "website"
    / "jordan-and-the-fosters.v1.json"
)


def _static_content() -> WebsiteStaticContent:
    data = json.loads(STATIC_CONTENT_PATH.read_text(encoding="utf-8"))
    return WebsiteStaticContent.model_validate(data)


class FakeGitRunner:
    def __init__(
        self,
        *,
        status_output: str = " M index.html\n",
        rev_parse_output: str = "deadbeef1234\n",
        raise_on: dict[str, BaseException] | None = None,
        fail_action: str | None = None,
        fail_stderr: str = "",
    ) -> None:
        self.calls: list[list[str]] = []
        self._status_output = status_output
        self._rev_parse_output = rev_parse_output
        self._raise_on = raise_on or {}
        self._fail_action = fail_action
        self._fail_stderr = fail_stderr

    async def run(
        self, args: Sequence[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        action = args[0]
        self.calls.append(list(args))
        if action in self._raise_on:
            raise self._raise_on[action]
        if action == self._fail_action:
            return subprocess.CompletedProcess(list(args), 1, stdout="", stderr=self._fail_stderr)
        if action == "status":
            return subprocess.CompletedProcess(
                list(args), 0, stdout=self._status_output, stderr=""
            )
        if action == "rev-parse":
            return subprocess.CompletedProcess(
                list(args), 0, stdout=self._rev_parse_output, stderr=""
            )
        return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")


class FakeGitHubActionsAPI:
    """Stateful fake for the GitHub Actions endpoints WebsitePublisher polls.

    `list_responses` is a queue of workflow_runs lists, one consumed per GET call to the
    runs-list endpoint; the last entry repeats for any further calls once exhausted -- this is
    what lets a single fake represent "run appears after N polls" and "run never completes".
    """

    def __init__(
        self,
        *,
        list_responses: list[list[dict[str, Any]]],
        dispatch_status_code: int = 204,
        list_status_code: int = 200,
    ) -> None:
        self._list_responses = list_responses
        self._dispatch_status_code = dispatch_status_code
        self._list_status_code = list_status_code
        self.list_call_count = 0
        self.dispatch_call_count = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/dispatches"):
            self.dispatch_call_count += 1
            return httpx.Response(self._dispatch_status_code)
        if request.url.path.endswith("/runs"):
            index = min(self.list_call_count, len(self._list_responses) - 1)
            runs = self._list_responses[index]
            self.list_call_count += 1
            return httpx.Response(self._list_status_code, json={"workflow_runs": runs})
        return httpx.Response(404)


def _immediate_success_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/runs"):
        return httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {
                        "id": 1,
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://github.com/owner/repo/actions/runs/1",
                    }
                ]
            },
        )
    return httpx.Response(204)


def _publisher(
    git_runner: FakeGitRunner,
    *,
    working_copy_root: Path,
    actions_handler: Callable[[httpx.Request], httpx.Response] = _immediate_success_handler,
    already_cloned: bool = True,
) -> WebsitePublisher:
    working_copy_root.mkdir(parents=True, exist_ok=True)
    if already_cloned:
        (working_copy_root / ".git").mkdir(exist_ok=True)
    transport = httpx.MockTransport(actions_handler)
    return WebsitePublisher(
        git_runner,
        working_copy_root=working_copy_root,
        github_token="ghp_supersecret",
        github_repo="owner/repo",
        static_content=_static_content(),
        approved_destinations=("https://www.amazon.com/dp/B0D5BT1XDZ",),
        http_transport=transport,
    )


def _request(tmp_path: Path, **overrides: object) -> PublishRequest:
    image_path = tmp_path / "cover.png"
    if not image_path.exists():
        image_path.write_bytes(b"fake-png-bytes")
    defaults: dict[str, object] = {
        "destination_platform": "website",
        "text": "Jordan learns to trust.",
        "title": "When Trust Takes Time",
        "image_path": image_path,
    }
    defaults.update(overrides)
    return PublishRequest(**defaults)


def test_publish_writes_files_commits_pushes_and_deploys(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    git_runner = FakeGitRunner()
    api = FakeGitHubActionsAPI(
        list_responses=[
            [{"id": 1, "status": "queued", "html_url": "https://github.com/owner/repo/1"}],
            [
                {
                    "id": 1,
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.com/owner/repo/1",
                }
            ],
        ]
    )
    publisher = _publisher(git_runner, working_copy_root=working_copy, actions_handler=api.handle)

    response = asyncio.run(publisher.publish(_request(tmp_path)))

    assert response.external_post_id == "deadbeef1234"
    assert response.external_url == "https://jordanandthefosters.fun"
    assert response.provider_metadata["deployed"] is True
    assert (working_copy / "index.html").is_file()
    assert (working_copy / "assets" / "cover.png").is_file()
    assert "When Trust Takes Time" in (working_copy / "index.html").read_text(encoding="utf-8")
    actions = [call[0] for call in git_runner.calls]
    assert actions == ["fetch", "reset", "status", "add", "-c", "push", "rev-parse"]
    # A brand-new commit is found via GitHub's own `on: push` trigger -- no dispatch needed.
    assert api.dispatch_call_count == 0


def test_publish_clones_when_no_existing_working_copy(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    git_runner = FakeGitRunner()
    publisher = _publisher(git_runner, working_copy_root=working_copy, already_cloned=False)

    asyncio.run(publisher.publish(_request(tmp_path)))

    actions = [call[0] for call in git_runner.calls]
    assert actions[0] == "clone"
    clone_call = git_runner.calls[0]
    assert "ghp_supersecret" in clone_call[1]
    assert "owner/repo" in clone_call[1]


def test_publish_skips_commit_and_push_when_nothing_changed(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    git_runner = FakeGitRunner(status_output="")
    api = FakeGitHubActionsAPI(
        list_responses=[
            [],  # existing-run snapshot, before dispatch: nothing yet
            [
                {
                    "id": 1,
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.com/owner/repo/1",
                }
            ],
        ]
    )
    publisher = _publisher(git_runner, working_copy_root=working_copy, actions_handler=api.handle)

    response = asyncio.run(publisher.publish(_request(tmp_path)))

    # The deploy is still force-dispatched even with nothing new to commit -- a prior attempt
    # may have pushed successfully but failed to deploy, and a retry must not silently no-op.
    assert response.provider_metadata["deployed"] is True
    actions = [call[0] for call in git_runner.calls]
    assert "commit" not in actions
    assert "push" not in actions
    assert actions == ["fetch", "reset", "status", "rev-parse"]
    assert api.dispatch_call_count == 1


def test_retry_dispatch_finds_the_new_run_not_a_stale_prior_one(tmp_path: Path) -> None:
    """Exercises the seen_run_ids disambiguation directly: an old, already-completed run for
    this exact commit SHA must never be mistaken for the freshly dispatched one."""
    working_copy = tmp_path / "site"
    git_runner = FakeGitRunner(status_output="")
    stale_run = {
        "id": 1,
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/owner/repo/1",
    }
    fresh_queued = {"id": 2, "status": "queued", "html_url": "https://github.com/owner/repo/2"}
    fresh_done = {
        "id": 2,
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/owner/repo/2",
    }
    api = FakeGitHubActionsAPI(
        list_responses=[
            [stale_run],  # existing-run snapshot, before dispatch
            [fresh_queued, stale_run],  # first poll after dispatch
            [fresh_done, stale_run],  # second poll after dispatch
        ]
    )
    publisher = _publisher(git_runner, working_copy_root=working_copy, actions_handler=api.handle)

    response = asyncio.run(publisher.publish(_request(tmp_path)))

    assert response.provider_metadata["deployed"] is True
    assert api.dispatch_call_count == 1


def test_publish_requires_title(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    publisher = _publisher(FakeGitRunner(), working_copy_root=working_copy)

    with pytest.raises(ValueError, match="title"):
        asyncio.run(publisher.publish(_request(tmp_path, title=None)))


def test_publish_requires_image_path(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    publisher = _publisher(FakeGitRunner(), working_copy_root=working_copy)

    with pytest.raises(ValueError, match="image_path"):
        asyncio.run(publisher.publish(_request(tmp_path, image_path=None)))


def test_git_authentication_failure_is_normalized(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    git_runner = FakeGitRunner(fail_action="push", fail_stderr="fatal: Authentication failed")
    publisher = _publisher(git_runner, working_copy_root=working_copy)

    with pytest.raises(PublisherAuthenticationError):
        asyncio.run(publisher.publish(_request(tmp_path)))


def test_git_connect_failure_is_normalized(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    git_runner = FakeGitRunner(
        fail_action="push", fail_stderr="fatal: Could not resolve host: github.com"
    )
    publisher = _publisher(git_runner, working_copy_root=working_copy)

    with pytest.raises(PublisherUnavailableError):
        asyncio.run(publisher.publish(_request(tmp_path)))


def test_git_generic_failure_is_normalized(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    git_runner = FakeGitRunner(fail_action="push", fail_stderr="fatal: something else broke")
    publisher = _publisher(git_runner, working_copy_root=working_copy)

    with pytest.raises(PublisherError):
        asyncio.run(publisher.publish(_request(tmp_path)))


def test_github_token_never_leaks_into_error_message(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    git_runner = FakeGitRunner(
        fail_action="push",
        fail_stderr=(
            "fatal: unable to access "
            "'https://x-access-token:ghp_supersecret@github.com/owner/repo.git/': "
            "Authentication failed"
        ),
    )
    publisher = _publisher(git_runner, working_copy_root=working_copy)

    with pytest.raises(PublisherAuthenticationError) as captured:
        asyncio.run(publisher.publish(_request(tmp_path)))
    assert "ghp_supersecret" not in str(captured.value)
    assert "***" in str(captured.value)


def test_git_timeout_is_normalized(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    git_runner = FakeGitRunner(
        raise_on={"push": subprocess.TimeoutExpired(cmd=["git", "push"], timeout=60.0)}
    )
    publisher = _publisher(git_runner, working_copy_root=working_copy)

    with pytest.raises(PublisherTimeoutError):
        asyncio.run(publisher.publish(_request(tmp_path)))


def test_git_not_installed_is_normalized(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    git_runner = FakeGitRunner(raise_on={"fetch": FileNotFoundError()})
    publisher = _publisher(git_runner, working_copy_root=working_copy)

    with pytest.raises(PublisherError, match="git executable not found"):
        asyncio.run(publisher.publish(_request(tmp_path)))


def test_github_actions_authentication_failure_is_normalized(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    publisher = _publisher(FakeGitRunner(), working_copy_root=working_copy, actions_handler=handler)

    with pytest.raises(PublisherAuthenticationError):
        asyncio.run(publisher.publish(_request(tmp_path)))


def test_github_actions_non_json_response_is_normalized(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    publisher = _publisher(FakeGitRunner(), working_copy_root=working_copy, actions_handler=handler)

    with pytest.raises(PublisherMalformedResponseError):
        asyncio.run(publisher.publish(_request(tmp_path)))


def test_github_actions_run_conclusion_failure_is_normalized(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"
    api = FakeGitHubActionsAPI(
        list_responses=[
            [
                {
                    "id": 1,
                    "status": "completed",
                    "conclusion": "failure",
                    "html_url": "https://github.com/owner/repo/1",
                }
            ]
        ]
    )
    publisher = _publisher(
        FakeGitRunner(), working_copy_root=working_copy, actions_handler=api.handle
    )

    with pytest.raises(PublisherError, match="failure"):
        asyncio.run(publisher.publish(_request(tmp_path)))


def test_github_actions_poll_budget_exhausted_raises_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentic_workshop.adapters.website_publisher as website_publisher_module

    monkeypatch.setattr(website_publisher_module, "ACTIONS_POLL_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(website_publisher_module, "ACTIONS_POLL_INTERVAL_SECONDS", 0.0)
    working_copy = tmp_path / "site"
    api = FakeGitHubActionsAPI(
        list_responses=[[{"id": 1, "status": "in_progress", "html_url": "https://x"}]]
    )
    publisher = _publisher(
        FakeGitRunner(), working_copy_root=working_copy, actions_handler=api.handle
    )

    with pytest.raises(PublisherTimeoutError):
        asyncio.run(publisher.publish(_request(tmp_path)))


def test_github_actions_dispatch_not_found_is_normalized(tmp_path: Path) -> None:
    working_copy = tmp_path / "site"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json={"workflow_runs": []})
        return httpx.Response(404)

    git_runner = FakeGitRunner(status_output="")  # forces the retry/dispatch path
    publisher = _publisher(git_runner, working_copy_root=working_copy, actions_handler=handler)

    with pytest.raises(PublisherError, match="404"):
        asyncio.run(publisher.publish(_request(tmp_path)))


def test_missing_credentials_fail_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("GITHUB_TOKEN", "GITHUB_REPO"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(PublisherAuthenticationError, match="GITHUB_TOKEN"):
        WebsitePublisher.from_environment(
            working_copy_root=tmp_path / "site",
            static_content=_static_content(),
            approved_destinations=(),
            load_dotenv=False,
        )


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/jdini76/jatf_website.git",
        "git@github.com:jdini76/jatf_website.git",
    ],
)
def test_github_repo_rejects_a_full_clone_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_token")
    monkeypatch.setenv("GITHUB_REPO", value)

    with pytest.raises(PublisherAuthenticationError, match="owner/repo"):
        WebsitePublisher.from_environment(
            working_copy_root=tmp_path / "site",
            static_content=_static_content(),
            approved_destinations=(),
            load_dotenv=False,
        )


def test_dotenv_loading_and_operating_system_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GITHUB_TOKEN=dotenv-token\nGITHUB_REPO=dotenv/repo\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_TOKEN", "os-token")
    monkeypatch.setenv("GITHUB_REPO", "os/repo")

    publisher = WebsitePublisher.from_environment(
        working_copy_root=tmp_path / "site",
        static_content=_static_content(),
        approved_destinations=(),
        env_file=env_file,
    )

    assert publisher._github_token == "os-token"
    assert publisher._github_repo == "os/repo"
