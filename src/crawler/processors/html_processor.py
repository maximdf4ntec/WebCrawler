"""HTML Processor — parses HTML responses to extract links, title, and persist content.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6
"""

import asyncio
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from crawler.content_dispatcher import BaseProcessor
from crawler.logger import get_logger
from crawler.types import FetchResponse, HtmlMetadata, LeaseResult, ProcessorResult

logger = get_logger()


def _parse_html(body: bytes, base_url: str) -> tuple[str, list[str]]:
    """Parse HTML body and extract title + links (CPU-bound, run in thread).

    Extracts:
      - Page title from <title> element (empty string if absent)
      - Links from <a href>, <img src>, <video src>, <script src>

    Resolves relative URLs against base_url and filters to http/https only.

    Args:
        body: Raw HTML bytes.
        base_url: The page URL used to resolve relative references.

    Returns:
        Tuple of (page_title, list_of_resolved_urls).
    """
    soup = BeautifulSoup(body, "lxml")

    # Extract title
    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else ""

    # Extract links from specified elements
    raw_links: list[str] = []

    # <a href="...">
    for tag in soup.find_all("a", href=True):
        raw_links.append(tag["href"])

    # <img src="...">, <video src="...">, <script src="...">
    for tag_name in ("img", "video", "script"):
        for tag in soup.find_all(tag_name, src=True):
            raw_links.append(tag["src"])

    # Resolve relative URLs and filter to http/https
    discovered_urls: list[str] = []
    for link in raw_links:
        try:
            resolved = urljoin(base_url, link)
            parsed = urlparse(resolved)
            if parsed.scheme in ("http", "https"):
                discovered_urls.append(resolved)
            else:
                logger.debug(
                    "html_processor_skipped_non_http_link",
                    link=link,
                    resolved=resolved,
                    base_url=base_url,
                )
        except Exception:
            logger.debug(
                "html_processor_unresolvable_link",
                link=link,
                base_url=base_url,
            )

    return page_title, discovered_urls


class HtmlProcessor(BaseProcessor):
    """Processes text/html responses: extracts links, title, and persists raw HTML.

    Workflow:
      1. Compute content hash of raw body
      2. Check if hash matches stored hash (skip re-persist if unchanged)
      3. Parse HTML to extract title and links (offloaded to thread pool)
      4. Persist raw HTML to output/html/<hash>.html
      5. Record metadata (page_title, link_count) in MetadataStore
      6. Return ProcessorResult with discovered URLs
    """

    async def process(
        self, response: FetchResponse, lease: LeaseResult, store: object
    ) -> ProcessorResult:
        """Parse HTML, extract links/title, persist, return result."""
        body = response.body or b""
        content_hash = self.compute_hash(body)
        file_path = f"output/html/{content_hash}.html"

        # Check if content is unchanged (deduplication / change detection)
        stored_hash = await store.get_content_hash(lease.normalized_url)  # type: ignore[attr-defined]
        if stored_hash == content_hash:
            # Content unchanged — skip re-persist, update timestamp only
            await store.update_timestamp(lease.normalized_url)  # type: ignore[attr-defined]
            return ProcessorResult(
                discovered_urls=[],
                metadata={"page_title": "", "link_count": 0},
                content_hash=content_hash,
                file_path=file_path,
            )

        # Offload CPU-bound HTML parsing to thread pool
        base_url = lease.url
        page_title, discovered_urls = await asyncio.to_thread(
            _parse_html, body, base_url
        )

        # Persist raw HTML
        await self.write_file_if_not_exists(file_path, body)

        # Record metadata in store
        link_count = len(discovered_urls)
        html_metadata = HtmlMetadata(page_title=page_title, link_count=link_count)
        await store.store_html_metadata(  # type: ignore[attr-defined]
            normalized_url=lease.normalized_url,
            metadata=html_metadata,
        )

        return ProcessorResult(
            discovered_urls=discovered_urls,
            metadata={"page_title": page_title, "link_count": link_count},
            content_hash=content_hash,
            file_path=file_path,
        )
