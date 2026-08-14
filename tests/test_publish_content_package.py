import asyncio
import binascii
import hashlib
import struct
import zlib
from datetime import date
from pathlib import Path

import pytest

from agentic_workshop.application.assets import PNG_SIGNATURE
from agentic_workshop.application.publish_content_package import (
    FACEBOOK_ASSET_USE,
    WEBSITE_ASSET_USE,
    PublishApprovedContentPackage,
    PublishContentPackageError,
)
from agentic_workshop.domain.assets import AssetRecommendation, ClientAssetManifest
from agentic_workshop.domain.content import ContentDraft, ContentPackage
from agentic_workshop.domain.marketing import BriefApprovalState
from agentic_workshop.domain.publication import PublicationRecord, PublicationStatus
from agentic_workshop.ports.publishing import (
    Publisher,
    PublisherContentRejectedError,
    PublishRequest,
    PublishResponse,
)

CHECKSUM_A = hashlib.sha256(b"content-package-a").hexdigest()
CHECKSUM_B = hashlib.sha256(b"content-package-b").hexdigest()


class FakePublisher(Publisher):
    def __init__(self, outcomes: list[PublishResponse | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[PublishRequest] = []

    async def publish(self, request: PublishRequest) -> PublishResponse:
        self.calls.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _png_chunk(name: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(name + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", checksum)


def _make_test_png() -> bytes:
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x00\x00\x00")
    return PNG_SIGNATURE + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", pixels) + _png_chunk(
        b"IEND", b""
    )


def _write_manifest(root: Path, *, permitted_uses: tuple[str, ...]) -> ClientAssetManifest:
    png = _make_test_png()
    original = root / "assets" / "cover.png"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(png)
    payload = {
        "schema_version": 1,
        "manifest_revision": 1,
        "client_id": "jordan-and-the-fosters",
        "source_reference": "client-assets/jordan-and-the-fosters.v1.json",
        "assets": [
            {
                "asset_id": "cover",
                "asset_version": 1,
                "name": "Cover",
                "description": "Approved cover",
                "asset_type": "front_cover",
                "repository_path": "assets/cover.png",
                "file_format": "png",
                "dimensions": {"width_px": 1, "height_px": 1},
                "file_size_bytes": len(png),
                "checksum": {
                    "algorithm": "sha256",
                    "value": hashlib.sha256(png).hexdigest(),
                },
                "source": {
                    "source_type": "ceo_supplied_local_file",
                    "description": "Supplied locally by CEO",
                },
                "approval_state": "approved",
                "revision_note": None,
                "approved_uses": list(permitted_uses),
                "permitted_transformations": [],
                "attribution": {"text": None, "required": False, "status": "not_confirmed"},
                "restrictions": ["Do not transform"],
            }
        ],
    }
    return ClientAssetManifest.model_validate(payload)


def _draft(
    *, channel: str, assignment: str, body: str, permitted_uses: tuple[str, ...] = ()
) -> ContentDraft:
    asset_recommendations: tuple[AssetRecommendation, ...] = ()
    if permitted_uses:
        asset_recommendations = (
            AssetRecommendation(
                asset_id="cover",
                asset_type="front_cover",
                repository_path="assets/cover.png",
                manifest_source="client-assets/jordan-and-the-fosters.v1.json",
                availability="available",
                diagnostic="verified",
                approved_use="content_package_asset_recommendation",
                permitted_uses=permitted_uses,
            ),
        )
    return ContentDraft(
        assignment=assignment,
        channel=channel,
        title="Jordan and the Fosters",
        body=body,
        brand_voice_applied=("warm",),
        approved_facts_used=(),
        source_references=("clients/jordan-and-the-fosters.v1.json",),
        missing_assets_or_information=(),
        required_assets=(),
        asset_recommendations=asset_recommendations,
    )


def _social_draft(*, permitted_uses: tuple[str, ...] = ()) -> ContentDraft:
    return _draft(
        channel="Social Media",
        assignment="Facebook post",
        body="Check out the new book!",
        permitted_uses=permitted_uses,
    )


def _website_draft(*, permitted_uses: tuple[str, ...] = ()) -> ContentDraft:
    return _draft(
        channel="Official Website",
        assignment="Website pitch",
        body="Jordan learns to trust.",
        permitted_uses=permitted_uses,
    )


def _package(
    *,
    drafts: tuple[ContentDraft, ...],
    approval_state: BriefApprovalState = BriefApprovalState.APPROVED,
) -> ContentPackage:
    return ContentPackage(
        package_id="jordan-and-the-fosters-2026-08-03-content",
        client_id="jordan-and-the-fosters",
        employee_id="casey",
        week=date(2026, 8, 3),
        approved_brief_source="artifacts/weekly-briefs/jordan-and-the-fosters-2026-08-03.json",
        client_profile_source="clients/jordan-and-the-fosters.v1.json",
        brand_voice=("warm",),
        drafts=drafts,
        assumptions=(),
        missing_assets_or_information=(),
        required_assets=(),
        approval_state=approval_state,
    )


def _orchestrator(
    root: Path,
    *,
    facebook_publisher: Publisher | None = None,
    website_publisher: Publisher | None = None,
    enabled: bool = True,
) -> PublishApprovedContentPackage:
    return PublishApprovedContentPackage(
        root,
        facebook_publisher=facebook_publisher,
        website_publisher=website_publisher,
        enabled=enabled,
    )


def _write_two_asset_manifest(root: Path, *, permitted_uses: tuple[str, ...]) -> tuple[
    ClientAssetManifest, tuple[AssetRecommendation, ...]
]:
    """Two approved, equally-eligible assets, for testing weekly rotation among them."""
    recommendations = []
    entries = []
    for asset_id, filename in (("cover", "cover.png"), ("photo", "photo.png")):
        png = _make_test_png()
        path = root / "assets" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        entries.append(
            {
                "asset_id": asset_id,
                "asset_version": 1,
                "name": asset_id,
                "description": "Approved asset",
                "asset_type": "front_cover",
                "repository_path": f"assets/{filename}",
                "file_format": "png",
                "dimensions": {"width_px": 1, "height_px": 1},
                "file_size_bytes": len(png),
                "checksum": {"algorithm": "sha256", "value": hashlib.sha256(png).hexdigest()},
                "source": {
                    "source_type": "ceo_supplied_local_file",
                    "description": "Supplied locally by CEO",
                },
                "approval_state": "approved",
                "revision_note": None,
                "approved_uses": list(permitted_uses),
                "permitted_transformations": [],
                "attribution": {"text": None, "required": False, "status": "not_confirmed"},
                "restrictions": ["Do not transform"],
            }
        )
        recommendations.append(
            AssetRecommendation(
                asset_id=asset_id,
                asset_type="front_cover",
                repository_path=f"assets/{filename}",
                manifest_source="client-assets/jordan-and-the-fosters.v1.json",
                availability="available",
                diagnostic="verified",
                approved_use="content_package_asset_recommendation",
                permitted_uses=permitted_uses,
            )
        )
    manifest = ClientAssetManifest.model_validate(
        {
            "schema_version": 1,
            "manifest_revision": 1,
            "client_id": "jordan-and-the-fosters",
            "source_reference": "client-assets/jordan-and-the-fosters.v1.json",
            "assets": entries,
        }
    )
    return manifest, tuple(recommendations)


def test_asset_selection_rotates_deterministically_across_the_eligible_pool(
    tmp_path: Path,
) -> None:
    manifest, recommendations = _write_two_asset_manifest(
        tmp_path, permitted_uses=(FACEBOOK_ASSET_USE,)
    )
    draft = ContentDraft(
        assignment="Facebook post",
        channel="Social Media",
        title="Jordan and the Fosters",
        body="Check out the new book!",
        brand_voice_applied=("warm",),
        approved_facts_used=(),
        source_references=("clients/jordan-and-the-fosters.v1.json",),
        missing_assets_or_information=(),
        required_assets=(),
        asset_recommendations=recommendations,
    )
    publisher = FakePublisher(
        [
            PublishResponse(external_post_id="1", external_url="https://facebook.com/1"),
            PublishResponse(external_post_id="2", external_url="https://facebook.com/2"),
        ]
    )
    orchestrator = _orchestrator(tmp_path, facebook_publisher=publisher)

    week_a = date(2026, 8, 3)
    week_b = date(2026, 8, 10)
    asyncio.run(
        orchestrator.execute(
            package=_package(drafts=(draft,), approval_state=BriefApprovalState.APPROVED)
            .model_copy(update={"week": week_a, "package_id": "week-a"}),
            package_checksum=CHECKSUM_A,
            manifest=manifest,
        )
    )
    asyncio.run(
        orchestrator.execute(
            package=_package(drafts=(draft,), approval_state=BriefApprovalState.APPROVED)
            .model_copy(update={"week": week_b, "package_id": "week-b"}),
            package_checksum=CHECKSUM_B,
            manifest=manifest,
        )
    )

    sorted_ids = sorted(recommendation.asset_id for recommendation in recommendations)
    expected_a = sorted_ids[week_a.toordinal() % len(sorted_ids)]
    expected_b = sorted_ids[week_b.toordinal() % len(sorted_ids)]
    assert expected_a != expected_b, "test weeks must land on different pool members"
    assert publisher.calls[0].image_path is not None
    assert publisher.calls[0].image_path.name == f"{expected_a}.png"
    assert publisher.calls[1].image_path is not None
    assert publisher.calls[1].image_path.name == f"{expected_b}.png"


def test_disabled_orchestrator_is_a_noop(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=(FACEBOOK_ASSET_USE,))
    package = _package(drafts=(_social_draft(permitted_uses=(FACEBOOK_ASSET_USE,)),))
    publisher = FakePublisher([])
    orchestrator = _orchestrator(tmp_path, facebook_publisher=publisher, enabled=False)

    outcome = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )

    assert outcome.facebook_page is None
    assert outcome.website is None
    assert publisher.calls == []


def test_unapproved_package_raises_precondition_error(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=(FACEBOOK_ASSET_USE,))
    package = _package(
        drafts=(_social_draft(permitted_uses=(FACEBOOK_ASSET_USE,)),),
        approval_state=BriefApprovalState.DRAFT,
    )
    orchestrator = _orchestrator(tmp_path, facebook_publisher=FakePublisher([]))

    with pytest.raises(PublishContentPackageError):
        asyncio.run(
            orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
        )


def test_missing_publisher_writes_skipped_record(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=(FACEBOOK_ASSET_USE,))
    package = _package(drafts=(_social_draft(permitted_uses=(FACEBOOK_ASSET_USE,)),))
    orchestrator = _orchestrator(tmp_path)

    outcome = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )

    assert outcome.facebook_page is not None
    assert outcome.facebook_page.status is PublicationStatus.SKIPPED
    assert outcome.facebook_page.error_detail is not None
    assert "credentials are not configured" in outcome.facebook_page.error_detail
    assert outcome.website is not None
    assert outcome.website.status is PublicationStatus.SKIPPED


def test_no_matching_draft_writes_skipped_record(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=(FACEBOOK_ASSET_USE,))
    package = _package(drafts=(_website_draft(permitted_uses=(WEBSITE_ASSET_USE,)),))
    publisher = FakePublisher([])
    orchestrator = _orchestrator(tmp_path, facebook_publisher=publisher)

    outcome = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )

    assert outcome.facebook_page is not None
    assert outcome.facebook_page.status is PublicationStatus.SKIPPED
    assert publisher.calls == []


