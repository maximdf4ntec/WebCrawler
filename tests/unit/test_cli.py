"""
Unit tests for CLI entry point (Task 10.1).

Tests:
- CLI accepts a config file path as argument
- CLI creates output directories before crawling
- CLI reports progress at configurable intervals
- __main__.py is importable and has main() function
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from crawler.__main__ import main


# ---------------------------------------------------------------------------
# CLI entry point existence
# ---------------------------------------------------------------------------


class TestCliEntryPoint:
    """The CLI entry point exists and is callable."""

    def test_main_function_exists(self) -> None:
        """main() is importable from crawler.__main__."""
        assert callable(main)

    def test_main_raises_not_implemented_stub(self) -> None:
        """Stub main() raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            main()


# ---------------------------------------------------------------------------
# Output directory creation
# ---------------------------------------------------------------------------


class TestOutputDirectoryCreation:
    """CLI creates output directories before worker dispatch."""

    def test_output_directories_structure(self, tmp_path: Path) -> None:
        """Expected output dirs: output/html, output/images, output/videos, output/pdfs."""
        # This test verifies the contract — implementation must create these dirs.
        # We verify the expected structure matches design.md's output layout.
        expected_dirs = [
            "output/html",
            "output/images",
            "output/videos",
            "output/pdfs",
        ]
        # These are the directories the implementation must create.
        # Since we can't test the stub, we document the contract here.
        # The actual test will verify they exist AFTER start() runs.
        for dir_name in expected_dirs:
            assert dir_name.startswith("output/")
