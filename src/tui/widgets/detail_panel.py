"""TemplateDetailPanel — rich detail view for a SessionConfig."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog

from src.session.config import SessionConfig


class TemplateDetailPanel(Widget):
    """Renders full metadata from a SessionConfig into a scrollable RichLog."""

    DEFAULT_CSS = """
    TemplateDetailPanel {
        height: 1fr;
    }
    TemplateDetailPanel RichLog {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield RichLog(id="detail-log", wrap=True, markup=True)

    def show_config(self, config: SessionConfig | None) -> None:
        """Render a SessionConfig into the detail log."""
        log = self.query_one("#detail-log", RichLog)
        log.clear()
        if config is None:
            log.write("[dim]Select a template to view details.[/dim]")
            return
        self._render_config(log, config)

    def _render_config(self, log: RichLog, cfg: SessionConfig) -> None:
        # ── Header ──
        type_badge = f"[bold cyan]\\[{cfg.type}][/bold cyan]"
        log.write(f"[bold underline]{cfg.title}[/bold underline]  {type_badge}")
        if cfg.setting:
            log.write(f"[dim]Setting:[/dim] {cfg.setting}")
        if cfg.description:
            log.write(f"\n{cfg.description}")
        log.write("")

        # ── Game section ──
        if cfg.game:
            g = cfg.game
            log.write("[bold yellow]━━ Game ━━[/bold yellow]")
            log.write(f"[bold]{g.name}[/bold]")
            if g.description:
                log.write(f"[dim]{g.description}[/dim]")
            log.write("")

            if g.rules:
                log.write("[bold]Rules:[/bold]")
                for i, rule in enumerate(g.rules, 1):
                    log.write(f"  {i}. {rule}")
                log.write("")

            if g.how_to_play:
                log.write("[bold]How to Play:[/bold]")
                log.write(f"  {g.how_to_play.strip()}")
                log.write("")

            if g.win_condition:
                log.write(f"[bold]Win Condition:[/bold] {g.win_condition}")
            if g.turn_order:
                log.write(f"[bold]Turn Order:[/bold] {g.turn_order}")
            if g.max_rounds is not None:
                log.write(f"[bold]Max Rounds:[/bold] {g.max_rounds}")
            hitl_flag = "[green]Yes[/green]" if g.hitl_compatible else "[red]No[/red]"
            log.write(f"[bold]HITL Compatible:[/bold] {hitl_flag}")
            log.write("")

            if g.roles:
                log.write("[bold]Roles:[/bold]")
                log.write(f"  {'Name':<20} {'Count':<8} Description")
                log.write(f"  {'─' * 20} {'─' * 8} {'─' * 40}")
                for role in g.roles:
                    log.write(f"  {role.name:<20} {str(role.count):<8} {role.description}")
                log.write("")

        # ── Agent roster ──
        log.write("[bold yellow]━━ Agents ━━[/bold yellow]")
        log.write(f"  {'Name':<18} {'Provider/Model':<30} {'Role':<14} Team")
        log.write(f"  {'─' * 18} {'─' * 30} {'─' * 14} {'─' * 12}")
        for a in cfg.agents:
            prov_model = f"{a.provider}/{a.model}"
            team = a.team or "—"
            log.write(f"  {a.name:<18} {prov_model:<30} {a.role:<14} {team}")
        log.write("")

        # ── Channels ──
        if cfg.channels:
            log.write("[bold yellow]━━ Channels ━━[/bold yellow]")
            for ch in cfg.channels:
                members = ", ".join(ch.members) if ch.members else "all"
                log.write(f"  [bold]{ch.id}[/bold] ({ch.type}) — {members}")
            log.write("")

        # ── Orchestrator ──
        log.write("[bold yellow]━━ Orchestrator ━━[/bold yellow]")
        if cfg.orchestrator.type == "python":
            log.write(f"  Type: python  Module: {cfg.orchestrator.module}")
        else:
            prov = cfg.orchestrator.provider or "—"
            model = cfg.orchestrator.model or "—"
            log.write(f"  Type: llm  Provider: {prov}  Model: {model}")
        log.write("")

        # ── HITL ──
        log.write("[bold yellow]━━ HITL ━━[/bold yellow]")
        if cfg.hitl.enabled:
            role = cfg.hitl.role or "participant"
            log.write(f"  [green]Enabled[/green] — Role: {role}")
        else:
            log.write("  [dim]Disabled[/dim]")
        log.write("")

        # ── Session limits ──
        if cfg.max_turns or cfg.completion_signal:
            log.write("[bold yellow]━━ Session Limits ━━[/bold yellow]")
            if cfg.max_turns is not None:
                log.write(f"  Max Turns: {cfg.max_turns}")
            if cfg.completion_signal:
                log.write(f"  Completion Signal: \"{cfg.completion_signal}\"")
