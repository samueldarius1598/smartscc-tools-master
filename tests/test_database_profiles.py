import unittest

from smartscc_tools.services.odoo.profiles import (
    DatabaseProfile,
    FOLLOW_GLOBAL_PROFILE_ID,
    build_global_default_database_options,
    build_module_database_options,
    ensure_database_profile,
    migrate_legacy_global_db_override,
    render_database_profile_label,
    resolve_database_selection,
)


class DatabaseProfilesTest(unittest.TestCase):
    def test_render_label_uses_alias_value_and_note(self) -> None:
        label = render_database_profile_label(
            DatabaseProfile(
                profile_id="db_live",
                database_value="hwgroup_erp",
                alias="Live ERP",
                note="Database Live",
            )
        )

        self.assertEqual(label, "Live ERP - hwgroup_erp [Database Live]")

    def test_build_options_include_synthetic_entries(self) -> None:
        profiles = [DatabaseProfile(profile_id="db_live", database_value="hwgroup_erp", alias="Live ERP")]

        module_options = build_module_database_options(profiles)
        global_options = build_global_default_database_options(profiles)

        self.assertEqual(module_options[0][0], FOLLOW_GLOBAL_PROFILE_ID)
        self.assertEqual(global_options[0][0], "")

    def test_resolve_database_selection_prefers_module_then_global_then_gas(self) -> None:
        profiles = [
            DatabaseProfile(profile_id="db_live", database_value="hwgroup_erp", alias="Live ERP"),
            DatabaseProfile(profile_id="db_dummy", database_value="hwgroup_erp_22022026", alias="Dummy ERP"),
        ]

        self.assertEqual(
            resolve_database_selection(
                profiles=profiles,
                module_profile_id="db_dummy",
                default_profile_id="db_live",
                gas_default_database="gas-db",
            ),
            "hwgroup_erp_22022026",
        )
        self.assertEqual(
            resolve_database_selection(
                profiles=profiles,
                module_profile_id=FOLLOW_GLOBAL_PROFILE_ID,
                default_profile_id="db_live",
                gas_default_database="gas-db",
            ),
            "hwgroup_erp",
        )
        self.assertEqual(
            resolve_database_selection(
                profiles=profiles,
                module_profile_id=FOLLOW_GLOBAL_PROFILE_ID,
                default_profile_id="",
                gas_default_database="gas-db",
            ),
            "gas-db",
        )

    def test_ensure_database_profile_and_legacy_migration_create_profile_once(self) -> None:
        profiles: list[DatabaseProfile] = []

        default_profile_id = migrate_legacy_global_db_override(
            profiles=profiles,
            default_profile_id="",
            legacy_db_override="legacy-db",
        )
        same_profile_id = ensure_database_profile(profiles, "legacy-db", alias="legacy-db")

        self.assertEqual(default_profile_id, same_profile_id)
        self.assertEqual(len(profiles), 1)


if __name__ == "__main__":
    unittest.main()
