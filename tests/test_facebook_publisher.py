import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from agentic_workshop.adapters.facebook_page_publisher import FacebookPagePublisher
from agentic_workshop.ports.publishing import (
    PublisherAuthenticationError,
    PublisherContentRejectedError,
    PublisherError,
    PublisherMalformedResponseError,
    PublisherRateLimitError,
    PublisherTimeoutError,
    PublisherUnavailableError,
    PublishRequest,
)


def _publisher(handler: Callable[[httpx.Request], httpx.Response]) -> FacebookPagePublisher:
    transport = httpx.MockTransport(handler)
    return FacebookPagePublisher(page_id="page-1", access_token="token-1", transport=transport)


def test_publish_with_image_posts_binary_photo_upload(tmp_path: Path) -> None:
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake-png-bytes")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers["content-type"]
        return httpx.Response(200, json={"id": "789", "post_id": "789_101"})

    publisher = _publisher(handler)
    response = asyncio.run(
        publisher.publish(
            PublishRequest(
                destination_platform="facebook_page",
                text="Check out Jordan and the Fosters!",
                image_path=image_path,
            )
        )
    )

    assert response.external_post_id == "789_101"
    assert response.external_url == "https://www.facebook.com/789_101"
    assert str(captured["url"]) == "https://graph.facebook.com/v21.0/page-1/photos"
    assert "multipart/form-data" in str(captured["content_type"])


def test_publish_without_image_posts_to_feed() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"id": "555"})

    publisher = _publisher(handler)
    response = asyncio.run(
        publisher.publish(
            PublishRequest(destination_platform="facebook_page", text="No image today.")
        )
    )

    assert response.external_post_id == "555"
    assert str(captured["url"]) == "https://graph.facebook.com/v21.0/page-1/feed"
    assert "message=No+image+today." in str(captured["body"])


def test_publish_rejects_malformed_json_response() -> None:
    publisher = _publisher(lambda request: httpx.Response(200, text="not-json"))

    with pytest.raises(PublisherMalformedResponseError):
        asyncio.run(
            publisher.publish(
                PublishRequest(destination_platform="facebook_page", text="hello")
            )
        )


def test_publish_rejects_response_missing_post_id() -> None:
    publisher = _publisher(lambda request: httpx.Response(200, json={"unrelated": True}))

    with pytest.raises(PublisherMalformedResponseError):
        asyncio.run(
            publisher.publish(
                PublishRequest(destination_platform="facebook_page", text="hello")
            )
        )


def test_publish_times_out_raises_normalized_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    publisher = _publisher(handler)

    with pytest.raises(PublisherTimeoutError):
        asyncio.run(
            publisher.publish(
                PublishRequest(destination_platform="facebook_page", text="hello")
            )
        )


def test_publish_connect_error_raises_normalized_publisher_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect", request=request)

    publisher = _publisher(handler)

    with pytest.raises(PublisherError):
        asyncio.run(
            publisher.publish(
                PublishRequest(destination_platform="facebook_page", text="hello")
            )
        )


@pytest.mark.parametrize(
    ("status_code", "error_code", "normalized_error"),
    [
        (401, None, PublisherAuthenticationError),
        (400, 190, PublisherAuthenticationError),
        (400, 4, PublisherRateLimitError),
        (400, 17, PublisherRateLimitError),
        (400, 32, PublisherRateLimitError),
        (400, 613, PublisherRateLimitError),
        (400, 368, PublisherContentRejectedError),
        (404, None, PublisherUnavailableError),
        (500, None, PublisherError),
    ],
)
def test_publish_normalizes_graph_api_error_codes(
    status_code: int,
    error_code: int | None,
    normalized_error: type[PublisherError],
) -> None:
    error_body: dict[str, object] = {"message": "sensitive provider detail"}
    if error_code is not None:
        error_body["code"] = error_code

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": error_body})

    publisher = _publisher(handler)

    with pytest.raises(normalized_error):
        asyncio.run(
            publisher.publish(
                PublishRequest(destination_platform="facebook_page", text="hello")
            )
        )


def test_publish_error_message_is_provider_message_not_raw_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": 368, "message": "policy violation"}})

    publisher = _publisher(handler)

    with pytest.raises(PublisherContentRejectedError, match="policy violation"):
        asyncio.run(
            publisher.publish(
                PublishRequest(destination_platform="facebook_page", text="hello")
            )
        )


def test_missing_credentials_fail_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FACEBOOK_PAGE_ID", raising=False)
    monkeypatch.delenv("FACEBOOK_PAGE_ACCESS_TOKEN", raising=False)

    with pytest.raises(PublisherAuthenticationError, match="no Facebook request was made"):
        FacebookPagePublisher.from_environment(load_dotenv=False)


@pytest.mark.parametrize("value", ["", "placeholder", "<FACEBOOK_PAGE_ACCESS_TOKEN>"])
def test_placeholder_access_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    monkeypatch.delenv("FACEBOOK_PAGE_ID", raising=False)
    monkeypatch.delenv("FACEBOOK_PAGE_ACCESS_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"FACEBOOK_PAGE_ID=page-1\nFACEBOOK_PAGE_ACCESS_TOKEN={value}\n",
        encoding="utf-8",
    )

    with pytest.raises(PublisherAuthenticationError):
        FacebookPagePublisher.from_environment(env_file=env_file)


def test_dotenv_loading_and_operating_system_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FACEBOOK_PAGE_ID=dotenv-page\nFACEBOOK_PAGE_ACCESS_TOKEN=dotenv-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FACEBOOK_PAGE_ID", "os-page")
    monkeypatch.setenv("FACEBOOK_PAGE_ACCESS_TOKEN", "os-token")

    publisher = FacebookPagePublisher.from_environment(env_file=env_file)

    assert publisher._page_id == "os-page"
    assert publisher._access_token == "os-token"


def test_from_environment_targets_configured_graph_api_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FACEBOOK_PAGE_ID", "page-1")
    monkeypatch.setenv("FACEBOOK_PAGE_ACCESS_TOKEN", "token-1")
    monkeypatch.setenv("FACEBOOK_GRAPH_API_VERSION", "v99.0")

    publisher = FacebookPagePublisher.from_environment(load_dotenv=False)

    assert publisher._api_version == "v99.0"
    assert str(publisher._new_client().base_url) == "https://graph.facebook.com/v99.0/"
