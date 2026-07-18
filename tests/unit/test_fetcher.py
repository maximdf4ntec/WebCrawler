"""
Unit tests for Fetcher (fetcher.py) — Gap #1.

Tests the actual I/O boundary: verifies that MockApiFetcher and HttpFetcher
correctly translate httpx exceptions into the exception types (ConnectionError,
TimeoutError, TransientError) that the Worker expects.

Uses httpx mock transport to simulate network conditions without real I/O.
"""

import pytest
import httpx

from crawler.fetcher import MockApiFetcher, HttpFetcher, Fetcher
from crawler.types import FetchResponse, TransientError


# ---------------------------------------------------------------------------
# MockApiFetcher tests
# ---------------------------------------------------------------------------


class TestMockApiFetcherHappyPath:
    """MockApiFetcher correctly unwraps the JSON envelope on success."""

    @pytest.mark.asyncio
    async def test_returns_fetch_response_from_valid_json(self) -> None:
        """Valid JSON envelope → FetchResponse with status, headers, body."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "headers": {"content-type": "text/html"},
                    "body": "<html>hello</html>",
                },
            )

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = MockApiFetcher(client=client)

        result = await fetcher.fetch("https://example.com/page")

        assert isinstance(result, FetchResponse)
        assert result.status_code == 200
        assert result.headers["content-type"] == "text/html"
        assert result.body == b"<html>hello</html>"

    @pytest.mark.asyncio
    async def test_returns_none_body_when_body_is_null(self) -> None:
        """JSON body: null → FetchResponse.body is None."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"statusCode": 200, "headers": {}, "body": None},
            )

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = MockApiFetcher(client=client)

        result = await fetcher.fetch("https://example.com/empty")
        assert result.body is None

    @pytest.mark.asyncio
    async def test_encodes_url_in_query_parameter(self) -> None:
        """The target URL is URL-encoded in the query string to mock API."""
        captured_url = None

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_url
            captured_url = str(request.url)
            return httpx.Response(
                200,
                json={"statusCode": 200, "headers": {}, "body": "ok"},
            )

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = MockApiFetcher(client=client)

        await fetcher.fetch("https://example.com/path?q=hello world")
        assert "mock-api.mock.com/fetch?url=" in captured_url


class TestMockApiFetcherErrorTranslation:
    """MockApiFetcher translates httpx errors into expected exception types."""

    @pytest.mark.asyncio
    async def test_connect_error_raises_connection_error(self) -> None:
        """httpx.ConnectError → ConnectionError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = MockApiFetcher(client=client)

        with pytest.raises(ConnectionError):
            await fetcher.fetch("https://example.com/fail")

    @pytest.mark.asyncio
    async def test_timeout_raises_timeout_error(self) -> None:
        """httpx.TimeoutException → TimeoutError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Read timed out")

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = MockApiFetcher(client=client)

        with pytest.raises(TimeoutError):
            await fetcher.fetch("https://example.com/slow")

    @pytest.mark.asyncio
    async def test_malformed_json_raises_transient_error(self) -> None:
        """Invalid JSON from mock API → TransientError (Req 5.9)."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json at all")

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = MockApiFetcher(client=client)

        with pytest.raises(TransientError, match="malformed fetch response"):
            await fetcher.fetch("https://example.com/bad")

    @pytest.mark.asyncio
    async def test_missing_status_code_field_raises_transient_error(self) -> None:
        """JSON missing 'statusCode' field → TransientError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"headers": {}, "body": ""})

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = MockApiFetcher(client=client)

        with pytest.raises(TransientError, match="malformed fetch response"):
            await fetcher.fetch("https://example.com/incomplete")

    @pytest.mark.asyncio
    async def test_generic_http_error_raises_transient_error(self) -> None:
        """Other httpx.HTTPError → TransientError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.RemoteProtocolError("Server sent bad data")

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = MockApiFetcher(client=client)

        with pytest.raises(TransientError):
            await fetcher.fetch("https://example.com/protocol-error")


# ---------------------------------------------------------------------------
# HttpFetcher tests
# ---------------------------------------------------------------------------


class TestHttpFetcherHappyPath:
    """HttpFetcher translates real HTTP responses to FetchResponse."""

    @pytest.mark.asyncio
    async def test_returns_fetch_response_from_200(self) -> None:
        """HTTP 200 → FetchResponse with correct status, headers, body."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html", "x-custom": "val"},
                content=b"<html>page</html>",
            )

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = HttpFetcher(client=client)

        result = await fetcher.fetch("https://example.com/page")

        assert result.status_code == 200
        assert result.headers["content-type"] == "text/html"
        assert result.body == b"<html>page</html>"

    @pytest.mark.asyncio
    async def test_does_not_follow_redirects(self) -> None:
        """HttpFetcher uses follow_redirects=False — reports 301/302 directly."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                301,
                headers={"location": "https://example.com/new"},
            )

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = HttpFetcher(client=client)

        result = await fetcher.fetch("https://example.com/old")
        assert result.status_code == 301
        assert result.headers["location"] == "https://example.com/new"


class TestHttpFetcherErrorTranslation:
    """HttpFetcher translates httpx errors into expected exception types."""

    @pytest.mark.asyncio
    async def test_connect_error_raises_connection_error(self) -> None:
        """httpx.ConnectError → ConnectionError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("DNS resolution failed")

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = HttpFetcher(client=client)

        with pytest.raises(ConnectionError):
            await fetcher.fetch("https://nonexistent.example.com")

    @pytest.mark.asyncio
    async def test_timeout_raises_timeout_error(self) -> None:
        """httpx.TimeoutException → TimeoutError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Timed out")

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = HttpFetcher(client=client)

        with pytest.raises(TimeoutError):
            await fetcher.fetch("https://example.com/slow")

    @pytest.mark.asyncio
    async def test_protocol_error_raises_transient_error(self) -> None:
        """Other httpx errors → TransientError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.RemoteProtocolError("Garbage data")

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = HttpFetcher(client=client)

        with pytest.raises(TransientError):
            await fetcher.fetch("https://example.com/broken")
