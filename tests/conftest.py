"""
Shared pytest fixtures and hooks for the test suite.
"""

import logging

import pytest


@pytest.fixture(autouse=True)
def _live_test_output(request):
    """For *_live_e2e* tests, suppress INFO/DEBUG log noise so the shot-by-shot
    game output is easy to read.  Run these tests with:
        uv run pytest tests/test_*_live_e2e.py -s -v
    """
    if "live_e2e" not in request.node.nodeid:
        yield
        return

    # Suppress INFO/DEBUG from the application's structured logger so the
    # readable print() output (shot log, violations, session end) stands out.
    from src.logging import configure_logging  # noqa: PLC0415

    configure_logging(level="WARNING")
    try:
        yield
    finally:
        # Restore INFO for subsequent tests in the same session.
        configure_logging(level="INFO")
