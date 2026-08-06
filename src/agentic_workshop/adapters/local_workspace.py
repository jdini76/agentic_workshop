"""Small standard-library HTTP adapter for the local campaign workspace."""

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import date
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from agentic_workshop.adapters.filesystem_resources import (
    FilesystemResourceLoader,
    ResourceLoadingError,
)
from agentic_workshop.application.brief_review import (
    BriefArtifactConflictError,
    BriefArtifactIdentityError,
    BriefArtifactInvalidError,
    BriefArtifactMissingError,
    BriefReviewAction,
    BriefReviewError,
    LoadedBriefArtifact,
    ReviewWeeklyMarketingBrief,
)
from agentic_workshop.application.campaign_history import (
    CampaignAmbiguityError,
    CampaignArtifactError,
    CampaignNotFoundError,
    CampaignView,
    LoadCampaignHistory,
    parse_campaign_week,
)
from agentic_workshop.application.content_review import (
    ContentArtifactConflictError,
    ContentArtifactIdentityError,
    ContentArtifactInvalidError,
    ContentArtifactMissingError,
    ContentReviewAction,
    ContentReviewError,
    LoadedContentArtifact,
    ReviewContentPackage,
)
from agentic_workshop.application.deterministic_content import (
    DeterministicContentConflictError,
    DeterministicContentPrerequisiteError,
    DeterministicContentWorkflowError,
    GenerateDeterministicContentPackage,
    combined_generation_checksum,
)
from agentic_workshop.application.marketing import MarketingBriefError
from agentic_workshop.application.next_campaign import (
    DuplicateCampaignWeekError,
    NextCampaignWorkflowError,
    StartNextCampaign,
)
from agentic_workshop.application.preview import CampaignPreviewError
from agentic_workshop.application.preview_status import (
    MANIFEST_RESOURCE_TEMPLATE,
    PREVIEW_ROUTE_GUIDANCE,
    GenerateVerifiedCampaignPreview,
    PreviewStatusService,
    PreviewWorkflowConflictError,
    PreviewWorkflowError,
    asset_binding,
)
from agentic_workshop.application.todays_work import TodaysWorkError
from agentic_workshop.domain.assets import ClientAssetManifest
from agentic_workshop.domain.identity import ClientId
from agentic_workshop.presentation.workspace import (
    render_brief,
    render_confirmation,
    render_generation_confirmation,
    render_new_campaign_form,
    render_package,
    render_package_confirmation,
    render_preview_confirmation,
    render_workspace_error,
    render_workspace_home,
)

WORKSPACE_HOST = "127.0.0.1"
SESSION_COOKIE = "agentic_workshop_session"
MAX_FORM_BYTES = 16_384


@dataclass(frozen=True)
class WorkspaceConfig:
    repository_root: Path
    resource_root: Path
    client_id: ClientId
    port: int = 8765

    @property
    def host_header(self) -> str:
        return f"{WORKSPACE_HOST}:{self.port}"

    @property
    def origin(self) -> str:
        return f"http://{self.host_header}"


@dataclass(frozen=True)
class WorkspaceRequest:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes = b""


@dataclass(frozen=True)
class WorkspaceResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


