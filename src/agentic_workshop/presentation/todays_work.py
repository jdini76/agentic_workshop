"""Escaped static HTML rendering for a Today's Work snapshot."""

import html

from agentic_workshop.application.todays_work import TodaysWorkSnapshot


def render_todays_work(
    snapshot: TodaysWorkSnapshot,
    *,
    asset_path: str | None,
    preview_path: str | None,
) -> str:
    """Render a local-only dashboard without reading files or resources."""
    week = snapshot.campaign_week.isoformat() if snapshot.campaign_week else "Not available"
    attention = "".join(f"<li>{html.escape(item)}</li>" for item in snapshot.attention)
    drafts = "".join(
        f"""<article class="draft">
          <p class="eyebrow">{html.escape(draft.channel)}</p>
          <h3>{html.escape(draft.title)}</h3>
          <p>{html.escape(draft.summary)}</p>
        </article>"""
        for draft in snapshot.draft_summaries
    ) or '<p class="empty">No Casey drafts are available yet.</p>'
    strategy = _strategy(snapshot)
    asset = _asset(snapshot, asset_path)
    preview = (
        f'<a class="button" href="{html.escape(preview_path, quote=True)}">'
        "Open local campaign preview</a>"
        if snapshot.preview_exists and preview_path is not None
        else '<span class="muted">Campaign preview not available.</span>'
    )
    provenance = "".join(
        f"<li><code>{html.escape(source)}</code></li>" for source in snapshot.provenance
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Today's Work — {html.escape(snapshot.client_name)}</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: system-ui, sans-serif;
      color: #292522;
      background: #f5f1eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; }}
    header {{ padding: 28px; color: white; background: #2f2925; }}
    header div, main {{ max-width: 1120px; margin: auto; }}
    main {{ padding: 28px; }}
    .notice, .eyebrow {{ font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }}
    .notice {{ color: #f8d692; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px; }}
    .card {{
      padding: 24px;
      margin-bottom: 22px;
      border-radius: 14px;
      background: white;
      box-shadow: 0 3px 15px #0001;
    }}
    .attention {{ border-left: 7px solid #b85d30; }}
    .status {{
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      background: #eee6da;
    }}
    .asset {{ display: grid; grid-template-columns: 160px 1fr; gap: 22px; align-items: start; }}
    img {{ display: block; width: 100%; height: auto; border-radius: 7px; }}
    .draft {{ padding: 18px 0; border-top: 1px solid #ddd4c8; }}
    .draft:first-child {{ border-top: 0; }}
    .eyebrow {{ color: #805238; font-size: .76rem; }}
    .button {{
      display: inline-block;
      padding: 11px 16px;
      color: white;
      background: #7a3d24;
      border-radius: 8px;
      text-decoration: none;
    }}
    .muted, .empty {{ color: #716a65; }}
    details {{ margin-top: 20px; }}
    code {{ overflow-wrap: anywhere; }}
    @media (max-width: 720px) {{
      .grid, .asset {{ grid-template-columns: 1fr; }}
      .asset img {{ max-width: 220px; }}
    }}
  </style>
</head>
<body>
  <header><div>
    <p class="notice">Local workspace — nothing is published.</p>
    <h1>Today's Work</h1>
    <p>{html.escape(snapshot.client_name)} · Campaign week {html.escape(week)}</p>
  </div></header>
  <main>
    <section class="card attention">
      <h2>What needs your attention</h2>
      <ul>{attention}</ul>
    </section>
    <div class="grid">
      <section class="card">
        <h2>Sarah's weekly brief</h2>
        <p class="status">{html.escape(snapshot.brief.label)}</p>
        {strategy}
      </section>
      <section class="card">
        <h2>Casey's content package</h2>
        <p class="status">{html.escape(snapshot.content_package.label)}</p>
        <p>Generation method: {html.escape(snapshot.generation_method)}</p>
        {preview}
      </section>
    </div>
    <section class="card">
      <h2>Website and social drafts</h2>
      {drafts}
    </section>
    <section class="card asset">
      {asset}
    </section>
    <details class="card">
      <summary>Provenance and workflow details</summary>
      <p>Client ID: <code>{html.escape(str(snapshot.client_id))}</code></p>
      <p>Brief state: {html.escape(snapshot.brief.state)}</p>
      <p>Package state: {html.escape(snapshot.content_package.state)}</p>
      <p>Generation method: {html.escape(snapshot.generation_method)}</p>
      <ul>{provenance}</ul>
    </details>
  </main>
</body>
</html>
"""


def _strategy(snapshot: TodaysWorkSnapshot) -> str:
    values = (
        ("Objective", snapshot.strategy.objective),
        ("Audience", snapshot.strategy.audience),
        ("Campaign theme", snapshot.strategy.campaign_theme),
    )
    populated = [(label, value) for label, value in values if value]
    if not populated:
        return '<p class="empty">Strategy is not available yet.</p>'
    return "".join(
        f"<h3>{html.escape(label)}</h3><p>{html.escape(value)}</p>"
        for label, value in populated
    )


def _asset(snapshot: TodaysWorkSnapshot, asset_path: str | None) -> str:
    if snapshot.asset is None or asset_path is None:
        return """<div><h2>Approved campaign cover</h2>
        <p class="empty">No validated marketing derivative is available.</p></div>"""
    return f"""<img src="{html.escape(asset_path, quote=True)}" alt="Approved campaign cover">
      <div><h2>Approved campaign cover</h2>
      <p><code>{html.escape(snapshot.asset.asset_id)}</code></p>
      <p>{html.escape(snapshot.asset.diagnostic)}</p></div>"""
