"""Command-line entry point for the first governed marketing workflow."""

import argparse
import asyncio
import json
import os
import shutil
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from agentic_workshop.adapters.deterministic_content import (
    DeterministicContentDraftGenerator,
)
from agentic_workshop.adapters.filesystem_resources import (
    FilesystemResourceLoader,
    ResourceNotFoundError,
)
from agentic_workshop.adapters.local_workspace import WorkspaceConfig, serve_workspace
from agentic_workshop.adapters.model_attempts import (
    ModelAttemptRecorder,
    RetainedAttemptLanguageModel,
)
from agentic_workshop.adapters.model_content import ModelContentDraftGenerator
from agentic_workshop.adapters.openai_language_model import OpenAILanguageModel
from agentic_workshop.application.assets import ClientAssetInventory
from agentic_workshop.application.brief_review import ReviewWeeklyMarketingBrief
from agentic_workshop.application.content import ContentPackageError, GenerateContentPackage
from agentic_workshop.application.marketing import (
    CLIENT_RESOURCE_TEMPLATE,
    GenerateWeeklyMarketingBrief,
    MarketingBriefError,
)
from agentic_workshop.application.preview import GenerateCampaignPreview
from agentic_workshop.application.todays_work import LoadTodaysWork
from agentic_workshop.domain.assets import (
    AssetApprovalState,
    AssetRecommendation,
    ClientAssetManifest,
)
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.identity import ClientId
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief
from agentic_workshop.domain.model_attempts import UntrustedModelAttempt
from agentic_workshop.ports.content_generation import ContentDraftGenerator
from agentic_workshop.ports.models import LanguageModelError
from agentic_workshop.presentation.content_markdown import render_content_package
from agentic_workshop.presentation.markdown import render_weekly_marketing_brief
from agentic_workshop.presentation.todays_work import render_todays_work

