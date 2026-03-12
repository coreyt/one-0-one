"""
Market Research focus group orchestrator — user-facing root copy.

This file is loaded by session templates that specify:
    orchestrator:
      type: python
      module: market_research

It is kept in sync with src/orchestrators/market_research.py.
See dev/preliminary-solution-design.md §3 for the orchestrator interface contract.
"""

# Re-export from the canonical implementation
from src.orchestrators.market_research import orchestrate  # noqa: F401

__all__ = ["orchestrate"]
