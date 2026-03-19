from __future__ import annotations

import logging

from src.logging import configure_logging


def test_configure_logging_tui_routes_gameplay_and_application_logs(tmp_path):
    from src.settings import settings

    original_sessions = settings.sessions_path
    original_logs = settings.logs_path
    try:
        settings.sessions_path = tmp_path / "sessions"
        settings.logs_path = tmp_path / "logs"
        configure_logging("INFO", mode="tui")

        logging.getLogger("src.session.engine").info("session.started gameplay")
        logging.getLogger("src.tui.app").info("tui.started application")

        gameplay_logs = sorted(settings.sessions_path.glob("gameplay_*.log"))
        app_logs = sorted(settings.logs_path.glob("application_*.log"))

        assert gameplay_logs
        assert app_logs
        gameplay_log = gameplay_logs[-1]
        app_log = app_logs[-1]
        assert "session.started gameplay" in gameplay_log.read_text(encoding="utf-8")
        assert "tui.started application" not in gameplay_log.read_text(encoding="utf-8")
        assert "tui.started application" in app_log.read_text(encoding="utf-8")
        assert "session.started gameplay" not in app_log.read_text(encoding="utf-8")
    finally:
        settings.sessions_path = original_sessions
        settings.logs_path = original_logs
