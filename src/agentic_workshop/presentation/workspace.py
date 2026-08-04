"""Escaped HTML pages for the local interactive workspace."""

import html

from agentic_workshop.application.brief_review import BriefReviewAction
from agentic_workshop.application.todays_work import TodaysWorkSnapshot
from agentic_workshop.domain.marketing import WeeklyMarketingBrief


def render_workspace_home(
    snapshot: TodaysWorkSnapshot,
    *,
    message: str | None = None,
) -> str:
    week = snapshot.campaign_week.isoformat() if snapshot.campaign_week else "Not available"
    notice = f'<p class="success">{html.escape(message)}</p>' if message else ""
    attention = "".join(f"<li>{html.escape(item)}</li>" for item in snapshot.attention)
    asset = (
        f"{html.escape(snapshot.asset.asset_id)} ({html.escape(snapshot.asset.availability)})"
        if snapshot.asset is not None
        else "No approved campaign cover is available."
    )
    client_name = html.escape(snapshot.client_name)
    objective = html.escape(snapshot.strategy.objective or "Not available")
    audience = html.escape(snapshot.strategy.audience or "Not available")
    theme = html.escape(snapshot.strategy.campaign_theme or "Not available")
    drafts = "".join(
        f"<article><p><strong>{html.escape(draft.channel)}</strong></p>"
        f"<h3>{html.escape(draft.title)}</h3><p>{html.escape(draft.summary)}</p></article>"
        for draft in snapshot.draft_summaries
    ) or "<p>No Casey drafts are available yet.</p>"
    provenance = "".join(
        f"<li><code>{html.escape(source)}</code></li>" for source in snapshot.provenance
    )
    return _page(
        "Today's Work",
        f"""{notice}
        <p class="local">Local workspace — nothing is published.</p>
        <h1>Today's Work</h1>
        <p><strong>{client_name}</strong> · Campaign week {html.escape(week)}</p>
        <section><h2>What needs your attention</h2><ul>{attention}</ul></section>
        <section><h2>Sarah's weekly brief</h2>
          <p class="status">{html.escape(snapshot.brief.label)}</p>
          <p><strong>Objective:</strong> {objective}</p>
          <p><strong>Audience:</strong> {audience}</p>
          <p><strong>Theme:</strong> {theme}</p>
          <p><a href="/brief">Review Sarah's complete brief</a></p>
        </section>
        <section><h2>Casey's content package</h2>
          <p class="status">{html.escape(snapshot.content_package.label)}</p>
          <p>Generation method: {html.escape(snapshot.generation_method)}</p>
          {drafts}
        </section>
        <section><h2>Campaign cover and preview</h2>
          <p>{asset}</p>
          <p>Campaign preview: {"available" if snapshot.preview_exists else "not available"}</p>
        </section>
        <details><summary>Provenance and workflow details</summary>
          <ul>{provenance}</ul>
        </details>""",
    )


def render_brief(
    brief: WeeklyMarketingBrief,
    actions: tuple[BriefReviewAction, ...],
) -> str:
    assignments = "".join(
        "<li><strong>"
        f"{html.escape(item.deliverable)}</strong> — {html.escape(item.channel)}: "
        f"{html.escape(item.instructions)}</li>"
        for item in brief.content_assignments
    )
    sources = "".join(
        f"<li><code>{html.escape(source)}</code></li>" for source in brief.source_references
    )
    revision = (
        f"<p><strong>Revision instructions:</strong> {html.escape(brief.revision_note)}</p>"
        if brief.revision_note
        else ""
    )
    action_links: list[str] = []
    if BriefReviewAction.APPROVE in actions:
        action_links.append(
            '<a class="button" href="/brief/approve/confirm">Approve Sarah\'s brief</a>'
        )
    if BriefReviewAction.REQUEST_REVISION in actions:
        action_links.append(
            '<a class="button secondary" href="/brief/revision/confirm">'
            "Request a revision</a>"
        )
    action_section = (
        f'<div class="actions">{"".join(action_links)}</div>'
        if action_links
        else "<p>No review action is available until Sarah regenerates a draft.</p>"
    )
    return _page(
        "Sarah's weekly brief",
        f"""<p class="local">Local workspace — nothing is published.</p>
        <p><a href="/">← Today's Work</a></p>
        <h1>Sarah's weekly brief</h1>
        <p class="status">{html.escape(brief.approval_state.value.replace("_", " "))}</p>
        {revision}
        <dl>
          <dt>Client</dt><dd>{html.escape(str(brief.client_id))}</dd>
          <dt>Campaign week</dt><dd>{html.escape(brief.week.isoformat())}</dd>
          <dt>Objective</dt><dd>{html.escape(brief.objective)}</dd>
          <dt>Audience</dt><dd>{html.escape(brief.audience)}</dd>
          <dt>Campaign theme</dt><dd>{html.escape(brief.campaign_theme)}</dd>
          <dt>Rationale</dt><dd>{html.escape(brief.rationale)}</dd>
          <dt>Call to action</dt><dd>{html.escape(brief.call_to_action)}</dd>
        </dl>
        <h2>Content assignments</h2><ul>{assignments}</ul>
        <h2>Sources</h2><ul>{sources}</ul>
        {action_section}""",
    )


def render_confirmation(
    brief: WeeklyMarketingBrief,
    *,
    action: str,
    csrf_token: str,
    confirmation_nonce: str,
    checksum: str,
) -> str:
    revision = action == "revision"
    title = "Request a revision" if revision else "Approve Sarah's brief"
    instructions = (
        "Explain exactly what Sarah should change. Revision instructions are required."
        if revision
        else "Confirm that this brief is ready for Casey. This does not publish anything."
    )
    textarea = (
        '<label for="revision_note">Revision instructions</label>'
        '<textarea id="revision_note" name="revision_note" required></textarea>'
        if revision
        else ""
    )
    return _page(
        title,
        f"""<p class="local">Local workspace — nothing is published.</p>
        <p><a href="/brief">← Return to Sarah's brief</a></p>
        <h1>{html.escape(title)}</h1>
        <p>{html.escape(instructions)}</p>
        <p><strong>Client:</strong> {html.escape(str(brief.client_id))}<br>
        <strong>Campaign week:</strong> {html.escape(brief.week.isoformat())}<br>
        <strong>Current state:</strong> {html.escape(brief.approval_state.value)}</p>
        <form method="post" action="/brief/{html.escape(action)}">
          <input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">
          <input type="hidden" name="confirmation_nonce"
                 value="{html.escape(confirmation_nonce, quote=True)}">
          <input type="hidden" name="artifact_checksum" value="{html.escape(checksum, quote=True)}">
          <input type="hidden" name="client_id"
                 value="{html.escape(str(brief.client_id), quote=True)}">
          <input type="hidden" name="week"
                 value="{html.escape(brief.week.isoformat(), quote=True)}">
          {textarea}
          <button type="submit">{html.escape(title)}</button>
        </form>""",
    )


def render_workspace_error(title: str, message: str) -> str:
    return _page(
        title,
        f"""<p class="local">Local workspace — nothing is published.</p>
        <h1>{html.escape(title)}</h1>
        <p class="error">{html.escape(message)}</p>
        <p><a href="/">Return to Today's Work</a></p>""",
    )


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="/workspace.css">
</head><body>{body}</body></html>"""