PACKAGE_RESOURCE_ROOT = Path(__file__).parent / "resources"
DEFAULT_ARTIFACT_ROOT = Path("artifacts") / "weekly-briefs"
DEFAULT_CONTENT_ARTIFACT_ROOT = Path("artifacts") / "content-packages"
DEFAULT_MODEL_ARTIFACT_ROOT = Path("artifacts") / "model-content-packages"
DEFAULT_LIVE_SMOKE_ARTIFACT_ROOT = Path("artifacts") / "live-smoke" / "openai"
DEFAULT_PREVIEW_ROOT = Path("artifacts") / "campaign-previews"
DEFAULT_TODAYS_WORK_ROOT = Path("artifacts") / "todays-work"
CASEY_PROMPT_RESOURCE = "prompts/casey-content-creator.v1.md"
ASSET_MANIFEST_TEMPLATE = "client-assets/{client_id}.v1.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentic-workshop")
    subparsers = parser.add_subparsers(dest="command", required=True)

    brief = subparsers.add_parser("brief", help="generate a weekly marketing brief draft")
    brief.add_argument("client_id")
    brief.add_argument("--week-of", required=True, type=date.fromisoformat)
    brief.add_argument("--strict", action="store_true")
    brief.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    brief.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    brief.add_argument("--repository-root", type=Path, default=Path.cwd())

    content = subparsers.add_parser(
        "content-package", help="generate channel drafts from an approved weekly brief"
    )
    content.add_argument("brief_file", type=Path)
    content.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    content.add_argument("--artifact-root", type=Path, default=DEFAULT_CONTENT_ARTIFACT_ROOT)
    content.add_argument("--repository-root", type=Path, default=Path.cwd())
    _add_model_options(content)

    live = subparsers.add_parser(
        "live-smoke-openai",
        help="make one explicitly confirmed paid OpenAI draft-generation call",
    )
    live.add_argument("brief_file", type=Path)
    live.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    live.add_argument("--artifact-root", type=Path, default=DEFAULT_LIVE_SMOKE_ARTIFACT_ROOT)
    live.add_argument("--repository-root", type=Path, default=Path.cwd())
    _add_openai_settings(live)
    live.add_argument("--confirm-paid-call", action="store_true", required=True)

    revalidate = subparsers.add_parser(
        "revalidate-attempt",
        help="revalidate a retained untrusted attempt without a model request",
    )
    revalidate.add_argument("attempt_file", type=Path)
    revalidate.add_argument("brief_file", type=Path)
    revalidate.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    revalidate.add_argument("--artifact-root", type=Path, default=None)
    revalidate.add_argument("--repository-root", type=Path, default=Path.cwd())

    inventory = subparsers.add_parser(
        "asset-inventory", help="validate a client's local asset manifest"
    )
    inventory.add_argument("client_id")
    inventory.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    inventory.add_argument("--repository-root", type=Path, default=Path.cwd())

    asset_review = subparsers.add_parser(
        "asset-review", help="approve or request revision of a manifest asset"
    )
    asset_review.add_argument("manifest_file", type=Path)
    asset_review.add_argument("asset_id")
    asset_review.add_argument("--repository-root", type=Path, default=Path.cwd())
    asset_decision = asset_review.add_mutually_exclusive_group(required=True)
    asset_decision.add_argument("--approve", action="store_true")
    asset_decision.add_argument("--request-revision", metavar="INSTRUCTIONS")

    preview = subparsers.add_parser(
        "campaign-preview", help="generate a local static preview from an approved package"
    )
    preview.add_argument("package_file", type=Path)
    preview.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    preview.add_argument("--repository-root", type=Path, default=Path.cwd())
    preview.add_argument("--preview-root", type=Path, default=DEFAULT_PREVIEW_ROOT)
    preview.add_argument("--overwrite", action="store_true")

    todays_work = subparsers.add_parser(
        "todays-work", help="generate the local read-only Today's Work dashboard"
    )
    todays_work.add_argument("--client-id", default="jordan-and-the-fosters")
    todays_work.add_argument(
        "--brief-file",
        type=Path,
        default=Path("artifacts/weekly-briefs/jordan-and-the-fosters-2026-08-03.json"),
    )
    todays_work.add_argument(
        "--package-file",
        type=Path,
        default=Path(
            "artifacts/visual-enabled/2026-08-03/"
            "jordan-and-the-fosters-2026-08-03-content.json"
        ),
    )
    todays_work.add_argument(
        "--preview-file",
        type=Path,
        default=Path(
            "artifacts/campaign-previews/"
            "jordan-and-the-fosters-2026-08-03-content/index.html"
        ),
    )
    todays_work.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    todays_work.add_argument("--repository-root", type=Path, default=Path.cwd())
    todays_work.add_argument("--dashboard-root", type=Path, default=DEFAULT_TODAYS_WORK_ROOT)
    todays_work.add_argument("--overwrite", action="store_true")

    workspace = subparsers.add_parser(
        "workspace", help="run the local interactive campaign workspace"
    )
    workspace.add_argument("--port", type=int, default=8765)

    review = subparsers.add_parser("review", help="review an existing brief JSON file")
    review.add_argument("brief_file", type=Path)
    decision = review.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--request-revision", metavar="INSTRUCTIONS")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "brief":
            return _generate(args)
        if args.command == "content-package":
            return _generate_content_package(args)
        if args.command == "live-smoke-openai":
            return _generate_content_package(args, force_openai=True)
        if args.command == "revalidate-attempt":
            return _revalidate_attempt(args)
        if args.command == "asset-inventory":
            return _asset_inventory(args)
        if args.command == "asset-review":
            return _asset_review(args)
        if args.command == "campaign-preview":
            return _campaign_preview(args)
        if args.command == "todays-work":
            return _todays_work(args)
        if args.command == "workspace":
            return _workspace(args)
        return _review(args)
    except (
        ContentPackageError,
        MarketingBriefError,
        LanguageModelError,
        OSError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    return 2


def main() -> None:
    raise SystemExit(run())


def _generate(args: argparse.Namespace) -> int:
    loader = FilesystemResourceLoader(args.resource_root)
    asset_recommendations = asyncio.run(
        _asset_recommendations(loader, ClientId(args.client_id), args.repository_root)
    )
    service = GenerateWeeklyMarketingBrief(
        loader,
        asset_recommendations=asset_recommendations,
    )
    brief = asyncio.run(service.execute(args.client_id, args.week_of, strict=args.strict))
    stem = f"{args.client_id}-{brief.week.isoformat()}"
    json_path = args.artifact_root / f"{stem}.json"
    markdown_path = args.artifact_root / f"{stem}.md"
    _write_artifacts(brief, json_path, markdown_path)
    print(json_path)
    print(markdown_path)
    return 0


def _review(args: argparse.Namespace) -> int:
    artifact = _load_review_artifact(args.brief_file)
    if isinstance(artifact, WeeklyMarketingBrief):
        service = ReviewWeeklyMarketingBrief()
        if args.approve:
            service.approve(args.brief_file)
        else:
            service.request_revision(args.brief_file, args.request_revision)
        print(args.brief_file)
        return 0
    data = artifact.model_dump(mode="json")
    if args.approve:
        data.update(approval_state=BriefApprovalState.APPROVED, revision_note=None)
    else:
        data.update(
            approval_state=BriefApprovalState.REVISION_REQUESTED,
            revision_note=args.request_revision,
        )
    reviewed_package = ContentPackage.model_validate(data)
    _write_content_artifacts(
        reviewed_package, args.brief_file, args.brief_file.with_suffix(".md")
    )
    print(args.brief_file)
    return 0


def _write_artifacts(brief: WeeklyMarketingBrief, json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(brief.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_weekly_marketing_brief(brief), encoding="utf-8")


def _generate_content_package(
    args: argparse.Namespace, *, force_openai: bool = False
) -> int:
    brief = WeeklyMarketingBrief.model_validate_json(
        args.brief_file.read_text(encoding="utf-8")
    )
    loader = FilesystemResourceLoader(args.resource_root)
    client_ref = CLIENT_RESOURCE_TEMPLATE.format(client_id=brief.client_id)
    client = ClientProfile.model_validate_json(asyncio.run(loader.load_text(client_ref)))
    use_openai = force_openai or args.generator == "openai"
    if use_openai and not args.confirm_paid_call:
        raise ValueError("--confirm-paid-call is required before any paid OpenAI request")
    artifact_root = args.artifact_root
    if use_openai and artifact_root == DEFAULT_CONTENT_ARTIFACT_ROOT:
        artifact_root = DEFAULT_MODEL_ARTIFACT_ROOT
    recorder = ModelAttemptRecorder(artifact_root / "attempts") if use_openai else None
    generator = asyncio.run(
        _content_generator(args, loader, use_openai=use_openai, recorder=recorder)
    )
    asset_recommendations = asyncio.run(
        _asset_recommendations(loader, client.id, args.repository_root)
    )
    try:
        package = asyncio.run(
            GenerateContentPackage(
                generator, asset_recommendations=asset_recommendations
            ).execute(
                brief,
                client,
                approved_brief_source=str(args.brief_file),
            )
        )
    except Exception as error:
        if recorder is not None and recorder.path is not None:
            recorder.rejected((str(error),))
        raise
    suffix = "-openai-smoke" if force_openai else ""
    json_path = artifact_root / f"{package.package_id}{suffix}.json"
    markdown_path = artifact_root / f"{package.package_id}{suffix}.md"
    _write_content_artifacts(package, json_path, markdown_path)
    if recorder is not None:
        recorder.accepted(json_path)
    print(json_path)
    print(markdown_path)
    return 0


def _add_model_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--generator", choices=("deterministic", "openai"), default="deterministic"
    )
    _add_openai_settings(parser)
    parser.add_argument("--confirm-paid-call", action="store_true")


def _add_openai_settings(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=None,
        help="model override (otherwise OPENAI_MODEL or gpt-5.6-terra)",
    )
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        default="medium",
    )
    parser.add_argument("--max-output-tokens", type=int, default=4000)


