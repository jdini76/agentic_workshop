"""Renders the live Jordan and the Fosters homepage as plain static HTML.

Unlike application/preview.py's local-only review artifact (which deliberately carries
"DRAFT / not published" banners and a raw metadata dump), this is real, publishable markup:
no review banner, no internal metadata, no absolute /preview.css dependency on the local
workspace. The "current pitch" block is the only part WebsitePublisher overwrites on each
publish; everything else comes from a WebsiteStaticContent resource the CEO maintains directly.
"""

import html
import re

from agentic_workshop.domain.website_content import WebsiteStaticContent

URL_PATTERN = re.compile(r"https?://[^\s<>()]+")


class WebsiteRenderError(ValueError):
    """Raised when the homepage cannot be safely rendered."""


def _safe_body_html(body: str, approved_destinations: tuple[str, ...]) -> str:
    """Escape body text and linkify only pre-approved URLs, mirroring preview.py's guard."""
    approved = set(approved_destinations)
    parts: list[str] = []
    cursor = 0
    for match in URL_PATTERN.finditer(body):
        parts.append(html.escape(body[cursor : match.start()]))
        url = match.group(0)
        if url not in approved:
            raise WebsiteRenderError(f"pitch body contains an unapproved URL: {url}")
        escaped = html.escape(url, quote=True)
        parts.append(f'<a href="{escaped}" rel="noopener noreferrer">{escaped}</a>')
        cursor = match.end()
    parts.append(html.escape(body[cursor:]))
    return "".join(parts)


def _paragraphs_html(text: str, approved_destinations: tuple[str, ...] = ()) -> str:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    return "\n".join(
        f"<p>{_safe_body_html(paragraph, approved_destinations)}</p>" for paragraph in paragraphs
    )


def render_homepage(
    *,
    static_content: WebsiteStaticContent,
    pitch_title: str,
    pitch_body: str,
    cover_image_relative_path: str,
    approved_destinations: tuple[str, ...],
) -> str:
    """Render the full homepage. Raises WebsiteRenderError on unapproved content."""
    nav_html = "\n".join(
        f'<li><a href="#{html.escape(item.anchor, quote=True)}">{html.escape(item.label)}</a></li>'
        for item in static_content.nav_items
    )
    reviews_html = "\n".join(
        f"""<li class="review">
      <p class="quote">&ldquo;{html.escape(review.quote)}&rdquo;</p>
      <p class="attribution">&mdash; {html.escape(review.attribution)}</p>
    </li>"""
        for review in static_content.reviews
    )
    contact_links_html = "\n".join(
        f'<a href="{html.escape(link.url, quote=True)}">{html.escape(link.label)}</a>'
        for link in static_content.contact_links
    )
    author_bio_html = "\n".join(
        f"<p>{html.escape(paragraph)}</p>" for paragraph in static_content.author_bio_paragraphs
    )
    cover_src = html.escape(cover_image_relative_path, quote=True)
    author_photo_src = html.escape(static_content.author_photo_relative_path, quote=True)
    purchase_url = html.escape(static_content.purchase_url, quote=True)
    mailto = html.escape(f"mailto:{static_content.contact_email}", quote=True)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(pitch_title)}</title>
  <meta name="description" content="{html.escape(pitch_title)}">
  <link rel="stylesheet" href="site.css">
</head>
<body>
  <nav>
    <ul>
{nav_html}
    </ul>
  </nav>
  <header id="top" class="hero">
    <img src="{cover_src}" alt="Jordan and the Fosters cover">
    <div>
      <h1>{html.escape(static_content.site_title)}</h1>
      <p class="byline">{html.escape(static_content.author_byline)}</p>
      <a class="buy-now" href="{purchase_url}">{html.escape(static_content.purchase_label)}</a>
    </div>
  </header>

  <!-- BEGIN CURRENT PITCH: managed by agentic-workshop; do not hand-edit -->
  <section id="book" class="current-pitch">
    <img src="{cover_src}" alt="Jordan and the Fosters cover">
    <div>
      <h2>{html.escape(pitch_title)}</h2>
      {_paragraphs_html(pitch_body, approved_destinations)}
    </div>
  </section>
  <!-- END CURRENT PITCH -->

  <section id="author" class="author">
    <img src="{author_photo_src}" alt="{html.escape(static_content.author_byline)}">
    <div>
      <h2>{html.escape(static_content.author_heading)}</h2>
      {author_bio_html}
    </div>
  </section>

  <section id="reviews" class="reviews">
    <h2>{html.escape(static_content.reviews_heading)}</h2>
    <ul>
{reviews_html}
    </ul>
  </section>

  <footer id="contact" class="contact">
    <a href="{mailto}">{html.escape(static_content.contact_email)}</a>
{contact_links_html}
  </footer>
</body>
</html>
"""
