import tkinter as tk
import unittest

from smartscc_tools.core import theme as T
from smartscc_tools.widgets.collapsible_section import CollapsibleSection, build_compact_preview_text


class CollapsibleSectionWidgetTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self) -> None:
        if hasattr(self, "root"):
            self.root.destroy()

    def test_toggle_button_labels_and_summary_visibility(self) -> None:
        section = CollapsibleSection(self.root, key="logs", title="Logs", expanded=True)
        tk.Label(section.body, text="full").pack()
        section.pack(fill="x")
        section.set_summary("Latest ready")
        section.refresh_layout()
        self.root.update_idletasks()

        self.assertEqual(section.toggle_button.cget("text"), "Hide")
        self.assertEqual(section.toggle_button.cget("bg"), T.BRAND_PRIMARY)
        self.assertEqual(section.toggle_button.cget("fg"), T.TEXT_ON_DARK)
        self.assertEqual(section.summary_label.winfo_manager(), "")
        self.assertEqual(int(section.toggle_button.grid_info()["column"]), 0)
        self.assertEqual(int(section.title_label.grid_info()["column"]), 1)
        self.assertEqual(section.header_divider.winfo_manager(), "pack")

        section.set_open(False, emit=False)
        self.root.update_idletasks()

        self.assertEqual(section.toggle_button.cget("text"), "Show More")
        self.assertEqual(section.summary_var.get(), "Latest ready")
        self.assertEqual(section.summary_label.winfo_manager(), "grid")
        self.assertEqual(int(section.summary_label.grid_info()["column"]), 2)
        self.assertEqual(section.body.winfo_manager(), "")

    def test_compact_body_only_shows_when_collapsed_by_default(self) -> None:
        section = CollapsibleSection(self.root, key="simple", title="Simple", expanded=True)
        tk.Label(section.compact_body, text="compact").pack()
        tk.Label(section.body, text="full").pack()
        section.pack(fill="x")
        section.refresh_layout()
        self.root.update_idletasks()

        self.assertEqual(section.compact_body.winfo_manager(), "")
        self.assertEqual(section.body.winfo_manager(), "pack")
        self.assertEqual(section.header_divider.winfo_manager(), "pack")

        section.set_open(False, emit=False)
        self.root.update_idletasks()

        self.assertEqual(section.compact_body.winfo_manager(), "pack")
        self.assertEqual(section.body.winfo_manager(), "")
        self.assertEqual(section.header_divider.winfo_manager(), "pack")

    def test_compact_body_can_remain_visible_when_expanded(self) -> None:
        section = CollapsibleSection(
            self.root,
            key="actions",
            title="Action & Progress",
            expanded=True,
            show_compact_when_open=True,
        )
        tk.Label(section.compact_body, text="compact").pack()
        tk.Label(section.body, text="full").pack()
        section.pack(fill="x")
        section.refresh_layout()
        self.root.update_idletasks()

        self.assertEqual(section.compact_body.winfo_manager(), "pack")
        self.assertEqual(section.body.winfo_manager(), "pack")
        self.assertEqual(section.header_divider.winfo_manager(), "pack")

    def test_header_divider_hides_when_collapsed_without_compact_content(self) -> None:
        section = CollapsibleSection(self.root, key="simple", title="Simple", expanded=True)
        tk.Label(section.body, text="full").pack()
        section.pack(fill="x")
        section.set_summary("Ringkas")
        section.refresh_layout()
        self.root.update_idletasks()

        section.set_open(False, emit=False)
        self.root.update_idletasks()

        self.assertEqual(section.header_divider.winfo_manager(), "")
        self.assertEqual(section.body.winfo_manager(), "")

    def test_build_compact_preview_text_trims_and_flattens(self) -> None:
        self.assertEqual(
            build_compact_preview_text("Baris satu\nBaris dua", empty_text="Belum ada log.", max_chars=120),
            "Baris satu Baris dua",
        )
        self.assertEqual(
            build_compact_preview_text("", empty_text="Belum ada log.", max_chars=120),
            "Belum ada log.",
        )
        self.assertEqual(
            build_compact_preview_text("A" * 20, empty_text="-", max_chars=10),
            "AAAAAAA...",
        )


if __name__ == "__main__":
    unittest.main()