async def _content_generator(
    args: argparse.Namespace,
    loader: FilesystemResourceLoader,
    *,
    use_openai: bool,
    recorder: ModelAttemptRecorder | None = None,
) -> ContentDraftGenerator:
    if not use_openai:
        return DeterministicContentDraftGenerator()
    if args.timeout_seconds <= 0 or args.max_output_tokens <= 0:
        raise ValueError("timeout and output-token budget must be positive")
    instructions = await loader.load_text(CASEY_PROMPT_RESOURCE)
    model = OpenAILanguageModel.from_environment(
        model=args.model,
        timeout_seconds=args.timeout_seconds,
        reasoning_effort=args.reasoning_effort,
        max_output_tokens=args.max_output_tokens,
        attempt_recorder=recorder,
    )
    return ModelContentDraftGenerator(
        model,
        instructions=instructions,
        attempt_recorder=recorder,
    )


def _revalidate_attempt(args: argparse.Namespace) -> int:
    attempt = UntrustedModelAttempt.model_validate_json(
        args.attempt_file.read_text(encoding="utf-8")
    )
    brief = WeeklyMarketingBrief.model_validate_json(
        args.brief_file.read_text(encoding="utf-8")
    )
    loader = FilesystemResourceLoader(args.resource_root)
    client_ref = CLIENT_RESOURCE_TEMPLATE.format(client_id=brief.client_id)
    client = ClientProfile.model_validate_json(asyncio.run(loader.load_text(client_ref)))
    instructions = asyncio.run(loader.load_text(CASEY_PROMPT_RESOURCE))
    recorder = ModelAttemptRecorder(args.attempt_file.parent)
    recorder.attach(args.attempt_file)
    generator = ModelContentDraftGenerator(
        RetainedAttemptLanguageModel(attempt),
        instructions=instructions,
    )
    asset_recommendations = asyncio.run(
        _asset_recommendations(loader, client.id, args.repository_root)
    )
    try:
        package = asyncio.run(
            GenerateContentPackage(
                generator, asset_recommendations=asset_recommendations
            ).execute(
                brief,
                client,
                approved_brief_source=str(args.brief_file),
            )
        )
        artifact_root = args.artifact_root or args.attempt_file.parent.parent / "revalidated"
        stem = f"{package.package_id}-revalidated-{attempt.attempt_id[:8]}"
        json_path = artifact_root / f"{stem}.json"
        markdown_path = artifact_root / f"{stem}.md"
        _write_content_artifacts(package, json_path, markdown_path)
        recorder.accepted(json_path)
    except Exception as error:
        recorder.rejected((str(error),))
        raise
    print(json_path)
    print(markdown_path)
    return 0


