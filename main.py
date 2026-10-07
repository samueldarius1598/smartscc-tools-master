"""Bootstrap entrypoint for Smart's CC Tools Master."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    from smartscc_tools.entrypoints.gui import launch_tools_master_gui
    if not args:
        return launch_tools_master_gui()
    if args[0].strip().lower() == "gui":
        return launch_tools_master_gui()
    if args[0].strip().lower() == "svl-dashboard-analyze":
        from smartscc_tools.features.svl_fix_je.dashboard_cli import main as dashboard_cli_main

        return dashboard_cli_main(args[1:])
    if args[0].strip().lower() == "svl-dashboard-gui-inspect":
        from smartscc_tools.features.svl_fix_je.dashboard_gui_cli import main as dashboard_gui_cli_main

        return dashboard_gui_cli_main(args[1:])
    from smartscc_tools.features.item_journal.entrypoints import cli
    return cli.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
