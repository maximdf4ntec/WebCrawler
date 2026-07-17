"""CLI entry point for the web crawler.

Provides two subcommands:
  - crawl: Start a new crawl from a YAML configuration file
  - resume: Resume a previously interrupted crawl from its database

Usage:
  python -m crawler crawl --config config.yaml [--db crawl.db] [--output output]
  python -m crawler resume [--db crawl.db] [--output output]

Requirements: 1.1, 6.1, 15.1, 18.2
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from crawler.crawler import Crawler
from crawler.types import CrawlResult


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with crawl and resume subcommands."""
    parser = argparse.ArgumentParser(
        prog="crawler",
        description="Web crawler — recursively crawl a website from a seed URL.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- crawl subcommand ---
    crawl_parser = subparsers.add_parser(
        "crawl", help="Start a new crawl from a YAML config file"
    )
    crawl_parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to the YAML configuration file",
    )
    crawl_parser.add_argument(
        "--db",
        type=str,
        default="crawl.db",
        help="Path to the SQLite database file (default: crawl.db)",
    )
    crawl_parser.add_argument(
        "--output",
        type=str,
        default="output",
        help="Base output directory for crawled content (default: output)",
    )
    crawl_parser.add_argument(
        "--real",
        action="store_true",
        help="Use real HTTP requests (HttpFetcher) instead of the mock Fetch API",
    )

    # --- resume subcommand ---
    resume_parser = subparsers.add_parser(
        "resume", help="Resume a previously interrupted crawl"
    )
    resume_parser.add_argument(
        "--db",
        type=str,
        default="crawl.db",
        help="Path to the SQLite database file (default: crawl.db)",
    )
    resume_parser.add_argument(
        "--output",
        type=str,
        default="output",
        help="Base output directory for crawled content (default: output)",
    )
    resume_parser.add_argument(
        "--real",
        action="store_true",
        help="Use real HTTP requests (HttpFetcher) instead of the mock Fetch API",
    )

    return parser


def _print_result(result: CrawlResult) -> None:
    """Print crawl result summary to stdout."""
    duration_s = result.duration_ms / 1000
    print("\n--- Crawl Complete ---")
    print(f"  Total discovered: {result.total_discovered}")
    print(f"  Completed:        {result.total_completed}")
    print(f"  Failed:           {result.total_failed}")
    print(f"  Terminal failed:  {result.total_terminal_failed}")
    print(f"  Duration:         {duration_s:.2f}s")


async def _run_crawl(args: argparse.Namespace) -> None:
    """Execute the crawl subcommand."""
    crawler = Crawler(
        db_path=args.db, output_path=args.output, use_mock_api=not args.real
    )
    config_path = Path(args.config)

    if not config_path.exists():
        raise ValueError(f"Config file not found: {config_path}")

    result = await crawler.start(config_path)
    _print_result(result)


async def _run_resume(args: argparse.Namespace) -> None:
    """Execute the resume subcommand."""
    crawler = Crawler(
        db_path=args.db, output_path=args.output, use_mock_api=not args.real
    )
    result = await crawler.resume(db_path=args.db)
    _print_result(result)


def main() -> None:
    """Parse CLI arguments and run the crawler."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "crawl":
            asyncio.run(_run_crawl(args))
        elif args.command == "resume":
            asyncio.run(_run_resume(args))
    except KeyboardInterrupt:
        print("\nCrawl interrupted by user.", file=sys.stderr)
        sys.exit(130)
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"Runtime error: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"I/O error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
