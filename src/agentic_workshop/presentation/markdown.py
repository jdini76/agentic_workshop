"""Markdown rendering for weekly marketing briefs."""

from agentic_workshop.domain.marketing import WeeklyMarketingBrief


def render_weekly_marketing_brief(brief: WeeklyMarketingBrief) -> str:
    """Render every review-relevant brief field as stable Markdown."""
    lines = [
        f"# Weekly Marketing Brief - {brief.week.isoformat()}",
        "",
        f"- Client: `{brief.client_id}`",
        f"- Employee: `{brief.employee_id}`",
        f"- Approval state: **{brief.approval_state.value}**",
        "",
        "## Strategy",
        "",
        f"**Objective:** {brief.objective}",
        "",
        f"**Audience:** {brief.audience}",
        "",
        f"**Campaign theme:** {brief.campaign_theme}",
        "",
        f"**Rationale:** {brief.rationale}",
        "",
        f"**Call to action:** {brief.call_to_action}",
        "",
    ]
    _append_list(lines, "Recommended channels", brief.recommended_channels)
    lines.extend(["## Content assignments", ""])
    for assignment in brief.content_assignments:
        lines.extend(
            [
                f"### {assignment.deliverable}",
                "",
                f"- Owner: `{assignment.owner_id}`",
                f"- Channel: {assignment.channel}",
                f"- Instructions: {assignment.instructions}",
                "",
            ]
        )
    lines.extend(["## Success metrics", ""])
    lines.extend(f"- **{metric.name}:** {metric.target}" for metric in brief.success_metrics)
    lines.append("")
    _append_list(lines, "Assumptions", brief.assumptions)
    _append_list(lines, "Missing inputs", brief.missing_inputs)
    _append_list(lines, "Source references", brief.source_references)
    if brief.revision_note is not None:
        lines.extend(["## Revision note", "", brief.revision_note, ""])
    return "\n".join(lines)


def _append_list(lines: list[str], heading: str, items: tuple[str, ...]) -> None:
    lines.extend([f"## {heading}", ""])
    lines.extend(f"- {item}" for item in items)
    if not items:
        lines.append("- None")
    lines.append("")
