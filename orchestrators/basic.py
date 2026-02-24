"""
Basic round-robin Python orchestrator — user-facing root copy.

This file is the version loaded by session templates that specify:
    orchestrator:
      type: python
      module: basic

It is kept in sync with src/orchestrators/basic.py.
Place custom orchestrators alongside this file in the orchestrators/ directory.
See dev/preliminary-solution-design.md §3 for the orchestrator interface contract.
"""

# Re-export from the canonical implementation
from src.orchestrators.basic import orchestrate  # noqa: F401

__all__ = ["orchestrate"]