async def _asset_recommendations(
    loader: FilesystemResourceLoader,
    client_id: ClientId,
    repository_root: Path,
) -> tuple[AssetRecommendation, ...]:
    manifest_ref = ASSET_MANIFEST_TEMPLATE.format(client_id=client_id)
    try:
        raw_manifest = await loader.load_text(manifest_ref)
    except ResourceNotFoundError:
        return ()
    manifest = ClientAssetManifest.model_validate_json(raw_manifest)
    return await ClientAssetInventory(repository_root).recommendations(manifest)


def _asset_inventory(args: argparse.Namespace) -> int:
    loader = FilesystemResourceLoader(args.resource_root)
    manifest_ref = ASSET_MANIFEST_TEMPLATE.format(client_id=args.client_id)
    manifest = ClientAssetManifest.model_validate_json(
        asyncio.run(loader.load_text(manifest_ref))
    )
    results = asyncio.run(ClientAssetInventory(args.repository_root).inventory(manifest))
    payload = {
        "manifest": manifest_ref,
        "client_id": str(manifest.client_id),
        "results": [result.model_dump(mode="json") for result in results],
    }
    print(json.dumps(payload, indent=2))
    return 0 if all(result.valid for result in results) else 1


def _asset_review(args: argparse.Namespace) -> int:
    manifest = ClientAssetManifest.model_validate_json(
        args.manifest_file.read_text(encoding="utf-8")
    )
    matches = [asset for asset in manifest.assets if asset.asset_id == args.asset_id]
    if not matches:
        raise ValueError(f"asset ID not found in manifest: {args.asset_id}")
    target = matches[0]
    data = target.model_dump(mode="json")
    if args.approve:
        result = asyncio.run(ClientAssetInventory(args.repository_root).validate(target))
        if not result.valid:
            raise ValueError(f"cannot approve invalid asset: {result.diagnostic}")
        data.update(approval_state=AssetApprovalState.APPROVED, revision_note=None)
    else:
        data.update(
            approval_state=AssetApprovalState.REVISION_REQUESTED,
            revision_note=args.request_revision,
        )
    updated_assets = [
        data if asset.asset_id == args.asset_id else asset.model_dump(mode="json")
        for asset in manifest.assets
    ]
    updated = ClientAssetManifest.model_validate(
        {
            **manifest.model_dump(mode="json"),
            "manifest_revision": manifest.manifest_revision + 1,
            "assets": updated_assets,
        }
    )
    temporary = args.manifest_file.with_suffix(args.manifest_file.suffix + ".tmp")
    temporary.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.manifest_file)
    print(args.manifest_file)
    return 0


