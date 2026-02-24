"""
Agent memory stubs.

Persistent cross-session memory is tracked in GitHub issue #1.
These no-ops maintain the interface contract so the session engine
can call them unconditionally — swapping in a real implementation
later requires no engine changes.
"""


def save_memory(agent_id: str, session_id: str, data: dict) -> None:
    """Stub. Persist agent memory after a session ends.

    See GitHub issue #1 for the planned implementation.
    """
    pass


def load_memory(agent_id: str) -> dict:
    """Stub. Load agent memory before a session starts.

    See GitHub issue #1 for the planned implementation.
    Returns an empty dict until persistent memory is implemented.
    """
    return {}
