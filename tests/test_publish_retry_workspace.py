from datetime import UTC, datetime
from pathlib import Path

from agentic_workshop.adapters.local_workspace import (
    LocalWorkspaceApp,
    WorkspaceConfig,
    WorkspaceSecurity,
)
from agentic_workshop.application.publish_content_package import PublishApprovedContentPackage
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.marketing import BriefApprovalState
from agentic_workshop.domain.publication import PublicationRecord, PublicationStatus
from agentic_workshop.ports.publishing import Publisher, PublishRequest, PublishResponse
from tests.test_local_workspace import (
    CLIENT_ID,
    add_campaign,
    get,
    local_config,
    package_confirmation,
    package_post,
)

WEEK = "2026-08-10"


class FakePublisher(Publisher):
    def __init__(self) -> None:
        self.calls: list[PublishRequest] = []

    async def publish(self, request: PublishRequest) -> PublishResponse:
        self.calls.append(request)
        return PublishResponse(external_post_id="retry-1", external_url="https://example.test/1")


def _write_failed_record(root: Path, *, platform: str, package_id: str) -> None:
    record = PublicationRecord(
        destination_platform=platform,
        client_id=CLIENT_ID,
        campaign_week=WEEK,
        content_package_id=package_id,
        content_package_sha256="a" * 64,
        status=PublicationStatus.FAILED,
        attempted_at=datetime.now(UTC),
        error_detail="simulated prior failure",
        attempt_count=1,
    )
    filename = f"jordan-and-the-fosters-{WEEK}-content-{platform}.json"
    path = root / "artifacts" / "publications" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")


def _approved_package(tmp_path: Path) -> tuple[WorkspaceConfig, Path, ContentPackage]:
    config = local_config(tmp_path)
    _, package_path = add_campaign(config, WEEK)
    package = ContentPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
    updated = package.model_copy(update={"approval_state": BriefApprovalState.APPROVED})
    package_path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
    return config, package_path, updated


def test_retry_confirm_page_requires_a_failed_publication(tmp_path: Path) -> None:
    config, _, _ = _approved_package(tmp_path)
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"retry-none"))

    result = get(app, config, f"/campaign/{WEEK}/package/publish-retry-facebook_page/confirm")
    assert result.status == 422


def test_retry_confirm_and_post_reinvokes_orchestrator(tmp_path: Path) -> None:
    config, _, package = _approved_package(tmp_path)
    _write_failed_record(
        config.repository_root, platform="facebook_page", package_id=package.package_id
    )
    publisher = FakePublisher()
    orchestrator = PublishApprovedContentPackage(
        config.repository_root,
        facebook_publisher=publisher,
        website_publisher=None,
        enabled=True,
    )
    app = LocalWorkspaceApp(
        config, security=WorkspaceSecurity(b"retry-flow"), publish_orchestrator=orchestrator
    )

    cookie, fields = package_confirmation(app, config, WEEK, "publish-retry-facebook_page")
    response = package_post(
        app, config, WEEK, "publish-retry-facebook_page", cookie, fields
    )

    assert response.status == 303
    assert response.headers["Location"] == f"/campaign/{WEEK}?result=package-approved"
    record_path = (
        config.repository_root
        / "artifacts"
        / "publications"
        / f"jordan-and-the-fosters-{WEEK}-content-facebook_page.json"
    )
    updated_record = PublicationRecord.model_validate_json(
        record_path.read_text(encoding="utf-8")
    )
    assert updated_record.attempt_count == 2


def test_retry_nonce_cannot_be_replayed_across_platforms(tmp_path: Path) -> None:
    config, _, package = _approved_package(tmp_path)
    _write_failed_record(
        config.repository_root, platform="facebook_page", package_id=package.package_id
    )
    _write_failed_record(
        config.repository_root, platform="website", package_id=package.package_id
    )
    app = LocalWorkspaceApp(config, security=WorkspaceSecurity(b"retry-cross-platform"))

    cookie, fields = package_confirmation(app, config, WEEK, "publish-retry-facebook_page")
    cross_platform_response = package_post(
        app, config, WEEK, "publish-retry-website", cookie, fields
    )

    assert cross_platform_response.status == 403
