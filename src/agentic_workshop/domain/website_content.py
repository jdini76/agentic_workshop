"""Static, non-weekly homepage content for the website publisher's static site.

Kept separate from the weekly "current pitch" (title + body), which comes from Casey's
approved official_website draft and is the only part WebsitePublisher overwrites on each
publish. Everything modeled here -- author bio, reviews, contact links -- is carried over
verbatim from what the CEO already published by hand on the live site; it is not something
Casey generates or Casey is restricted to the governed ClientProfile facts for.
"""

from typing import Literal

from agentic_workshop.domain.base import DomainModel
from agentic_workshop.domain.identity import ClientId, NonBlank


class SiteNavItem(DomainModel):
    label: NonBlank
    anchor: NonBlank


class SiteReview(DomainModel):
    quote: NonBlank
    attribution: NonBlank


class SiteContactLink(DomainModel):
    label: NonBlank
    url: NonBlank


class WebsiteStaticContent(DomainModel):
    """Non-weekly homepage sections the WebsitePublisher never overwrites."""

    schema_version: Literal[1] = 1
    client_id: ClientId
    site_title: NonBlank
    nav_items: tuple[SiteNavItem, ...]
    author_byline: NonBlank
    purchase_label: NonBlank
    purchase_url: NonBlank
    author_heading: NonBlank
    author_bio_paragraphs: tuple[NonBlank, ...]
    author_photo_relative_path: NonBlank
    reviews_heading: NonBlank
    reviews: tuple[SiteReview, ...]
    contact_email: NonBlank
    contact_links: tuple[SiteContactLink, ...]
    source_reference: NonBlank
