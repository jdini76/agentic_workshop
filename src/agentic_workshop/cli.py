"""Command-line entry point for the first governed marketing workflow."""

import argparse
import asyncio
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from agentic_workshop.adapters.deterministic_content import (
    DeterministicContentDraftGenerator,
)
from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.adapters.model_content import ModelContentDraftGenerator
from agentic_workshop.adapters.openai_language_model import OpenAILanguageModel
from agentic_workshop.application.content import ContentPackageError, GenerateContentPackage
from agentic_workshop.application.marketing import (
    CLIENT_RESOURCE_TEMPLATE,
    GenerateWeeklyMarketingBrief,
    MarketingBriefError,
)
from agentic_workshop.domain.clients import ClientProfile
from agentic_workshop.domain.content import ContentPackage
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief
from agentic_workshop.ports.content_generation import ContentDraftGenerator
from agentic_workshop.ports.models import LanguageModelError
from agentic_workshop.presentation.content_markdown import render_content_package
from agentic_workshop.presentation.markdown import render_weekly_marketing_brief

PACKAGE_RESOURCE_ROOT = Path(__file__).parent / "resources"
DEFAULT_ARTIFACT_ROOT = Path("artifacts") / "weekly-briefs"
DEFAULT_CONTENT_ARTIFACT_ROOT = Path("artifacts") / "content-packages"
DEFAULT_MODEL_ARTIFACT_ROOT = Path("artifacts") / "model-content-packages"
DEFAULT_LIVE_SMOKE_ARTIFACT_ROOT = Path("artifacts") / "live-smoke" / "openai"
CASEY_PROMPT_RESOURCE = "prompts/casey-content-creator.v1.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentic-workshop")
    subparsers = parser.add_subparsers(dest="command", required=True)

    brief = subparsers.add_parser("brief", help="generate a weekly marketing brief draft")
    brief.add_argument("client_id")
    brief.add_argument("--week-of", required=True, type=date.fromisoformat)
    brief.add_argument("--strict", action="store_true")
    brief.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    brief.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)

    content = subparsers.add_parser(
        "content-package", help="generate channel drafts from an approved weekly brief"
    )
    content.add_argument("brief_file", type=Path)
    content.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    content.add_argument("--artifact-root", type=Path, default=DEFAULT_CONTENT_ARTIFACT_ROOT)
    _add_model_options(content)

    live = subparsers.add_parser(
        "live-smoke-openai",
        help="make one explicitly confirmed paid OpenAI draft-generation call",
    )
    live.add_argument("brief_file", type=Path)
    live.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    live.add_argument("--artifact-root", type=Path, default=DEFAULT_LIVE_SMOKE_ARTIFACT_ROOT)
    _add_openai_settings(live)
    live.add_argument("--confirm-paid-call", action="store_true", required=True)

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
    service = GenerateWeeklyMarketingBrief(loader)
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
    data = artifact.model_dump(mode="json")
    if args.approve:
        data.update(approval_state=BriefApprovalState.APPROVED, revision_note=None)
    else:
        data.update(
            approval_state=BriefApprovalState.REVISION_REQUESTED,
            revision_note=args.request_revision,
        )
    if isinstance(artifact, WeeklyMarketingBrief):
        reviewed_brief = WeeklyMarketingBrief.model_validate(data)
        _write_artifacts(
            reviewed_brief, args.brief_file, args.brief_file.with_suffix(".md")
        )
    else:
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
    generator = asyncio.run(_content_generator(args, loader, use_openai=use_openai))
    package = asyncio.run(
        GenerateContentPackage(generator).execute(
            brief,
            client,
            approved_brief_source=str(args.brief_file),
        )
    )
    artifact_root = args.artifact_root
    if use_openai and artifact_root == DEFAULT_CONTENT_ARTIFACT_ROOT:
        artifact_root = DEFAULT_MODEL_ARTIFACT_ROOT
    suffix = "-openai-smoke" if force_openai else ""
    json_path = artifact_root / f"{package.package_id}{suffix}.json"
    markdown_path = artifact_root / f"{package.package_id}{suffix}.md"
    _write_content_artifacts(package, json_path, markdown_path)
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
    )
    return ModelContentDraftGenerator(model, instructions=instructions)


def _load_review_artifact(path: Path) -> WeeklyMarketingBrief | ContentPackage:
    raw = path.read_text(encoding="utf-8")
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
