"""GUI entrypoint for Smart's CC Tools Master."""

from __future__ import annotations

import logging
from time import perf_counter

from smartscc_tools.branding import set_windows_app_user_model_id
from smartscc_tools.shell.dashboard import DashboardWindow


def launch_tools_master_gui() -> int:
    set_windows_app_user_model_id()
    started = perf_counter()
    app = DashboardWindow()
    logging.getLogger("smartscc_tools").info(
        "[STARTUP] DashboardWindow init: %.0fms",
        (perf_counter() - started) * 1000.0,
    )
    return app.run()