def _campaign_preview(args: argparse.Namespace) -> int:
    package = ContentPackage.model_validate_json(
        args.package_file.read_text(encoding="utf-8")
    )
    loader = FilesystemResourceLoader(args.resource_root)
    manifest_ref = ASSET_MANIFEST_TEMPLATE.format(client_id=package.client_id)
    client_ref = CLIENT_RESOURCE_TEMPLATE.format(client_id=package.client_id)
    manifest = ClientAssetManifest.model_validate_json(
        asyncio.run(loader.load_text(manifest_ref))
    )
    client = ClientProfile.model_validate_json(asyncio.run(loader.load_text(client_ref)))
    preview = asyncio.run(
        GenerateCampaignPreview(args.repository_root, args.preview_root).execute(
            package,
            manifest,
            approved_destinations=tuple(link.url for link in client.purchase_links),
            overwrite=args.overwrite,
        )
    )
    print(preview.html_path)
    print(preview.asset_path)
    return 0


def _todays_work(args: argparse.Namespace) -> int:
    repository_root = args.repository_root.resolve(strict=True)
    allowed_root = (repository_root / "artifacts" / "todays-work").resolve(strict=False)
    dashboard_root = args.dashboard_root.resolve(strict=False)
    if not dashboard_root.is_relative_to(allowed_root):
        raise ValueError("dashboard output must remain beneath artifacts/todays-work")
    loader = FilesystemResourceLoader(args.resource_root)
    snapshot = asyncio.run(
        LoadTodaysWork(repository_root, loader).execute(
            args.client_id,
            brief_path=args.brief_file,
            package_path=args.package_file,
            preview_path=args.preview_file,
        )
    )
    week = snapshot.campaign_week.isoformat() if snapshot.campaign_week else "current"
    destination = (dashboard_root / f"{snapshot.client_id}-{week}").resolve(strict=False)
    if not destination.is_relative_to(dashboard_root):
        raise ValueError("dashboard destination escapes its configured root")
    if destination.exists() and not args.overwrite:
        raise ValueError(
            f"dashboard already exists: {destination}; pass --overwrite to replace it"
        )
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    asset_relative: str | None = None
    if snapshot.asset_source_path is not None:
        asset_directory = destination / "assets"
        asset_directory.mkdir()
        copied_asset = asset_directory / snapshot.asset_source_path.name
        shutil.copyfile(snapshot.asset_source_path, copied_asset)
        asset_relative = copied_asset.relative_to(destination).as_posix()
    preview_relative = (
        Path(os.path.relpath(snapshot.preview_source_path, destination)).as_posix()
        if snapshot.preview_source_path is not None
        else None
    )
    dashboard = destination / "index.html"
    dashboard.write_text(
        render_todays_work(
            snapshot,
            asset_path=asset_relative,
            preview_path=preview_relative,
        ),
        encoding="utf-8",
    )
    print(dashboard)
    return 0


def _workspace(args: argparse.Namespace) -> int:
    if not 1 <= args.port <= 65535:
        raise ValueError("workspace port must be between 1 and 65535")
    repository_root = Path.cwd().resolve(strict=True)
    serve_workspace(
        WorkspaceConfig(
            repository_root=repository_root,
            resource_root=PACKAGE_RESOURCE_ROOT,
            client_id=ClientId("jordan-and-the-fosters"),
            port=args.port,
        )
    )
    return 0


def _load_review_artifact(path: Path) -> WeeklyMarketingBrief | ContentPackage:
    raw = path.read_text(encoding="utf-8")
    if '"record_type"' in raw and '"untrusted_model_attempt"' in raw:
        raise ValueError("untrusted model attempts cannot be reviewed or approved")
    try:
        return WeeklyMarketingBrief.model_validate_json(raw)
    except ValidationError:
        return ContentPackage.model_validate_json(raw)


def _write_content_artifacts(
    package: ContentPackage, json_path: Path, markdown_path: Path
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_content_package(package), encoding="utf-8")


if __name__ == "__main__":
    main()
