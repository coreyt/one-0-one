"""ChannelTabs — tabbed channel view with per-channel RichLog and unread badges."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog, Tab, Tabs

from src.session.config import ChannelConfig
from src.session.events import ChannelCreatedEvent, MessageEvent


class ChannelTabs(Widget):
    """Tabbed channel view. One RichLog per channel."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._channels: list[str] = ["public"]
        self._unread: dict[str, int] = {}
        self._labels: dict[str, str] = {"public": "Public"}
        self._active: str = "public"

    def compose(self) -> ComposeResult:
        yield Tabs(Tab("Public", id="tab_public"), id="channel-tabs")
        yield RichLog(id="log_public", highlight=True, markup=True)

    def add_channel(self, event: ChannelCreatedEvent) -> None:
        """Add a tab for a newly created channel (CHANNEL_CREATED handler)."""
        ch_id = event.channel_id
        if ch_id == "public" or ch_id in self._channels:
            return
        self._channels.append(ch_id)
        label = self._make_label(ch_id, event.channel_type, event.members)
        self._labels[ch_id] = label
        tabs = self.query_one(Tabs)
        tabs.add_tab(Tab(label, id=f"tab_{ch_id}"))
        log = RichLog(id=f"log_{ch_id}", highlight=True, markup=True)
        log.display = False
        self.mount(log)

    def append_message(self, event: MessageEvent) -> None:
        """Route a MessageEvent to the correct channel log."""
        ch_id = event.channel_id
        try:
            log = self.query_one(f"#log_{ch_id}", RichLog)
        except Exception:
            # Channel log doesn't exist yet; show in public
            log = self.query_one("#log_public", RichLog)
            ch_id = "public"

        text = self._format_message(event)
        log.write(text)

        if ch_id != self._active:
            self._unread[ch_id] = self._unread.get(ch_id, 0) + 1
            self._refresh_tab_label(ch_id)

    def append_system(self, text: str) -> None:
        """Append a dimmed system message to the public log."""
        log = self.query_one("#log_public", RichLog)
        log.write(f"[dim]─── [italic]{text}[/italic] ───[/dim]")

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None:
            return
        ch_id = event.tab.id.removeprefix("tab_")
        self._active = ch_id
        for log in self.query(RichLog):
            log.display = log.id == f"log_{ch_id}"
        self._unread.pop(ch_id, None)
        self._refresh_tab_label(ch_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_label(
        ch_id: str, ch_type: str, members: list[str] | None = None,
    ) -> str:
        if ch_id == "public":
            return "Public"
        if ch_type == "team":
            name = ch_id.removeprefix("team_").replace("_", " ").title()
            return f"Team: {name}"
        if ch_type == "private":
            if members and len(members) == 2:
                a = members[0].replace("_", " ").title()
                b = members[1].replace("_", " ").title()
                return f"{a} \u2192 {b}"
            return "Private"
        return ch_id.replace("_", " ").title()

    def _refresh_tab_label(self, ch_id: str) -> None:
        tabs = self.query_one(Tabs)
        unread = self._unread.get(ch_id, 0)
        base = self._labels.get(ch_id, ch_id)
        label = f"{base} [{unread}]" if unread > 0 else base
        try:
            tab = tabs.query_one(f"#tab_{ch_id}", Tab)
            tab.label = label
        except Exception:
            pass

    @staticmethod
    def _format_message(event: MessageEvent) -> str:
        is_private = event.recipient_id is not None
        is_parallel = event.is_parallel

        name_part = f"[bold]{event.agent_name}[/bold]"
        if is_private:
            recipient = event.recipient_id or ""
            name_part = f"[dim]\U0001f512 {event.agent_name} \u2192 {recipient}[/dim]"

        parallel_badge = " [dim]\\[parallel][/dim]" if is_parallel else ""

        if is_private:
            return f"{name_part}{parallel_badge}\n[dim]  {event.text}[/dim]"
        return f"{name_part}{parallel_badge}\n  {event.text}"
