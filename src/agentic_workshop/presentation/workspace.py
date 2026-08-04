"""Escaped HTML pages for the local interactive workspace."""

import html
from datetime import date

from agentic_workshop.application.brief_review import BriefReviewAction
from agentic_workshop.application.campaign_history import CampaignView
from agentic_workshop.application.content_review import ContentReviewAction
from agentic_workshop.application.todays_work import TodaysWorkSnapshot
from agentic_workshop.domain.content import ContentDraft, ContentPackage
from agentic_workshop.domain.marketing import WeeklyMarketingBrief


def render_workspace_home(
    snapshot: TodaysWorkSnapshot,
    *,
    campaigns: tuple[CampaignView, ...],
    selected_week: date,
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
    campaign_rows = "".join(
        _campaign_row(campaign, selected_week) for campaign in campaigns
    )
    selected_path = f"/campaign/{selected_week.isoformat()}"
    package_link = (
        f'<p><a href="{selected_path}/package">Review Casey\'s complete package</a></p>'
        if snapshot.content_package.state != "missing"
        else "<p>Casey's package is pending.</p>"
    )
    generation_link = ""
    if snapshot.brief.state == "approved" and snapshot.content_package.state == "missing":
        generation_link = (
            f'<p><a class="button" href="{selected_path}/package/generate/confirm">'
            "Generate Casey's package</a></p>"
        )
    elif snapshot.content_package.state == "revision_requested":
        generation_link = (
            f'<p><a class="button" href="{selected_path}/package/generate/confirm">'
            "Regenerate Casey's package</a></p>"
        )
    return _page(
        "Today's Work",
        f"""{notice}
        <p class="local">Local workspace — nothing is published.</p>
        <h1>Today's Work</h1>
        <p><strong>{client_name}</strong> · Campaign week {html.escape(week)}</p>
        <p class="status">Viewing campaign {html.escape(selected_week.isoformat())}</p>
        <section><h2>Campaigns</h2>
          <table><thead><tr><th>Week</th><th>Theme</th><th>Sarah</th><th>Casey</th>
          <th>Generation</th><th>Cover</th><th>Preview</th></tr></thead>
          <tbody>{campaign_rows}</tbody></table>
        </section>
        <section><h2>What needs your attention</h2><ul>{attention}</ul></section>
        <section><h2>Sarah's weekly brief</h2>
          <p class="status">{html.escape(snapshot.brief.label)}</p>
          <p><strong>Objective:</strong> {objective}</p>
          <p><strong>Audience:</strong> {audience}</p>
          <p><strong>Theme:</strong> {theme}</p>
          <p><a href="{selected_path}/brief">Review Sarah's complete brief</a></p>
        </section>
        <section><h2>Casey's content package</h2>
          <p class="status">{html.escape(snapshot.content_package.label)}</p>
          <p>Generation method: {html.escape(snapshot.generation_method)}</p>
          {drafts}
          {package_link}
          {generation_link}
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
    *,
    campaign_week: date,
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
            f'<a class="button" href="/campaign/{campaign_week.isoformat()}/brief/'
            'approve/confirm">Approve Sarah\'s brief</a>'
        )
    if BriefReviewAction.REQUEST_REVISION in actions:
        action_links.append(
            f'<a class="button secondary" href="/campaign/{campaign_week.isoformat()}/brief/'
            'revision/confirm">'
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
        <p><a href="/campaign/{campaign_week.isoformat()}">← Today's Work</a></p>
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
    campaign_week: date,
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
        <p><a href="/campaign/{campaign_week.isoformat()}/brief">← Return to Sarah's brief</a></p>
        <h1>{html.escape(title)}</h1>
        <p>{html.escape(instructions)}</p>
        <p><strong>Client:</strong> {html.escape(str(brief.client_id))}<br>
        <strong>Campaign week:</strong> {html.escape(brief.week.isoformat())}<br>
        <strong>Current state:</strong> {html.escape(brief.approval_state.value)}</p>
        <form method="post"
              action="/campaign/{campaign_week.isoformat()}/brief/{html.escape(action)}">
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


def render_workspace_error(
    title: str,
    message: str,
    *,
    return_path: str = "/",
    return_label: str = "Return to Today's Work",
) -> str:
    return _page(
        title,
        f"""<p class="local">Local workspace — nothing is published.</p>
        <h1>{html.escape(title)}</h1>
        <p class="error">{html.escape(message)}</p>
        <p><a href="{html.escape(return_path, quote=True)}">{html.escape(return_label)}</a></p>""",
    )


def render_package(
    package: ContentPackage,
    actions: tuple[ContentReviewAction, ...],
    *,
    campaign_week: date,
) -> str:
    drafts = "".join(_render_content_draft(draft) for draft in package.drafts)
    assumptions = _list_items(package.assumptions)
    missing = _list_items(package.missing_assets_or_information)
    assumption_explanation = html.escape(
        "These assumptions were recorded when Casey generated the package. "
        "The current workflow state is shown above."
    )
    revision = (
        f"<p><strong>Revision instructions:</strong> {html.escape(package.revision_note)}</p>"
        if package.revision_note
        else ""
    )
    links: list[str] = []
    base = f"/campaign/{campaign_week.isoformat()}/package"
    if ContentReviewAction.APPROVE in actions:
        links.append(
            f'<a class="button" href="{base}/approve/confirm">'
            "Approve Casey's package</a>"
        )
    if ContentReviewAction.REQUEST_REVISION in actions:
        links.append(
            f'<a class="button secondary" href="{base}/revision/confirm">Request a revision</a>'
        )
    action_section = (
        f'<div class="actions">{"".join(links)}</div>'
        if links
        else "<p>No review action is available until Casey regenerates a draft.</p>"
    )
    return _page(
        "Casey's content package",
        f"""<p class="local">Local workspace — nothing is published.</p>
        <p><a href="/campaign/{campaign_week.isoformat()}">← Today's Work</a></p>
        <h1>Casey's content package</h1>
        <p class="status">{html.escape(package.approval_state.value.replace('_', ' '))}</p>
        <p>Generation method: {html.escape(package.generation_metadata.generator)}</p>
        {revision}
        {drafts}
        <section class="secondary-details"><h2>Generation-time assumptions</h2>
          <p>{assumption_explanation}</p><ul>{assumptions}</ul>
        </section>
        <section><h2>Missing assets or information</h2><ul>{missing}</ul></section>
        {action_section}""",
    )


def render_package_confirmation(
    package: ContentPackage,
    *,
    action: str,
    csrf_token: str,
    confirmation_nonce: str,
    checksum: str,
    campaign_week: date,
) -> str:
    revision = action == "revision"
    title = "Request a revision" if revision else "Approve Casey's package"
    instructions = (
        "Explain exactly what Casey should change. Revision instructions are required."
        if revision
        else "Confirm that this package is accepted for review. This does not publish anything."
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
        <p><a href="/campaign/{campaign_week.isoformat()}/package">
        ← Return to Casey's package</a></p>
        <h1>{html.escape(title)}</h1><p>{html.escape(instructions)}</p>
        <p><strong>Package:</strong> {html.escape(package.package_id)}<br>
        <strong>Campaign week:</strong> {html.escape(package.week.isoformat())}<br>
        <strong>Current state:</strong> {html.escape(package.approval_state.value)}</p>
        <form method="post"
              action="/campaign/{campaign_week.isoformat()}/package/{html.escape(action)}">
          <input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">
          <input type="hidden" name="confirmation_nonce"
                 value="{html.escape(confirmation_nonce, quote=True)}">
          <input type="hidden" name="artifact_checksum" value="{html.escape(checksum, quote=True)}">
          <input type="hidden" name="client_id"
                 value="{html.escape(str(package.client_id), quote=True)}">
          <input type="hidden" name="week"
                 value="{html.escape(package.week.isoformat(), quote=True)}">
          {textarea}<button type="submit">{html.escape(title)}</button>
        </form>""",
    )


def render_generation_confirmation(
    *,
    campaign_week: date,
    client_id: str,
    brief_identity: str,
    brief_checksum: str,
    package_identity: str,
    package_checksum: str | None,
    csrf_token: str,
    confirmation_nonce: str,
) -> str:
    regeneration = package_checksum is not None
    verb = "Regenerate" if regeneration else "Generate"
    mode = (
        "replace the revision-requested package"
        if regeneration
        else "create a new package"
    )
    return _page(
        f"{verb} Casey's package",
        f"""<p class="local">Local workspace — nothing is published.</p>
        <p><a href="/campaign/{campaign_week.isoformat()}">← Today's Work</a></p>
        <h1>{verb} Casey's package</h1>
        <p>This will {html.escape(mode)} for campaign week
        <strong>{campaign_week.isoformat()}</strong>.</p>
        <ul><li>Sarah brief: <code>{html.escape(brief_identity)}</code></li>
        <li>Generation method: deterministic</li><li>No paid model request will be made.</li>
        <li>The result will be a draft and nothing will be published.</li></ul>
        <form method="post" action="/campaign/{campaign_week.isoformat()}/package/generate">
          <input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">
          <input type="hidden" name="confirmation_nonce"
                 value="{html.escape(confirmation_nonce, quote=True)}">
          <input type="hidden" name="client_id" value="{html.escape(client_id, quote=True)}">
          <input type="hidden" name="week" value="{campaign_week.isoformat()}">
          <input type="hidden" name="brief_identity"
                 value="{html.escape(brief_identity, quote=True)}">
          <input type="hidden" name="brief_checksum" value="{brief_checksum}">
          <input type="hidden" name="package_identity"
                 value="{html.escape(package_identity, quote=True)}">
          <input type="hidden" name="package_checksum"
                 value="{html.escape(package_checksum or '', quote=True)}">
          <button type="submit">{verb} draft package</button>
        </form>""",
    )


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="/workspace.css">
</head><body>{body}</body></html>"""


def _campaign_row(campaign: CampaignView, selected_week: date) -> str:
    snapshot = campaign.snapshot
    week = campaign.record.week.isoformat()
    selected = " (viewing)" if campaign.record.week == selected_week else ""
    theme = snapshot.strategy.campaign_theme or "Not available"
    cover = snapshot.asset.availability if snapshot.asset is not None else "missing"
    return (
        "<tr>"
        f'<td><a href="/campaign/{week}">{html.escape(week)}{selected}</a></td>'
        f"<td>{html.escape(theme)}</td>"
        f"<td>{html.escape(snapshot.brief.state)}</td>"
        f"<td>{html.escape(snapshot.content_package.state)}</td>"
        f"<td>{html.escape(snapshot.generation_method)}</td>"
        f"<td>{html.escape(cover)}</td>"
        f"<td>{'available' if snapshot.preview_exists else 'missing'}</td>"
        "</tr>"
    )


def _render_content_draft(draft: ContentDraft) -> str:
    sources = _list_items(draft.source_references, code=True)
    facts = _list_items(draft.approved_facts_used, code=True)
    assets = "".join(
        "<li><strong>"
        f"{html.escape(item.asset_id)}</strong> — {html.escape(item.availability)}; "
        f"permitted uses: {html.escape(', '.join(item.permitted_uses) or 'none')}</li>"
        for item in draft.asset_recommendations
    ) or "<li>No asset recommendation.</li>"
    return (
        f"<article><p><strong>{html.escape(draft.channel)}</strong></p>"
        f"<h2>{html.escape(draft.title)}</h2>"
        f'<div class="public-copy">{html.escape(draft.body).replace(chr(10), "<br>")}</div>'
        "<details><summary>Sources, facts, and asset details</summary>"
        f"<h3>Source provenance</h3><ul>{sources}</ul>"
        f"<h3>Approved fact identifiers</h3><ul>{facts}</ul>"
        f"<h3>Asset recommendations</h3><ul>{assets}</ul></details></article>"
    )


def _list_items(items: tuple[str, ...], *, code: bool = False) -> str:
    if not items:
        return "<li>None.</li>"
    if code:
        return "".join(f"<li><code>{html.escape(item)}</code></li>" for item in items)
    return "".join(f"<li>{html.escape(item)}</li>" for item in items)