def test_no_eligible_asset_writes_skipped_record(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=("content_package_asset_recommendation",))
    package = _package(
        drafts=(_social_draft(permitted_uses=("content_package_asset_recommendation",)),)
    )
    publisher = FakePublisher([])
    orchestrator = _orchestrator(tmp_path, facebook_publisher=publisher)

    outcome = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )

    assert outcome.facebook_page is not None
    assert outcome.facebook_page.status is PublicationStatus.SKIPPED
    assert outcome.facebook_page.error_detail is not None
    assert FACEBOOK_ASSET_USE in outcome.facebook_page.error_detail
    assert publisher.calls == []


def test_successful_publish_writes_published_record(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=(FACEBOOK_ASSET_USE,))
    package = _package(drafts=(_social_draft(permitted_uses=(FACEBOOK_ASSET_USE,)),))
    publisher = FakePublisher(
        [PublishResponse(external_post_id="123", external_url="https://facebook.com/123")]
    )
    orchestrator = _orchestrator(tmp_path, facebook_publisher=publisher)

    outcome = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )

    record = outcome.facebook_page
    assert record is not None
    assert record.status is PublicationStatus.PUBLISHED
    assert record.external_post_id == "123"
    assert record.external_url == "https://facebook.com/123"
    assert len(publisher.calls) == 1
    assert publisher.calls[0].text == "Check out the new book!"
    assert publisher.calls[0].title is None
    assert publisher.calls[0].image_path == tmp_path / "assets" / "cover.png"

    record_path = tmp_path / "artifacts" / "publications" / (
        "jordan-and-the-fosters-2026-08-03-content-facebook_page.json"
    )
    assert record_path.is_file()
    on_disk = PublicationRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    assert on_disk == record


