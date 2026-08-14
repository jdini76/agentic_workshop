"""Facebook Page adapter for the provider-neutral Publisher port.

Uses the Graph API's direct binary upload for Page photos, so this destination -- unlike
Instagram, whose Graph API requires media to already be hosted at a public URL -- works
completely standalone from a server that only ever binds to 127.0.0.1.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx

from agentic_workshop.adapters.env_credentials import (
    environment_value,
    is_invalid_credential,
    local_environment,
)
from agentic_workshop.ports.publishing import (
    Publisher,
    PublisherAuthenticationError,
    PublisherContentRejectedError,
    PublisherError,
    PublisherMalformedResponseError,
    PublisherRateLimitError,
    PublisherTimeoutError,
    PublisherUnavailableError,
    PublishRequest,
    PublishResponse,
)

FACEBOOK_PROVIDER = "facebook_page"
DEFAULT_GRAPH_API_VERSION = os.getenv("FACEBOOK_GRAPH_API_VERSION", "v21.0")

# Graph API error codes: https://developers.facebook.com/docs/graph-api/guides/error-handling/
AUTH_ERROR_CODES = frozenset({190})
RATE_LIMIT_ERROR_CODES = frozenset({4, 17, 32, 613})
CONTENT_REJECTED_ERROR_CODES = frozenset({368})


class FacebookPagePublisher(Publisher):
    """Post Casey's approved social draft, with its approved cover, to one Facebook Page."""

    def __init__(
        self,
        *,
        page_id: str,
        access_token: str,
        api_version: str = DEFAULT_GRAPH_API_VERSION,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._page_id = page_id
        self._access_token = access_token
        self._api_version = api_version
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @classmethod
    def from_environment(
        cls,
        *,
        timeout_seconds: float = 30.0,
        load_dotenv: bool = True,
        env_file: Path | None = None,
    ) -> "FacebookPagePublisher":
        local_values = local_environment(env_file, load_dotenv=load_dotenv)
        page_id = environment_value("FACEBOOK_PAGE_ID", local_values)
        access_token = environment_value("FACEBOOK_PAGE_ACCESS_TOKEN", local_values)
        if is_invalid_credential(page_id) or is_invalid_credential(access_token):
            raise PublisherAuthenticationError(
                "FACEBOOK_PAGE_ID or FACEBOOK_PAGE_ACCESS_TOKEN is absent, empty, or a "
                "placeholder; no Facebook request was made",
                provider=FACEBOOK_PROVIDER,
            )
        assert page_id is not None
        assert access_token is not None
        api_version = (
            environment_value("FACEBOOK_GRAPH_API_VERSION", local_values)
            or DEFAULT_GRAPH_API_VERSION
        )
        return cls(
            page_id=page_id,
            access_token=access_token,
            api_version=api_version,
            timeout_seconds=timeout_seconds,
        )

    def _new_client(self) -> httpx.AsyncClient:
        # A fresh client per publish() call, not one reused for the adapter's whole lifetime --
        # httpx.AsyncClient's connection pool is bound to the event loop active when it first
        # makes a request. A long-lived Publisher instance shared across many independent
        # asyncio.run() calls (e.g. one per HTTP request in a long-running server) would try to
        # reuse a pool tied to an already-closed event loop on the second and later calls,
        # raising "RuntimeError: Event loop is closed" instead of actually posting.
        return httpx.AsyncClient(
            base_url=f"https://graph.facebook.com/{self._api_version}",
            timeout=self._timeout_seconds,
            transport=self._transport,
        )

    async def publish(self, request: PublishRequest) -> PublishResponse:
        try:
            async with self._new_client() as client:
                if request.image_path is not None:
                    response = await self._post_photo(client, request.text, request.image_path)
                else:
                    response = await self._post_feed(client, request.text)
        except httpx.TimeoutException:
            raise PublisherTimeoutError(
                "Facebook request timed out", provider=FACEBOOK_PROVIDER
            ) from None
        except httpx.ConnectError:
            raise PublisherError(
                "Facebook could not be reached", provider=FACEBOOK_PROVIDER
            ) from None

        if response.status_code >= 400:
            raise self._normalized_error(response)

        try:
            payload = response.json()
        except ValueError:
            raise PublisherMalformedResponseError(
                "Facebook returned a non-JSON response", provider=FACEBOOK_PROVIDER
            ) from None
        post_id = payload.get("post_id") or payload.get("id")
        if not isinstance(post_id, str) or not post_id:
            raise PublisherMalformedResponseError(
                "Facebook response did not include a post identifier",
                provider=FACEBOOK_PROVIDER,
            )
        return PublishResponse(
            external_post_id=post_id,
            # Best-effort constructed permalink, not fetched from a follow-up API call --
            # Facebook's generic /{post_id} form redirects to the real post.
            external_url=f"https://www.facebook.com/{post_id}",
            provider_metadata={"provider": FACEBOOK_PROVIDER, "raw_response": payload},
        )

    async def _post_photo(
        self, client: httpx.AsyncClient, caption: str, image_path: Path
    ) -> httpx.Response:
        image_bytes = await asyncio.to_thread(image_path.read_bytes)
        return await client.post(
            f"/{self._page_id}/photos",
            data={"caption": caption, "access_token": self._access_token},
            files={"source": (image_path.name, image_bytes, "image/png")},
        )

    async def _post_feed(self, client: httpx.AsyncClient, message: str) -> httpx.Response:
        return await client.post(
            f"/{self._page_id}/feed",
            data={"message": message, "access_token": self._access_token},
        )

    def _normalized_error(self, response: httpx.Response) -> PublisherError:
        detail: dict[str, Any] = {}
        try:
            detail = response.json().get("error", {})
        except ValueError:
            pass
        code = detail.get("code")
        message = detail.get("message") or f"Facebook request failed ({response.status_code})"

        if response.status_code == 401 or code in AUTH_ERROR_CODES:
            return PublisherAuthenticationError(message, provider=FACEBOOK_PROVIDER)
        if code in RATE_LIMIT_ERROR_CODES:
            return PublisherRateLimitError(message, provider=FACEBOOK_PROVIDER)
        if code in CONTENT_REJECTED_ERROR_CODES:
            return PublisherContentRejectedError(message, provider=FACEBOOK_PROVIDER)
        if response.status_code == 404:
            return PublisherUnavailableError(message, provider=FACEBOOK_PROVIDER)
        return PublisherError(message, provider=FACEBOOK_PROVIDER)
