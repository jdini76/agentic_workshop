"""Markdown rendering for content packages."""

from agentic_workshop.domain.content import ContentPackage


def render_content_package(package: ContentPackage) -> str:
    """Render a complete content package for CEO review."""
    lines = [
        f"# Content Package - {package.week.isoformat()}",
        "",
        f"- Package: `{package.package_id}`",
        f"- Client: `{package.client_id}`",
        f"- Employee: `{package.employee_id}`",
        f"- Approval state: **{package.approval_state.value}**",
        f"- Approved brief: `{package.approved_brief_source}`",
        f"- Client profile: `{package.client_profile_source}`",
        "",
        "## Brand voice",
        "",
    ]
    lines.extend(f"- {voice}" for voice in package.brand_voice)
    lines.append("")
    for draft in package.drafts:
        lines.extend(
            [
                f"## {draft.title}",
                "",
                f"- Assignment: {draft.assignment}",
                f"- Channel: {draft.channel}",
                f"- Brand voice applied: {', '.join(draft.brand_voice_applied)}",
                "",
                draft.body,
                "",
                "### Sources",
                "",
            ]
        )
        lines.extend(f"- `{source}`" for source in draft.source_references)
        lines.extend(["", "### Missing assets or information", ""])
        lines.extend(f"- {item}" for item in draft.missing_assets_or_information)
        if not draft.missing_assets_or_information:
            lines.append("- None")
        lines.append("")
    _append_list(lines, "Package assumptions", package.assumptions)
    _append_list(
        lines,
        "Package missing assets or information",
        package.missing_assets_or_information,
    )
    if package.revision_note is not None:
        lines.extend(["## Revision note", "", package.revision_note, ""])
    return "\n".join(lines)


def _append_list(lines: list[str], heading: str, items: tuple[str, ...]) -> None:
    lines.extend([f"## {heading}", ""])
    lines.extend(f"- {item}" for item in items)
    if not items:
        lines.append("- None")
    lines.append("")