class WorkspaceSecurity:
    """Ephemeral cookie, CSRF, and single-use confirmation protection."""

    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self._used_nonces: set[str] = set()

    def new_session(self) -> str:
        return secrets.token_urlsafe(32)

    def csrf_token(self, session: str) -> str:
        return hmac.new(self._secret, f"csrf:{session}".encode(), hashlib.sha256).hexdigest()

    def confirmation_nonce(
        self,
        *,
        action: str,
        client_id: ClientId,
        week: date,
        checksum: str,
        artifact_identity: str = "brief",
    ) -> str:
        payload = json.dumps(
            {
                "action": action,
                "client_id": str(client_id),
                "week": week.isoformat(),
                "checksum": checksum,
                "artifact_identity": artifact_identity,
                "expires": int(time.time()) + 600,
                "random": secrets.token_urlsafe(18),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def consume_confirmation(
        self,
        token: str,
        *,
        action: str,
        client_id: str,
        week: str,
        checksum: str,
        artifact_identity: str = "brief",
    ) -> bool:
        if token in self._used_nonces:
            return False
        try:
            encoded, signature = token.split(".", maxsplit=1)
            expected = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return False
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
            valid = (
                payload["action"] == action
                and payload["client_id"] == client_id
                and payload["week"] == week
                and payload["checksum"] == checksum
                and payload["artifact_identity"] == artifact_identity
                and int(payload["expires"]) >= int(time.time())
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        if not valid:
            return False
        self._used_nonces.add(token)
        return True

    def new_campaign_nonce(self, client_id: ClientId) -> str:
        """Confirmation for creating a campaign that does not exist yet.

        There is no prior artifact to bind a checksum to, so this is bound only to the action
        and client identity, plus the same expiry and single-use protection as other nonces.
        """
        payload = json.dumps(
            {
                "action": "campaign-new",
                "client_id": str(client_id),
                "expires": int(time.time()) + 600,
                "random": secrets.token_urlsafe(18),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def consume_new_campaign_nonce(self, token: str, *, client_id: str) -> bool:
        if token in self._used_nonces:
            return False
        try:
            encoded, signature = token.split(".", maxsplit=1)
            expected = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return False
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
            valid = (
                payload["action"] == "campaign-new"
                and payload["client_id"] == client_id
                and int(payload["expires"]) >= int(time.time())
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        if not valid:
            return False
        self._used_nonces.add(token)
        return True


class LocalWorkspaceApp:
    """Route local HTML requests to existing application services."""

    def __init__(
        self,
        config: WorkspaceConfig,
        *,
        security: WorkspaceSecurity | None = None,
    ) -> None:
        self._config = config
        self._security = security or WorkspaceSecurity()
        self._review = ReviewWeeklyMarketingBrief()
        self._content_review = ReviewContentPackage()
        self._loader = FilesystemResourceLoader(config.resource_root)
        self._deterministic_content = GenerateDeterministicContentPackage(
            config.repository_root,
            self._loader,
        )
        self._preview_status = PreviewStatusService(config.repository_root, self._loader)
        self._preview_generation = GenerateVerifiedCampaignPreview(
            config.repository_root, self._loader
        )
        self._history = LoadCampaignHistory(
            config.repository_root, self._loader, config.client_id
        )
        self._next_campaign = StartNextCampaign(config.repository_root, self._loader)
        self._stylesheet_path = config.resource_root / "static" / "workspace.css"
        self._preview_stylesheet_path = config.resource_root / "static" / "preview.css"

    def handle(self, request: WorkspaceRequest) -> WorkspaceResponse:
        if request.headers.get("Host") != self._config.host_header:
            return self._route_error(
                request.target,
                HTTPStatus.BAD_REQUEST,
                "Invalid local workspace host.",
            )
        route = urlsplit(request.target)
        if route.scheme or route.netloc:
            return self._route_error(
                route.path,
                HTTPStatus.BAD_REQUEST,
                "Absolute request targets are not accepted.",
            )
        if request.method == "GET":
            return self._get(route.path, route.query, request)
        if request.method == "POST":
            return self._post(route.path, request)
        return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "This action is not supported.")

    def _get(self, path: str, query: str, request: WorkspaceRequest) -> WorkspaceResponse:
        if path == "/workspace.css":
            if query:
                return self._error(
                    HTTPStatus.BAD_REQUEST,
                    "The workspace stylesheet route does not accept parameters.",
                )
            try:
                stylesheet = self._stylesheet_path.read_bytes()
            except FileNotFoundError:
                return self._error(
                    HTTPStatus.NOT_FOUND,
                    "The local workspace stylesheet is missing.",
                )
            return WorkspaceResponse(
                status=HTTPStatus.OK,
                body=stylesheet,
                headers=self._headers(content_type="text/css; charset=utf-8"),
            )
        if path == "/preview.css":
            if query:
                return self._error(HTTPStatus.BAD_REQUEST, "Stylesheet parameters are invalid.")
            try:
                stylesheet = self._preview_stylesheet_path.read_bytes()
            except FileNotFoundError:
                return self._error(HTTPStatus.NOT_FOUND, "The preview stylesheet is missing.")
            return WorkspaceResponse(
                status=HTTPStatus.OK,
                body=stylesheet,
                headers=self._headers(content_type="text/css; charset=utf-8"),
            )
        session, cookie_header = self._session(request)
        try:
            if path == "/" or path.startswith("/campaign/") or path.startswith("/brief"):
                selected, page = self._campaign_route(path)
                if page == "new-campaign":
                    return self._get_new_campaign_page(session, cookie_header)
                campaigns = asyncio.run(self._history.execute())
                campaign = self._select_campaign(campaigns, selected)
                messages = {
                    "campaign-started": "Sarah drafted a new weekly brief for this campaign.",
                    "brief-approved": "Sarah's brief was approved.",
                    "brief-revision-requested": "Revision instructions were recorded for Sarah.",
                    "package-approved": "Casey's package was approved.",
                    "package-revision-requested": (
                        "Revision instructions were recorded for Casey."
                    ),
                }
                result = parse_qs(query).get("result", [""])[0]
                if page == "home":
                    return self._html(
                        HTTPStatus.OK,
                        render_workspace_home(
                            campaign.snapshot,
                            campaigns=campaigns,
                            selected_week=campaign.record.week,
                            message=messages.get(result),
                        ),
                        cookie_header,
                    )
                if page.startswith("preview"):
                    return self._get_preview_page(
                        page,
                        campaign,
                        session,
                        cookie_header,
                    )
                if page.startswith("package"):
                    if page == "package-generate-confirm":
                        loaded_brief = self._load_expected_brief(campaign)
                        package_checksum: str | None = None
                        package_identity = "absent"
                        if campaign.record.package is not None:
                            loaded_existing = self._load_expected_package(campaign)
                            package_checksum = loaded_existing.checksum
                            package_identity = loaded_existing.package.package_id
                        self._ensure_generation_allowed(
                            loaded_brief.brief.approval_state.value,
                            (
                                campaign.record.package.approval_state.value
                                if campaign.record.package is not None
                                else None
                            ),
                        )
                        brief_identity = self._brief_identity(campaign)
                        bound_checksum = combined_generation_checksum(
                            loaded_brief.checksum,
                            package_checksum,
                        )
                        nonce = self._security.confirmation_nonce(
                            action="generate",
                            client_id=loaded_brief.brief.client_id,
                            week=loaded_brief.brief.week,
                            checksum=bound_checksum,
                            artifact_identity=(
                                f"{brief_identity}:{package_identity}"
                            ),
                        )
                        return self._html(
                            HTTPStatus.OK,
                            render_generation_confirmation(
                                campaign_week=campaign.record.week,
                                client_id=str(self._config.client_id),
                                brief_identity=brief_identity,
                                brief_checksum=loaded_brief.checksum,
                                package_identity=package_identity,
                                package_checksum=package_checksum,
                                csrf_token=self._security.csrf_token(session),
                                confirmation_nonce=nonce,
                            ),
                            cookie_header,
                        )
                    loaded_package = self._load_expected_package(campaign)
                    if page == "package":
                        return self._html(
                            HTTPStatus.OK,
                            render_package(
                                loaded_package.package,
                                self._content_review.available_actions(
                                    loaded_package.package
                                ),
                                campaign_week=campaign.record.week,
                            ),
                            cookie_header,
                        )
                    action = {
                        "package-approve-confirm": "approve",
                        "package-revision-confirm": "revision",
                    }.get(page)
                    if action is None:
                        return self._error(
                            HTTPStatus.NOT_FOUND,
                            "That local workspace page was not found.",
                        )
                    content_action = ContentReviewAction(action)
                    self._content_review.ensure_action_allowed(
                        loaded_package.package, content_action
                    )
                    nonce = self._security.confirmation_nonce(
                        action=action,
                        client_id=loaded_package.package.client_id,
                        week=loaded_package.package.week,
                        checksum=loaded_package.checksum,
                        artifact_identity=loaded_package.package.package_id,
                    )
                    return self._html(
                        HTTPStatus.OK,
                        render_package_confirmation(
                            loaded_package.package,
                            action=action,
                            csrf_token=self._security.csrf_token(session),
                            confirmation_nonce=nonce,
                            checksum=loaded_package.checksum,
                            campaign_week=campaign.record.week,
                        ),
                        cookie_header,
                    )
                loaded = self._load_expected_brief(campaign)
                if page == "brief":
                    return self._html(
                        HTTPStatus.OK,
                        render_brief(
                            loaded.brief,
                            self._review.available_actions(loaded.brief),
                            campaign_week=campaign.record.week,
                        ),
                        cookie_header,
                    )
                action = {"approve-confirm": "approve", "revision-confirm": "revision"}.get(page)
                if action is None:
                    return self._error(
                        HTTPStatus.NOT_FOUND,
                        "That local workspace page was not found.",
                    )
                review_action = BriefReviewAction(action)
                self._review.ensure_action_allowed(loaded.brief, review_action)
                nonce = self._security.confirmation_nonce(
                    action=action,
                    client_id=loaded.brief.client_id,
                    week=loaded.brief.week,
                    checksum=loaded.checksum,
                )
                return self._html(
                    HTTPStatus.OK,
                    render_confirmation(
                        loaded.brief,
                        action=action,
                        csrf_token=self._security.csrf_token(session),
                        confirmation_nonce=nonce,
                        checksum=loaded.checksum,
                        campaign_week=campaign.record.week,
                    ),
                    cookie_header,
                )
            return self._route_error(
                path,
                HTTPStatus.NOT_FOUND,
                "That local workspace page was not found.",
            )
        except CampaignAmbiguityError as error:
            return self._route_error(path, HTTPStatus.CONFLICT, str(error), cookie_header)
        except CampaignNotFoundError as error:
            status = HTTPStatus.BAD_REQUEST if "format" in str(error) else HTTPStatus.NOT_FOUND
            return self._route_error(path, status, str(error), cookie_header)
        except BriefArtifactMissingError as error:
            return self._route_error(path, HTTPStatus.NOT_FOUND, str(error), cookie_header)
        except ContentArtifactMissingError as error:
            return self._route_error(path, HTTPStatus.NOT_FOUND, str(error), cookie_header)
        except (
            BriefArtifactInvalidError,
            BriefArtifactIdentityError,
            BriefReviewError,
            TodaysWorkError,
            CampaignArtifactError,
            ContentArtifactInvalidError,
            ContentArtifactIdentityError,
            ContentReviewError,
            DeterministicContentPrerequisiteError,
        ) as error:
            return self._route_error(
                path,
                HTTPStatus.UNPROCESSABLE_ENTITY,
                str(error),
                cookie_header,
            )
        except (OSError, ValidationError, ValueError) as error:
            return self._route_error(
                path,
                HTTPStatus.UNPROCESSABLE_ENTITY,
                f"The local workspace could not load safely: {error}",
                cookie_header,
            )

    def _get_new_campaign_page(
        self, session: str, cookie_header: str | None
    ) -> WorkspaceResponse:
        try:
            campaigns = asyncio.run(self._history.execute())
            existing_weeks = tuple(sorted(view.record.week for view in campaigns))
        except CampaignNotFoundError:
            existing_weeks = ()
        nonce = self._security.new_campaign_nonce(self._config.client_id)
        return self._html(
            HTTPStatus.OK,
            render_new_campaign_form(
                client_id=str(self._config.client_id),
                existing_weeks=existing_weeks,
                csrf_token=self._security.csrf_token(session),
                confirmation_nonce=nonce,
            ),
            cookie_header,
        )

    def _post(self, path: str, request: WorkspaceRequest) -> WorkspaceResponse:
        if request.headers.get("Origin") != self._config.origin:
            return self._route_error(path, HTTPStatus.FORBIDDEN, "Invalid request origin.")
        session = self._read_session(request)
        if session is None:
            return self._route_error(
                path,
                HTTPStatus.FORBIDDEN,
                "The local session cookie is missing.",
            )
        try:
            form = self._form(request)
        except ValueError as error:
            return self._route_error(path, HTTPStatus.BAD_REQUEST, str(error))
        csrf = self._single(form, "csrf_token")
        if csrf is None or not hmac.compare_digest(
            csrf,
            self._security.csrf_token(session),
        ):
            return self._route_error(
                path,
                HTTPStatus.FORBIDDEN,
                "The CSRF token is missing or invalid.",
            )
        if path == "/campaign/new":
            return self._post_new_campaign(form)
        try:
            selected, workflow, action = self._post_campaign_route(path)
            campaigns = asyncio.run(self._history.execute())
            campaign = self._select_campaign(campaigns, selected)
        except CampaignAmbiguityError as error:
            return self._route_error(path, HTTPStatus.CONFLICT, str(error))
        except CampaignNotFoundError as error:
            status = HTTPStatus.BAD_REQUEST if "format" in str(error) else HTTPStatus.NOT_FOUND
            return self._route_error(path, status, str(error))
        except CampaignArtifactError as error:
            return self._route_error(path, HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
        if action not in {"approve", "revision"}:
            if workflow == "package" and action == "generate":
                return self._post_generation(form, campaign)
            if workflow == "preview" and action == "generate":
                return self._post_preview_generation(form, campaign)
            return self._route_error(
                path,
                HTTPStatus.NOT_FOUND,
                "That local action was not found.",
            )
        artifact_identity = "brief"
        if workflow == "package":
            if campaign.record.package is None:
                return self._error(
                    HTTPStatus.NOT_FOUND,
                    "Casey's content package is missing for this campaign.",
                )
            artifact_identity = campaign.record.package.package_id
        checksum = self._single(form, "artifact_checksum") or ""
        client_id = self._single(form, "client_id") or ""
        week = self._single(form, "week") or ""
        nonce = self._single(form, "confirmation_nonce") or ""
        if not self._security.consume_confirmation(
            nonce,
            action=action,
            client_id=client_id,
            week=week,
            checksum=checksum,
            artifact_identity=artifact_identity,
        ):
            return self._error(
                HTTPStatus.FORBIDDEN,
                "The confirmation is missing, invalid, expired, or already used.",
            )
        if (
            client_id != str(self._config.client_id)
            or week != campaign.record.week.isoformat()
        ):
            return self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "The confirmed artifact does not match the current campaign.",
            )
        try:
            if workflow == "package":
                if action == "approve":
                    self._content_review.approve(
                        campaign.record.package_path,
                        expected_checksum=checksum,
                        expected_client_id=self._config.client_id,
                        expected_week=campaign.record.week,
                    )
                    location = f"/campaign/{week}?result=package-approved"
                else:
                    note = self._single(form, "revision_note") or ""
                    self._content_review.request_revision(
                        campaign.record.package_path,
                        note,
                        expected_checksum=checksum,
                        expected_client_id=self._config.client_id,
                        expected_week=campaign.record.week,
                    )
                    location = f"/campaign/{week}?result=package-revision-requested"
            elif action == "approve":
                self._review.approve(
                    campaign.record.brief_path,
                    expected_checksum=checksum,
                    expected_client_id=self._config.client_id,
                    expected_week=campaign.record.week,
                )
                location = f"/campaign/{week}?result=brief-approved"
            else:
                note = self._single(form, "revision_note") or ""
                self._review.request_revision(
                    campaign.record.brief_path,
                    note,
                    expected_checksum=checksum,
                    expected_client_id=self._config.client_id,
                    expected_week=campaign.record.week,
                )
                location = f"/campaign/{week}?result=brief-revision-requested"
        except BriefArtifactConflictError as error:
            return self._error(HTTPStatus.CONFLICT, str(error))
        except BriefArtifactMissingError as error:
            return self._error(HTTPStatus.NOT_FOUND, str(error))
        except BriefArtifactIdentityError as error:
            return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
        except BriefReviewError as error:
            return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
        except ContentArtifactConflictError as error:
            return self._error(HTTPStatus.CONFLICT, str(error))
        except ContentArtifactMissingError as error:
            return self._error(HTTPStatus.NOT_FOUND, str(error))
        except ContentArtifactIdentityError as error:
            return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
        except ContentReviewError as error:
            return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
        return WorkspaceResponse(
            status=HTTPStatus.SEE_OTHER,
            body=b"",
            headers=self._headers({"Location": location}),
        )

    def _post_new_campaign(self, form: dict[str, list[str]]) -> WorkspaceResponse:
        client_id = self._single(form, "client_id") or ""
        nonce = self._single(form, "confirmation_nonce") or ""
        if not self._security.consume_new_campaign_nonce(nonce, client_id=client_id):
            return self._new_campaign_error(
                HTTPStatus.FORBIDDEN,
                "The confirmation is missing, invalid, expired, or already used.",
            )
        if client_id != str(self._config.client_id):
            return self._new_campaign_error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "The confirmed client does not match the configured workspace client.",
            )
        week_value = self._single(form, "week") or ""
        try:
            requested_week = parse_campaign_week(week_value)
        except CampaignNotFoundError as error:
            return self._new_campaign_error(HTTPStatus.BAD_REQUEST, str(error))
        try:
            started = asyncio.run(
                self._next_campaign.execute(
                    client_id=self._config.client_id,
                    requested_week=requested_week,
                )
            )
        except DuplicateCampaignWeekError as error:
            return self._new_campaign_error(
                HTTPStatus.CONFLICT, str(error), existing_week=error.week
            )
        except (
            NextCampaignWorkflowError,
            MarketingBriefError,
            ResourceLoadingError,
            OSError,
            ValidationError,
            ValueError,
        ) as error:
            return self._new_campaign_error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
        return WorkspaceResponse(
            status=HTTPStatus.SEE_OTHER,
            body=b"",
            headers=self._headers(
                {
                    "Location": (
                        f"/campaign/{started.brief.week.isoformat()}?result=campaign-started"
                    )
                }
            ),
        )

    def _new_campaign_error(
        self,
        status: int,
        internal_message: str,
        *,
        existing_week: date | None = None,
    ) -> WorkspaceResponse:
        del internal_message
        conflict_message = (
            f"A campaign for {existing_week.isoformat()} already exists."
            if existing_week is not None
            else "A campaign for that week already exists."
        )
        safe_messages = {
            HTTPStatus.BAD_REQUEST: "Enter the campaign week as a valid date.",
            HTTPStatus.FORBIDDEN: "The new-campaign request could not be verified.",
            HTTPStatus.CONFLICT: conflict_message,
            HTTPStatus.UNPROCESSABLE_ENTITY: (
                "Sarah could not draft that campaign yet. Review the client profile."
            ),
        }
        message = safe_messages.get(
            HTTPStatus(status), "The new campaign could not be started."
        )
        return_path = (
            f"/campaign/{existing_week.isoformat()}"
            if existing_week is not None
            else "/campaign/new"
        )
        return_label = (
            f"Open the {existing_week.isoformat()} campaign"
            if existing_week is not None
            else "Return to start a new campaign"
        )
        return self._html(
            status,
            render_workspace_error(
                "Campaign can't be started yet",
                message,
                return_path=return_path,
                return_label=return_label,
            ),
        )

    def _post_generation(
        self,
        form: dict[str, list[str]],
        campaign: CampaignView,
    ) -> WorkspaceResponse:
        client_id = self._single(form, "client_id") or ""
        week = self._single(form, "week") or ""
        brief_identity = self._single(form, "brief_identity") or ""
        brief_checksum = self._single(form, "brief_checksum") or ""
        package_identity = self._single(form, "package_identity") or ""
        package_checksum_value = self._single(form, "package_checksum") or ""
        package_checksum = package_checksum_value or None
        nonce = self._single(form, "confirmation_nonce") or ""
        bound_checksum = combined_generation_checksum(brief_checksum, package_checksum)
        if not self._security.consume_confirmation(
            nonce,
            action="generate",
            client_id=client_id,
            week=week,
            checksum=bound_checksum,
            artifact_identity=f"{brief_identity}:{package_identity}",
        ):
            return self._campaign_generation_error(
                campaign.record.week,
                HTTPStatus.FORBIDDEN,
                "The generation confirmation is missing, invalid, expired, or already used.",
            )
        if (
            client_id != str(self._config.client_id)
            or week != campaign.record.week.isoformat()
            or brief_identity != self._brief_identity(campaign)
        ):
            return self._campaign_generation_error(
                campaign.record.week,
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "The confirmed generation inputs do not match the selected campaign.",
            )
        try:
            generated = asyncio.run(
                self._deterministic_content.execute(
                    brief_path=campaign.record.brief_path,
                    package_path=campaign.record.package_path,
                    expected_client_id=self._config.client_id,
                    expected_week=campaign.record.week,
                    expected_brief_checksum=brief_checksum,
                    expected_package_checksum=package_checksum,
                    expected_package_identity=(
                        package_identity if package_identity != "absent" else None
                    ),
                    expect_package_absent=package_identity == "absent",
                )
            )
        except DeterministicContentConflictError as error:
            return self._campaign_generation_error(
                campaign.record.week, HTTPStatus.CONFLICT, str(error)
            )
        except DeterministicContentWorkflowError as error:
            return self._campaign_generation_error(
                campaign.record.week,
                HTTPStatus.UNPROCESSABLE_ENTITY,
                str(error),
            )
        except (OSError, ValidationError, ValueError):
            return self._campaign_generation_error(
                campaign.record.week,
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "The deterministic generation inputs could not be validated.",
            )
        except Exception:
            return self._campaign_generation_error(
                campaign.record.week,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Deterministic generation failed unexpectedly.",
            )
        return WorkspaceResponse(
            status=HTTPStatus.SEE_OTHER,
            body=b"",
            headers=self._headers(
                {
                    "Location": (
                        f"/campaign/{generated.package.week.isoformat()}/package"
                        "?result=package-generated"
                    )
                }
            ),
        )

    def _get_preview_page(
        self,
        page: str,
        campaign: CampaignView,
        session: str,
        cookie_header: str | None,
    ) -> WorkspaceResponse:
        status = asyncio.run(
            self._preview_status.inspect(
                client_id=self._config.client_id,
                week=campaign.record.week,
                package_path=campaign.record.package_path,
                preview_directory=campaign.record.preview_path.parent,
            )
        )
        if page == "preview":
            if status.state != "current" or status.provenance is None:
                status_code = {
                    "missing": HTTPStatus.NOT_FOUND,
                    "stale": HTTPStatus.CONFLICT,
                    "unverified": HTTPStatus.CONFLICT,
                    "invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
                }[status.state]
                return self._preview_error(
                    campaign.record.week,
                    status_code,
                    status.diagnostic,
                    preview_state=status.state,
                )
            try:
                content = campaign.record.preview_path.read_bytes()
            except OSError:
                return self._preview_error(
                    campaign.record.week, HTTPStatus.NOT_FOUND, "Preview HTML is missing."
                )
            return WorkspaceResponse(
                status=HTTPStatus.OK,
                body=content,
                headers=self._headers(content_type="text/html; charset=utf-8"),
            )
        if page.startswith("preview-asset:"):
            return self._serve_preview_asset(page.split(":", 1)[1], campaign, status)
        if page != "preview-generate-confirm":
            return self._preview_error(
                campaign.record.week, HTTPStatus.NOT_FOUND, "Preview route not found."
            )
        loaded = self._load_expected_package(campaign)
        if loaded.package.approval_state.value != "approved":
            return self._preview_error(
                campaign.record.week,
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "Casey's package must be approved first.",
            )
        if status.state == "current":
            return self._preview_error(
                campaign.record.week,
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "The current preview does not need regeneration.",
            )
        manifest = ClientAssetManifest.model_validate_json(
            asyncio.run(
                self._loader.load_text(
                    MANIFEST_RESOURCE_TEMPLATE.format(client_id=self._config.client_id)
                )
            )
        )
        binding = asset_binding(manifest, loaded.package)
        bound = combined_generation_checksum(loaded.checksum, binding)
        nonce = self._security.confirmation_nonce(
            action="preview-generate",
            client_id=self._config.client_id,
            week=campaign.record.week,
            checksum=bound,
            artifact_identity=f"{loaded.package.package_id}:{status.state}",
        )
        return self._html(
            HTTPStatus.OK,
            render_preview_confirmation(
                campaign_week=campaign.record.week,
                preview_state=status.state,
                client_id=str(self._config.client_id),
                package_id=loaded.package.package_id,
                package_checksum=loaded.checksum,
                asset_binding=binding,
                csrf_token=self._security.csrf_token(session),
                confirmation_nonce=nonce,
            ),
            cookie_header,
        )

    def _post_preview_generation(
        self, form: dict[str, list[str]], campaign: CampaignView
    ) -> WorkspaceResponse:
        client_id = self._single(form, "client_id") or ""
        week = self._single(form, "week") or ""
        package_id = self._single(form, "package_id") or ""
        package_checksum = self._single(form, "package_checksum") or ""
        preview_state = self._single(form, "preview_state") or ""
        asset_checksum = self._single(form, "asset_binding") or ""
        nonce = self._single(form, "confirmation_nonce") or ""
        bound = combined_generation_checksum(package_checksum, asset_checksum)
        if not self._security.consume_confirmation(
            nonce,
            action="preview-generate",
            client_id=client_id,
            week=week,
            checksum=bound,
            artifact_identity=f"{package_id}:{preview_state}",
        ):
            return self._preview_error(
                campaign.record.week, HTTPStatus.FORBIDDEN, "Invalid preview confirmation."
            )
        if client_id != str(self._config.client_id) or week != campaign.record.week.isoformat():
            return self._preview_error(
                campaign.record.week, HTTPStatus.UNPROCESSABLE_ENTITY, "Campaign mismatch."
            )
        try:
            asyncio.run(
                self._preview_generation.execute(
                    client_id=self._config.client_id,
                    week=campaign.record.week,
                    package_path=campaign.record.package_path,
                    expected_package_checksum=package_checksum,
                    expected_state=preview_state,
                    expected_asset_binding=asset_checksum,
                )
            )
        except PreviewWorkflowConflictError as error:
            return self._preview_error(campaign.record.week, HTTPStatus.CONFLICT, str(error))
        except (PreviewWorkflowError, CampaignPreviewError, ValidationError) as error:
            return self._preview_error(
                campaign.record.week, HTTPStatus.UNPROCESSABLE_ENTITY, str(error)
            )
        except Exception:
            return self._preview_error(
                campaign.record.week, HTTPStatus.INTERNAL_SERVER_ERROR, "Preview generation failed."
            )
        return WorkspaceResponse(
            status=HTTPStatus.SEE_OTHER,
            body=b"",
            headers=self._headers(
                {"Location": f"/campaign/{campaign.record.week.isoformat()}"}
            ),
        )

    def _serve_preview_asset(
        self,
        name: str,
        campaign: CampaignView,
        status: object,
    ) -> WorkspaceResponse:
        from agentic_workshop.application.preview_status import PreviewStatus

        if not isinstance(status, PreviewStatus) or status.state != "current":
            return self._preview_error(
                campaign.record.week, HTTPStatus.CONFLICT, "Preview is not current."
            )
        if (
            status.provenance is None
            or not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "%" in name
        ):
            return self._preview_error(
                campaign.record.week, HTTPStatus.NOT_FOUND, "Preview asset not found."
            )
        matches = [asset for asset in status.provenance.assets if asset.copied_name == name]
        if len(matches) != 1:
            return self._preview_error(
                campaign.record.week, HTTPStatus.NOT_FOUND, "Preview asset not found."
            )
        path = status.preview_directory / "assets" / name
        try:
            resolved = path.resolve(strict=True)
            assets_root = (status.preview_directory / "assets").resolve(strict=True)
            if path.is_symlink() or not resolved.is_relative_to(assets_root):
                raise OSError("unsafe preview asset")
            content = path.read_bytes()
        except OSError:
            return self._preview_error(
                campaign.record.week, HTTPStatus.NOT_FOUND, "Preview asset not found."
            )
        if hashlib.sha256(content).hexdigest() != matches[0].copied_sha256:
            return self._preview_error(
                campaign.record.week, HTTPStatus.CONFLICT, "Preview asset checksum failed."
            )
        return WorkspaceResponse(
            status=HTTPStatus.OK,
            body=content,
            headers=self._headers(content_type="image/png"),
        )

    def _preview_error(
        self,
        week: date,
        status: int,
        message: str,
        cookie_header: str | None = None,
        preview_state: str | None = None,
    ) -> WorkspaceResponse:
        generic_safe = {
            HTTPStatus.NOT_FOUND: "The requested local preview file is unavailable.",
            HTTPStatus.CONFLICT: "The preview is no longer current. Regenerate it before review.",
            HTTPStatus.UNPROCESSABLE_ENTITY: "The preview prerequisites are not satisfied.",
            HTTPStatus.FORBIDDEN: "The preview request could not be verified.",
        }.get(HTTPStatus(status), "The local preview could not be prepared.")
        del message
        heading, safe = PREVIEW_ROUTE_GUIDANCE.get(
            preview_state or "",
            ("Campaign preview isn't ready", generic_safe),
        )
        return self._html(
            status,
            render_workspace_error(
                heading,
                safe,
                return_path=f"/campaign/{week.isoformat()}",
                return_label=f"Return to campaign {week.isoformat()}",
            ),
            cookie_header,
        )

    def _load_expected_brief(self, campaign: CampaignView) -> LoadedBriefArtifact:
        loaded = self._review.load(campaign.record.brief_path)
        if (
            loaded.brief.client_id != self._config.client_id
            or loaded.brief.week != campaign.record.week
        ):
            raise BriefArtifactIdentityError(
                "Sarah's brief does not match the configured client and campaign week."
            )
        return loaded

    def _load_expected_package(self, campaign: CampaignView) -> LoadedContentArtifact:
        loaded = self._content_review.load(campaign.record.package_path)
        if (
            loaded.package.client_id != self._config.client_id
            or loaded.package.week != campaign.record.week
        ):
            raise ContentArtifactIdentityError(
                "Casey's package does not match the configured client and campaign week."
            )
        return loaded

    @staticmethod
    def _brief_identity(campaign: CampaignView) -> str:
        brief = campaign.record.brief
        if brief is None:
            return "missing"
        return f"{brief.client_id}:{brief.week.isoformat()}:{brief.employee_id}"

    @staticmethod
    def _ensure_generation_allowed(
        brief_state: str,
        package_state: str | None,
    ) -> None:
        if brief_state != "approved":
            raise DeterministicContentPrerequisiteError(
                "Sarah's brief must be approved before Casey can generate content."
            )
        if package_state not in {None, "revision_requested"}:
            raise DeterministicContentPrerequisiteError(
                "Casey generation is allowed only when the package is missing "
                "or revision requested."
            )

    @staticmethod
    def _select_campaign(
        campaigns: tuple[CampaignView, ...], selected: date | None
    ) -> CampaignView:
        if selected is None:
            return campaigns[-1]
        matches = [item for item in campaigns if item.record.week == selected]
        if len(matches) != 1:
            raise CampaignNotFoundError(
                f"No campaign exists for {selected.isoformat()}."
            )
        return matches[0]

    @staticmethod
    def _campaign_route(path: str) -> tuple[date | None, str]:
        if path == "/":
            return None, "home"
        if path == "/campaign/new":
            return None, "new-campaign"
        legacy = {
            "/brief": "brief",
            "/brief/approve/confirm": "approve-confirm",
            "/brief/revision/confirm": "revision-confirm",
        }
        if path in legacy:
            return None, legacy[path]
        parts = path.strip("/").split("/")
        if len(parts) < 2 or parts[0] != "campaign":
            raise CampaignNotFoundError("That campaign route is not available.")
        week = parse_campaign_week(parts[1])
        suffix = parts[2:]
        pages = {
            (): "home",
            ("brief",): "brief",
            ("brief", "approve", "confirm"): "approve-confirm",
            ("brief", "revision", "confirm"): "revision-confirm",
            ("package",): "package",
            ("package", "approve", "confirm"): "package-approve-confirm",
            ("package", "revision", "confirm"): "package-revision-confirm",
            ("package", "generate", "confirm"): "package-generate-confirm",
            ("preview",): "preview",
            ("preview", "generate", "confirm"): "preview-generate-confirm",
        }
        if len(suffix) == 3 and suffix[:2] == ["preview", "assets"]:
            return week, f"preview-asset:{suffix[2]}"
        page = pages.get(tuple(suffix))
        if page is None:
            raise CampaignNotFoundError("That campaign route is not available.")
        return week, page

    @staticmethod
    def _post_campaign_route(path: str) -> tuple[date | None, str, str]:
        if path in {"/brief/approve", "/brief/revision"}:
            return None, "brief", path.rsplit("/", maxsplit=1)[-1]
        parts = path.strip("/").split("/")
        if (
            len(parts) != 4
            or parts[0] != "campaign"
            or parts[2] not in {"brief", "package", "preview"}
        ):
            raise CampaignNotFoundError("That local action was not found.")
        return parse_campaign_week(parts[1]), parts[2], parts[3]

    def _session(self, request: WorkspaceRequest) -> tuple[str, str | None]:
        existing = self._read_session(request)
        if existing is not None:
            return existing, None
        session = self._security.new_session()
        return session, (
            f"{SESSION_COOKIE}={session}; Path=/; HttpOnly; SameSite=Strict"
        )

    @staticmethod
    def _read_session(request: WorkspaceRequest) -> str | None:
        cookie = SimpleCookie()
        cookie.load(request.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel is not None and morsel.value else None

    @staticmethod
    def _form(request: WorkspaceRequest) -> dict[str, list[str]]:
        if len(request.body) > MAX_FORM_BYTES:
            raise ValueError("The submitted form is too large.")
        if request.headers.get("Content-Type", "").split(";", maxsplit=1)[0] != (
            "application/x-www-form-urlencoded"
        ):
            raise ValueError("Only local HTML form submissions are accepted.")
        return parse_qs(request.body.decode("utf-8"), keep_blank_values=True)

    @staticmethod
    def _single(form: dict[str, list[str]], key: str) -> str | None:
        values = form.get(key)
        return values[0] if values is not None and len(values) == 1 else None

    def _html(
        self,
        status: int,
        content: str,
        cookie_header: str | None = None,
    ) -> WorkspaceResponse:
        extra = {"Set-Cookie": cookie_header} if cookie_header else {}
        return WorkspaceResponse(
            status=status,
            body=content.encode(),
            headers=self._headers(extra=extra),
        )

    def _error(
        self,
        status: int,
        message: str,
        cookie_header: str | None = None,
    ) -> WorkspaceResponse:
        return self._html(
            status,
            render_workspace_error(HTTPStatus(status).phrase, message),
            cookie_header,
        )

    def _route_error(
        self,
        path: str,
        status: int,
        message: str,
        cookie_header: str | None = None,
    ) -> WorkspaceResponse:
        preview_week = self._preview_generation_route_week(path)
        if preview_week is not None:
            return self._preview_error(
                preview_week,
                status,
                message,
                cookie_header,
            )
        week = self._generation_route_week(path)
        if week is not None:
            return self._campaign_generation_error(
                week,
                status,
                message,
                cookie_header,
            )
        return self._error(status, message, cookie_header)

    def _campaign_generation_error(
        self,
        week: date,
        status: int,
        internal_message: str,
        cookie_header: str | None = None,
    ) -> WorkspaceResponse:
        del internal_message
        safe_messages = {
            HTTPStatus.BAD_REQUEST: "The generation request could not be understood.",
            HTTPStatus.FORBIDDEN: "The generation request could not be verified.",
            HTTPStatus.NOT_FOUND: "Required campaign work is missing.",
            HTTPStatus.CONFLICT: "The campaign changed after confirmation. Return and try again.",
            HTTPStatus.UNPROCESSABLE_ENTITY: (
                "Review the campaign prerequisites before generating Casey's package."
            ),
        }
        message = safe_messages.get(
            HTTPStatus(status),
            "Casey's package could not be generated.",
        )
        return self._html(
            status,
            render_workspace_error(
                "Casey's package can't be generated yet",
                message,
                return_path=f"/campaign/{week.isoformat()}",
                return_label=f"Return to campaign {week.isoformat()}",
            ),
            cookie_header,
        )

    @staticmethod
    def _generation_route_week(path: str) -> date | None:
        route_path = urlsplit(path).path
        parts = route_path.strip("/").split("/")
        if (
            len(parts) < 4
            or parts[0] != "campaign"
            or parts[2:4] != ["package", "generate"]
        ):
            return None
        try:
            return parse_campaign_week(parts[1])
        except CampaignNotFoundError:
            return None

    @staticmethod
    def _preview_generation_route_week(path: str) -> date | None:
        route_path = urlsplit(path).path
        parts = route_path.strip("/").split("/")
        if (
            len(parts) < 4
            or parts[0] != "campaign"
            or parts[2:4] != ["preview", "generate"]
        ):
            return None
        try:
            return parse_campaign_week(parts[1])
        except CampaignNotFoundError:
            return None

    @staticmethod
    def _headers(
        extra: dict[str, str] | None = None,
        *,
        content_type: str = "text/html; charset=utf-8",
    ) -> dict[str, str]:
        headers = {
            "Content-Type": content_type,
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'self'; script-src 'none'; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            ),
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            # "same-origin", not "no-referrer": Chrome computes the Origin header for
            # form-navigation POSTs from the referrer policy in effect, and under
            # "no-referrer" it sends the literal string "null" even for genuinely
            # same-origin submissions -- which the Origin check below then correctly
            # rejects as forged. "same-origin" preserves the same privacy guarantee
            # (the referrer is still never sent to the one external link this app has,
            # the Amazon URL) while letting real-browser same-origin POSTs compute
            # correctly. Loosening the Origin check to accept "null" instead would
            # reopen the exact cross-origin/sandboxed-iframe CSRF vector it exists to
            # block, since forged requests also send "null".
            "Referrer-Policy": "same-origin",
        }
        headers.update(extra or {})
        return headers


class WorkspaceRequestHandler(BaseHTTPRequestHandler):
    """Translate standard-library HTTP requests into local app requests."""

    app: ClassVar[LocalWorkspaceApp]
    server_version = "AgenticWorkshopLocal/1"
    sys_version = ""

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_FORM_BYTES:
            body = b""
        else:
            body = self.rfile.read(length)
        response = self.app.handle(
            WorkspaceRequest(
                method=method,
                target=self.path,
                headers={key: value for key, value in self.headers.items()},
                body=body,
            )
        )
        self.send_response(response.status)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        self.wfile.write(response.body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def serve_workspace(config: WorkspaceConfig) -> None:
    """Serve the workspace on the fixed literal IPv4 loopback address."""
    WorkspaceRequestHandler.app = LocalWorkspaceApp(config)
    server = ThreadingHTTPServer((WORKSPACE_HOST, config.port), WorkspaceRequestHandler)
    print(f"Agentic Workshop is available at {config.origin}/")
    print("Press Ctrl+C to stop the local workspace.")
    print("While it is running, use this workspace as the sole workflow writer.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
