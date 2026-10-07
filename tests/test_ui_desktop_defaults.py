from pathlib import Path
import unittest

from smartscc_tools.core import theme as T


class UiDesktopDefaultsTest(unittest.TestCase):
    def test_shared_log_minimum_is_15_lines(self) -> None:
        self.assertEqual(T.MIN_LOG_VISIBLE_LINES, 15)

    def test_log_panes_use_shared_minimum_constant(self) -> None:
        edit_text = Path("smartscc_tools/modules/edit_transaksi_module.py").read_text(encoding="utf-8")
        item_journal_text = Path("smartscc_tools/features/item_journal/entrypoints/gui.py").read_text(encoding="utf-8")

        self.assertIn("height=max(6, T.MIN_LOG_VISIBLE_LINES)", edit_text)
        self.assertIn("height=max(14, T.MIN_LOG_VISIBLE_LINES)", item_journal_text)

    def test_repo_agents_document_log_and_scroll_ui_rules(self) -> None:
        agents_text = Path("AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("at least 15 visible rows", agents_text)
        self.assertIn("meaningful header summary or one-line compact preview", agents_text)
        self.assertIn("Shared scroll tuning belongs in the shared scroll widget", agents_text)

    def test_personal_desktop_ui_skill_exists_and_records_defaults(self) -> None:
        skill_root = Path.home() / ".codex" / "skills" / "personal-desktop-ui-preferences"
        skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        openai_yaml_text = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertIn("at least 15 visible lines", skill_text)
        self.assertIn("smaller immediate steps", skill_text)
        self.assertIn("$personal-desktop-ui-preferences", openai_yaml_text)


if __name__ == "__main__":
    unittest.main()
