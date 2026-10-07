from pathlib import Path
import unittest


class PackagingUpdateTest(unittest.TestCase):
    def test_build_script_emits_latest_json_manifest(self) -> None:
        script_text = Path("build_tools/build_installer.ps1").read_text(encoding="utf-8")

        self.assertIn("latest.json", script_text)
        self.assertIn("Get-FileHash", script_text)
        self.assertIn("release_notes_url", script_text)
        self.assertIn("published_at", script_text)

    def test_inno_setup_closes_running_app_during_upgrade(self) -> None:
        iss_text = Path("build_tools/packaging/windows/installer.iss").read_text(encoding="utf-8")

        self.assertIn("CloseApplications=yes", iss_text)
        self.assertIn("CloseApplicationsFilter={#AppExeName}", iss_text)
        self.assertIn("RestartApplications=no", iss_text)

    def test_install_script_uses_zipfile_extract_and_runtime_validation(self) -> None:
        script_text = Path("build_tools/packaging/windows/install_app.ps1").read_text(encoding="utf-8")

        self.assertIn("Expand-PayloadZip", script_text)
        self.assertIn("ZipFileExtensions", script_text)
        self.assertIn("Assert-InstalledRuntime", script_text)
        self.assertIn("_internal\\python313.dll", script_text)
        self.assertNotIn("Expand-Archive", script_text)

    def test_install_script_stamps_shortcuts_with_app_user_model_id(self) -> None:
        script_text = Path("build_tools/packaging/windows/install_app.ps1").read_text(encoding="utf-8")

        self.assertIn("Set-ShortcutAppUserModelId", script_text)
        self.assertIn("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3", script_text)
        self.assertIn('smartscc.tools.master', script_text)
        self.assertIn('mainlogo.ico', script_text)


if __name__ == "__main__":
    unittest.main()
