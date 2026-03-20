"""AgentRoster — read-only sidebar DataTable showing agent pool status."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable

from src.session.config import AgentConfig
from src.tui.colors import agent_color


_COLUMNS = ("Name", "Model", "Status")


class AgentRoster(Widget):
    """DataTable showing agent pool — read-only, cursor disabled."""

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="none", id="roster-table")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*_COLUMNS)

    def populate(self, agents: list[AgentConfig]) -> None:
        """Populate the table from agent configs."""
        table = self.query_one(DataTable)
        table.clear()
        for i, agent in enumerate(agents):
            color = agent_color(i)
            table.add_row(
                f"[{color}]{agent.name}[/]",
                agent.display_model,
                "idle",
                key=agent.id,
            )

    def set_status(self, agent_id: str, status: str) -> None:
        """Update the Status cell for a given agent."""
        table = self.query_one(DataTable)
        try:
            table.update_cell(agent_id, "Status", status)
        except Exception:
            pass
