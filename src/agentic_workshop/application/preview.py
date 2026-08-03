"""Local-only static campaign previews for approved content packages."""

import html
import re
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentic_workshop.application.assets import ClientAssetInventory
from agentic_workshop.domain.assets import AssetApprovalState, ClientAsset, ClientAssetManifest
from agentic_workshop.domain.content import ContentDraft, ContentPackage
from agentic_workshop.domain.marketing import BriefApprovalState

URL_PATTERN = re.compile(r"https?://[^\s<>()]+")


class CampaignPreviewError(ValueError):
    """Raised when a package cannot safely become a local preview."""


class CampaignPreviewResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    html_path: Path
    asset_path: Path


class GenerateCampaignPreview:
    """Validate, copy, and render a static review artifact without publishing it."""

    def __init__(self, repository_root: Path, preview_root: Path) -> None:
        self._repository_root = repository_root.resolve(strict=True)
        allowed_root = (self._repository_root / "artifacts" / "campaign-previews").resolve(
            strict=False
        )
        self._preview_root = preview_root.resolve(strict=False)
        if not self._preview_root.is_relative_to(allowed_root):
            raise CampaignPreviewError(
                "preview output must remain beneath artifacts/campaign-previews"
            )

    async def execute(
        self,
        package: ContentPackage,
        manifest: ClientAssetManifest,
        *,
        approved_destinations: tuple[str, ...],
        overwrite: bool = False,
    ) -> CampaignPreviewResult:
        if package.approval_state is not BriefApprovalState.APPROVED:
            raise CampaignPreviewError("campaign previews require an approved ContentPackage")
        if package.client_id != manifest.client_id:
            raise CampaignPreviewError("package and asset manifest client IDs do not match")
        if len(package.drafts) != 2:
            raise CampaignPreviewError("preview requires exactly one website and one social draft")

        website = self._draft_for_channel(package, "official_website")
        social = self._draft_for_channel(package, "social_posts")
        website_asset = await self._asset_for_draft(website, manifest, "official_website")
        social_asset = await self._asset_for_draft(social, manifest, "social_posts")
        if website_asset.asset_id != social_asset.asset_id:
            raise CampaignPreviewError(
                "website and social preview must use the same approved asset"
            )
        asset = website_asset

        destination = (self._preview_root / package.package_id).resolve(strict=False)
        if not destination.is_relative_to(self._preview_root):
            raise CampaignPreviewError("package ID would escape the preview directory")
        if destination.exists() and not overwrite:
            raise CampaignPreviewError(
                f"preview already exists: {destination}; pass --overwrite to replace it"
            )
        if destination.exists():
            shutil.rmtree(destination)
        destination_assets = destination / "assets"
        destination_assets.mkdir(parents=True, exist_ok=False)
        source = self._validated_asset_path(asset)
        copied_asset = destination_assets / source.name
        shutil.copyfile(source, copied_asset)
        html_path = destination / "index.html"
        html_path.write_text(
            self._render_html(
                package,
                website,
                social,
                asset,
                copied_asset.relative_to(destination).as_posix(),
                approved_destinations,
            ),
            encoding="utf-8",
        )
        return CampaignPreviewResult(
            html_path=destination / "index.html",
            asset_path=destination / "assets" / source.name,
        )

    async def _asset_for_draft(
        self,
        draft: ContentDraft,
        manifest: ClientAssetManifest,
        required_use: str,
    ) -> ClientAsset:
        eligible = [
            recommendation
            for recommendation in draft.asset_recommendations
            if recommendation.availability == "available"
            and required_use in recommendation.permitted_uses
        ]
        if len(eligible) != 1:
            raise CampaignPreviewError(
                f"{draft.assignment} requires exactly one available asset for {required_use}"
            )
        recommendation = eligible[0]
        matches = [asset for asset in manifest.assets if asset.asset_id == recommendation.asset_id]
        if len(matches) != 1:
            raise CampaignPreviewError("recommended asset is absent from the client manifest")
        asset = matches[0]
        if recommendation.repository_path != asset.repository_path:
            raise CampaignPreviewError("recommendation path differs from the manifest")
        if recommendation.manifest_source != manifest.source_reference:
            raise CampaignPreviewError("recommendation source differs from the manifest")
        if asset.approval_state is not AssetApprovalState.APPROVED:
            raise CampaignPreviewError("recommended asset is not approved")
        if asset.source.source_type != "local_derivative" or asset.transformation is None:
            raise CampaignPreviewError("preview refuses original or non-derivative assets")
        if not asset.transformation.embedded_metadata_removed:
            raise CampaignPreviewError("preview asset must have embedded metadata removed")
        required = {"content_package_asset_recommendation", required_use}
        if not required.issubset(asset.approved_uses):
            raise CampaignPreviewError("manifest does not permit this assignment channel")
        result = await ClientAssetInventory(self._repository_root).validate(asset)
        if not result.valid:
            raise CampaignPreviewError(f"preview asset validation failed: {result.diagnostic}")
        return asset

    def _validated_asset_path(self, asset: ClientAsset) -> Path:
        path = (self._repository_root / asset.repository_path).resolve(strict=True)
        if not path.is_relative_to(self._repository_root):
            raise CampaignPreviewError("asset path escapes repository root")
        return path

    @staticmethod
    def _draft_for_channel(package: ContentPackage, required_use: str) -> ContentDraft:
        matches = [
            draft
            for draft in package.drafts
            if GenerateCampaignPreview._channel_use(draft.channel) == required_use
        ]
        if len(matches) != 1:
            raise CampaignPreviewError(
                f"package requires exactly one assignment for {required_use}"
            )
        return matches[0]

    @staticmethod
    def _channel_use(channel: str) -> str:
        normalized = channel.lower()
        if "social" in normalized:
            return "social_posts"
        if "email" in normalized:
            return "email_marketing"
        if "website" in normalized:
            return "official_website"
        return "campaign_package_previews"

    @classmethod
    def _public_copy(cls, body: str, approved_destinations: tuple[str, ...]) -> str:
        parts: list[str] = []
        cursor = 0
        approved = set(approved_destinations)
        for match in URL_PATTERN.finditer(body):
            parts.append(html.escape(body[cursor : match.start()]))
            url = match.group(0)
            if url not in approved:
                raise CampaignPreviewError(f"public copy contains an unapproved URL: {url}")
            escaped = html.escape(url, quote=True)
            parts.append(f'<a href="{escaped}" rel="noopener noreferrer">{escaped}</a>')
            cursor = match.end()
        parts.append(html.escape(body[cursor:]))
        return "".join(parts)

    @classmethod
    def _render_html(
        cls,
        package: ContentPackage,
        website: ContentDraft,
        social: ContentDraft,
        asset: ClientAsset,
        asset_path: str,
        approved_destinations: tuple[str, ...],
    ) -> str:
        image = html.escape(asset_path, quote=True)
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local campaign preview — not published</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Georgia, serif;
      color: #25201d;
      background: #f4efe7;
    }}
    body {{ margin: 0; }}
    header, main, footer {{ max-width: 1080px; margin: auto; padding: 24px; }}
    header {{ background: #342e2a; color: white; max-width: none; text-align: center; }}
    .notice {{ font: 700 1rem system-ui, sans-serif; letter-spacing: .03em; }}
    .panel {{
      background: white;
      border-radius: 16px;
      margin: 28px 0;
      padding: 28px;
      box-shadow: 0 4px 20px #0001;
    }}
    .website {{
      display: grid;
      grid-template-columns: minmax(240px, 38%) 1fr;
      gap: 32px;
      align-items: start;
    }}
    img {{ display: block; width: 100%; height: auto; border-radius: 8px; }}
    .copy {{ white-space: pre-wrap; line-height: 1.55; }}
    .social {{ max-width: 640px; margin-inline: auto; }}
    .review {{
      font: .9rem/1.5 system-ui, sans-serif;
      background: #e9e2d8;
      border: 2px dashed #766b61;
    }}
    a {{ color: #7b351e; }}
    @media (max-width: 700px) {{ .website {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header><div class="notice">Local campaign preview — not published.</div></header>
  <main>
    <section class="panel website" aria-labelledby="website-title">
      <img src="{image}" alt="Approved Jordan and the Fosters front cover">
      <div>
        <h1 id="website-title">{html.escape(website.title)}</h1>
        <div class="copy">{cls._public_copy(website.body, approved_destinations)}</div>
      </div>
    </section>
    <section class="panel social" aria-labelledby="social-title">
      <img src="{image}" alt="Approved Jordan and the Fosters front cover">
      <h2 id="social-title">{html.escape(social.title)}</h2>
      <div class="copy">{cls._public_copy(social.body, approved_destinations)}</div>
    </section>
    <section class="panel review" aria-labelledby="review-title">
      <h2 id="review-title">Non-public review information</h2>
      <dl>
        <dt>Package</dt><dd>{html.escape(package.package_id)}</dd>
        <dt>Generation method</dt><dd>{html.escape(package.generation_metadata.generator)}</dd>
        <dt>Approval state</dt><dd>{html.escape(package.approval_state.value)}</dd>
        <dt>Asset</dt><dd>{html.escape(asset.asset_id)}</dd>
      </dl>
      <p>This local artifact has no publish, upload, post, send, or external-delivery action.</p>
    </section>
  </main>
  <footer>Review artifact only. Nothing has been published.</footer>
</body>
</html>
"""
