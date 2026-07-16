"""Fetcher abstraction — pluggable HTTP fetch strategies.

Provides an ABC that the Worker calls to retrieve a URL's content,
plus two implementations:
- MockApiFetcher: Hits the mock Fetch API (original behavior)
- HttpFetcher: Makes real HTTP requests for crawling live websites

Requirements: 5.1, 5.9
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional
from urllib.parse import quote

import httpx

from crawler.types import FetchResponse, TransientError

logger = logging.getLogger(__name__)


class Fetcher(ABC):
    """Abstract base class for URL fetching strategies.

    All fetch implementations must return a FetchResponse with status_code,
    headers, and body. Transient failures (network errors, malformed responses)
    should raise TransientError. Permanent transport-level errors that prevent
    any response should raise appropriate exceptions (ConnectionError, etc.).
    """

    @abstractmethod
    async def fetch(self, url: str) -> FetchResponse:
        """Fetch the given URL and return a structured response.

        Args:
            url: The URL to fetch.

        Returns:
            FetchResponse with status_code, headers dict, and body bytes.

        Raises:
            TransientError: For recoverable issues (malformed response, etc.)
            ConnectionError: For network-level failures.
            TimeoutError: For request timeouts.
        """
        ...


class MockApiFetcher(Fetcher):
    """Fetches URLs through the mock Fetch API endpoint.

    Sends GET requests to http://mock-api.mock.com/fetch?url=<encoded_url>
    and unwraps the JSON envelope {statusCode, headers, body} into a
    FetchResponse.

    This is the original fetch_url behavior extracted from Worker.
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        """Initialize with an optional shared httpx client.

        Args:
            client: Shared AsyncClient for connection pooling.
                    If None, creates an ephemeral client per request.
        """
        self._client = client

    async def fetch(self, url: str) -> FetchResponse:
        """Fetch via the mock API, unwrap JSON envelope.

        Raises:
            TransientError: If the mock API response is not valid JSON or
                is missing required fields. Per Req 5.9, malformed responses
                are transient and should be retried.
        """
        encoded_url = quote(url, safe="")
        try:
            if self._client is not None:
                resp = await self._client.get(
                    f"http://mock-api.mock.com/fetch?url={encoded_url}"
                )
            else:
                async with httpx.AsyncClient() as ephemeral_client:
                    resp = await ephemeral_client.get(
                        f"http://mock-api.mock.com/fetch?url={encoded_url}"
                    )
            data = resp.json()
            body = data.get("body")
            if isinstance(body, str):
                body = body.encode("utf-8")
            return FetchResponse(
                status_code=data["statusCode"],
                headers=data.get("headers", {}),
                body=body,
            )
        except (ValueError, KeyError) as e:
            # ValueError: JSON decode failure; KeyError: missing required fields.
            # Per Requirement 5.9, treat as transient error for retry.
            raise TransientError(f"malformed fetch response: {e}") from e


class HttpFetcher(Fetcher):
    """Fetches URLs via real HTTP requests for crawling live websites.

    Supports HTML pages, images, PDFs, and other content types.
    Maps real HTTP responses into FetchResponse objects compatible
    with the Worker's status-code dispatch logic.
    """

    # Default timeout for individual requests (seconds)
    _DEFAULT_TIMEOUT = 30.0

    # Default User-Agent to identify the crawler
    _DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; WebCrawler/1.0; +https://github.com/example/webcrawler)"

    # Maximum response body size to read (bytes). Protects against
    # unbounded memory usage on streaming responses.
    _MAX_STREAM_SIZE = 100 * 1024 * 1024  # 100 MB

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        user_agent: Optional[str] = None,
        max_response_size: int = _MAX_STREAM_SIZE,
    ) -> None:
        """Initialize the HTTP fetcher.

        Args:
            client: Shared AsyncClient for connection pooling.
                    If None, creates an ephemeral client per request.
            timeout: Request timeout in seconds.
            user_agent: Custom User-Agent header value.
            max_response_size: Maximum body size to read (bytes).
        """
        self._client = client
        self._timeout = timeout
        self._user_agent = user_agent or self._DEFAULT_USER_AGENT
        self._max_response_size = max_response_size

    async def fetch(self, url: str) -> FetchResponse:
        """Perform a real HTTP GET request and return a FetchResponse.

        Follows redirects up to httpx's default limit but reports
        the final response status. For redirect tracking at the crawler
        level, set follow_redirects=False so the Worker handles them.

        Raises:
            ConnectionError: On DNS failure, connection refused, etc.
            TimeoutError: On request timeout.
            TransientError: On unexpected transport errors.
        """
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "*/*",
        }

        try:
            if self._client is not None:
                resp = await self._client.get(
                    url,
                    headers=headers,
                    follow_redirects=False,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as ephemeral:
                    resp = await ephemeral.get(
                        url,
                        headers=headers,
                        follow_redirects=False,
                    )

            # Read the body (respecting size limit)
            body = resp.content
            if len(body) > self._max_response_size:
                body = body[: self._max_response_size]

            # Convert httpx headers to a plain dict (lowercase keys)
            response_headers: dict[str, str] = {}
            for key, value in resp.headers.items():
                # httpx headers are already lowercase
                response_headers[key] = value

            return FetchResponse(
                status_code=resp.status_code,
                headers=response_headers,
                body=body,
            )

        except httpx.TimeoutException as e:
            raise TimeoutError(f"Request timed out for {url}: {e}") from e
        except httpx.ConnectError as e:
            raise ConnectionError(f"Connection failed for {url}: {e}") from e
        except httpx.HTTPStatusError as e:
            # This shouldn't happen with raise_for_status disabled,
            # but handle defensively
            raise TransientError(f"HTTP error for {url}: {e}") from e
        except httpx.HTTPError as e:
            # Catch-all for other httpx transport errors
            raise TransientError(f"Transport error fetching {url}: {e}") from e
