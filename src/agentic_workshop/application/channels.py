"""Shared channel-to-asset-use classification.

Extracted from three previously-identical private copies in application/content.py,
application/preview.py, and application/preview_status.py.
"""


def channel_use(channel: str) -> str:
    normalized = channel.lower()
    if "social" in normalized:
        return "social_posts"
    if "email" in normalized:
        return "email_marketing"
    if "website" in normalized:
        return "official_website"
    return "campaign_package_previews"