def test_website_publish_includes_draft_title(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=(WEBSITE_ASSET_USE,))
    package = _package(drafts=(_website_draft(permitted_uses=(WEBSITE_ASSET_USE,)),))
    publisher = FakePublisher(
        [PublishResponse(external_post_id="sha1", external_url="https://jordanandthefosters.fun")]
    )
    orchestrator = _orchestrator(tmp_path, website_publisher=publisher)

    outcome = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )

    record = outcome.website
    assert record is not None
    assert record.status is PublicationStatus.PUBLISHED
    assert record.destination_platform == "website"
    assert len(publisher.calls) == 1
    assert publisher.calls[0].title == "Jordan and the Fosters"
    assert publisher.calls[0].text == "Jordan learns to trust."


def test_publisher_error_writes_failed_record(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=(FACEBOOK_ASSET_USE,))
    package = _package(drafts=(_social_draft(permitted_uses=(FACEBOOK_ASSET_USE,)),))
    publisher = FakePublisher(
        [PublisherContentRejectedError("policy violation", provider="facebook_page")]
    )
    orchestrator = _orchestrator(tmp_path, facebook_publisher=publisher)

    outcome = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )

    record = outcome.facebook_page
    assert record is not None
    assert record.status is PublicationStatus.FAILED
    assert record.error_detail == "policy violation"
    assert len(publisher.calls) == 1


