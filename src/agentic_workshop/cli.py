"""Command-line entry point for the first governed marketing workflow."""

import argparse
import asyncio
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from agentic_workshop.adapters.filesystem_resources import FilesystemResourceLoader
from agentic_workshop.application.marketing import (
    GenerateWeeklyMarketingBrief,
    MarketingBriefError,
)
from agentic_workshop.domain.marketing import BriefApprovalState, WeeklyMarketingBrief
from agentic_workshop.presentation.markdown import render_weekly_marketing_brief

PACKAGE_RESOURCE_ROOT = Path(__file__).parent / "resources"
DEFAULT_ARTIFACT_ROOT = Path("artifacts") / "weekly-briefs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentic-workshop")
    subparsers = parser.add_subparsers(dest="command", required=True)

    brief = subparsers.add_parser("brief", help="generate a weekly marketing brief draft")
    brief.add_argument("client_id")
    brief.add_argument("--week-of", required=True, type=date.fromisoformat)
    brief.add_argument("--strict", action="store_true")
    brief.add_argument("--resource-root", type=Path, default=PACKAGE_RESOURCE_ROOT)
    brief.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)

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
        return _review(args)
    except (MarketingBriefError, OSError, ValidationError, ValueError) as error:
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
    brief = WeeklyMarketingBrief.model_validate_json(args.brief_file.read_text(encoding="utf-8"))
    data = brief.model_dump(mode="json")
    if args.approve:
        data.update(approval_state=BriefApprovalState.APPROVED, revision_note=None)
    else:
        data.update(
            approval_state=BriefApprovalState.REVISION_REQUESTED,
            revision_note=args.request_revision,
        )
    reviewed = WeeklyMarketingBrief.model_validate(data)
    _write_artifacts(reviewed, args.brief_file, args.brief_file.with_suffix(".md"))
    print(args.brief_file)
    return 0


def _write_artifacts(brief: WeeklyMarketingBrief, json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(brief.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_weekly_marketing_brief(brief), encoding="utf-8")


if __name__ == "__main__":
    main()