def test_one_destination_failing_does_not_block_the_other(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=(FACEBOOK_ASSET_USE, WEBSITE_ASSET_USE))
    package = _package(
        drafts=(
            _social_draft(permitted_uses=(FACEBOOK_ASSET_USE, WEBSITE_ASSET_USE)),
            _website_draft(permitted_uses=(FACEBOOK_ASSET_USE, WEBSITE_ASSET_USE)),
        )
    )
    facebook_publisher = FakePublisher(
        [PublisherContentRejectedError("policy violation", provider="facebook_page")]
    )
    website_publisher = FakePublisher(
        [PublishResponse(external_post_id="sha1", external_url="https://jordanandthefosters.fun")]
    )
    orchestrator = _orchestrator(
        tmp_path, facebook_publisher=facebook_publisher, website_publisher=website_publisher
    )

    outcome = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )

    assert outcome.facebook_page is not None
    assert outcome.facebook_page.status is PublicationStatus.FAILED
    assert outcome.website is not None
    assert outcome.website.status is PublicationStatus.PUBLISHED
    assert len(facebook_publisher.calls) == 1
    assert len(website_publisher.calls) == 1


def test_republishing_identical_checksum_is_idempotent(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=(FACEBOOK_ASSET_USE,))
    package = _package(drafts=(_social_draft(permitted_uses=(FACEBOOK_ASSET_USE,)),))
    publisher = FakePublisher(
        [PublishResponse(external_post_id="123", external_url="https://facebook.com/123")]
    )
    orchestrator = _orchestrator(tmp_path, facebook_publisher=publisher)

    first = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )
    second = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )

    # Only PUBLISHED records short-circuit on a matching checksum; the website destination has
    # no matching draft in this package and is SKIPPED both times, which is not itself an
    # idempotency guarantee (skip diagnostics may legitimately be re-attempted). What matters
    # for this test is that Facebook -- the one destination that actually published -- never
    # calls the publisher a second time.
    assert first.facebook_page == second.facebook_page
    assert len(publisher.calls) == 1


def test_changed_checksum_publishes_again(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, permitted_uses=(FACEBOOK_ASSET_USE,))
    package = _package(drafts=(_social_draft(permitted_uses=(FACEBOOK_ASSET_USE,)),))
    publisher = FakePublisher(
        [
            PublishResponse(external_post_id="123", external_url="https://facebook.com/123"),
            PublishResponse(external_post_id="456", external_url="https://facebook.com/456"),
        ]
    )
    orchestrator = _orchestrator(tmp_path, facebook_publisher=publisher)

    first = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )
    second = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_B, manifest=manifest)
    )

    assert first.facebook_page is not None
    assert second.facebook_page is not None
    assert first.facebook_page.external_post_id == "123"
    assert second.facebook_page.external_post_id == "456"
    assert second.facebook_page.attempt_count == 2
    assert len(publisher.calls) == 2


def test_destinations_are_independently_idempotent(tmp_path: Path) -> None:
    """Re-calling execute() with the same checksum never republishes either destination,
    and each destination's record is stored, loaded, and compared entirely independently."""
    manifest = _write_manifest(tmp_path, permitted_uses=(FACEBOOK_ASSET_USE, WEBSITE_ASSET_USE))
    package = _package(
        drafts=(
            _social_draft(permitted_uses=(FACEBOOK_ASSET_USE, WEBSITE_ASSET_USE)),
            _website_draft(permitted_uses=(FACEBOOK_ASSET_USE, WEBSITE_ASSET_USE)),
        )
    )
    facebook_publisher = FakePublisher(
        [PublishResponse(external_post_id="fb-1", external_url="https://facebook.com/fb-1")]
    )
    website_publisher = FakePublisher(
        [PublishResponse(external_post_id="sha1", external_url="https://jordanandthefosters.fun")]
    )
    orchestrator = _orchestrator(
        tmp_path, facebook_publisher=facebook_publisher, website_publisher=website_publisher
    )

    first = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )
    second = asyncio.run(
        orchestrator.execute(package=package, package_checksum=CHECKSUM_A, manifest=manifest)
    )

    assert first == second
    assert len(facebook_publisher.calls) == 1
    assert len(website_publisher.calls) == 1
