from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    def test_two_stage_entry_points_exist(self):
        prep = (ROOT / "upgrade_prep.yaml").read_text(encoding="utf-8")
        perform = (ROOT / "upgrade_perform.yaml").read_text(encoding="utf-8")
        self.assertIn("Generate inventory.ini containing every HA pair", prep)
        self.assertIn("import_playbook: ha_upgrade.yaml", perform)

    def test_preparation_enforces_space_checksum_and_reports(self):
        prep = (ROOT / "upgrade_prep.yaml").read_text(encoding="utf-8")
        self.assertIn("minimum_var_free_gb", prep)
        self.assertIn("checksum_algorithm: sha256", prep)
        self.assertIn("Write JSON preparation report", prep)
        self.assertIn("Write Markdown preparation report", prep)
        self.assertIn("PREPARATION FAILED", prep)

    def test_actual_upgrade_requires_preparation_gate(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        node_tasks = (ROOT / "tasks" / "upgrade_node.yml").read_text(encoding="utf-8")
        self.assertIn("Enforce successful preparation gate", playbook)
        self.assertIn("prepared_inventory_sha256", playbook)
        self.assertIn("prepared_firmware_remote_marker", node_tasks)

    def test_playbook_limits_parallelism_by_pair(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        self.assertIn("hosts: netscaler_ha_pairs", playbook)
        self.assertIn('serial: "{{ ha_pairs_parallel | int }}"', playbook)
        self.assertIn("STEP 12/12", playbook)
        self.assertNotIn("ansible.builtin.shell: >-", playbook)

    def test_playbook_isolates_and_restores_ha_controls(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        disable = playbook.index("Disable HA sync and propagation")
        upgrade_secondary = playbook.index("Upgrade original Secondary")
        restore = playbook.index("Restore HA synchronization and command propagation")
        report = playbook.index("Build pair report record")
        self.assertLess(disable, upgrade_secondary)
        self.assertLess(restore, report)
        self.assertEqual(playbook.count("netscaler.adc.hanode:"), 2)
        self.assertIn("hasync: DISABLED", playbook)
        self.assertIn("haprop: DISABLED", playbook)
        self.assertIn("hasync: ENABLED", playbook)
        self.assertIn("haprop: ENABLED", playbook)
        self.assertIn("save_config: true", playbook)
        self.assertIn("netscaler.adc.hasync:", playbook)
        self.assertIn('save: "YES"', playbook)
        self.assertIn("ignore_errors: true", playbook)

    def test_playbook_writes_reports_before_final_assertion(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        json_report = playbook.index("Write JSON upgrade report")
        markdown_report = playbook.index("Write Markdown upgrade report")
        final_assertion = playbook.index("Return a failing exit code")
        self.assertLess(json_report, final_assertion)
        self.assertLess(markdown_report, final_assertion)

    def test_node_upgrade_uses_persistent_resume_tracking(self):
        node_tasks = (ROOT / "tasks" / "upgrade_node.yml").read_text(encoding="utf-8")
        self.assertNotIn("\n      async:", node_tasks)
        self.assertIn("INSTALLNS_STAGED", node_tasks)
        self.assertIn("INSTALLNS_RUNNING", node_tasks)
        self.assertIn("node_install_status.rc | default(1)", node_tasks)
        self.assertGreaterEqual(node_tasks.count("ansible.builtin.raw:"), 10)
        self.assertIn("without remote Python", node_tasks)
        self.assertIn("Wait for management IP to answer ping", node_tasks)
        self.assertIn("nohup /bin/sh", node_tasks)
        self.assertIn("/var/nsinstall/installns_state", node_tasks)
        self.assertEqual(node_tasks.count("regex_findall"), 2)
        self.assertEqual(node_tasks.count("show ns version"), 2)
        version_pattern = r"(?im)NS([0-9]+(?:[.][0-9]+)*): Build ([0-9]+(?:[.][0-9]+)*)"
        self.assertEqual(node_tasks.count(version_pattern), 2)
        sample = "        NetScaler NS14.1: Build 66.54.nc, Date: Feb 24 2026"
        self.assertEqual(re.findall(version_pattern, sample), [("14.1", "66.54")])

    def test_secret_bearing_commands_are_hidden(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        node_tasks = (ROOT / "tasks" / "upgrade_node.yml").read_text(encoding="utf-8")
        self.assertNotIn("nitro_pass=", playbook)
        self.assertGreaterEqual(playbook.count("no_log: true"), 4)
        self.assertGreaterEqual(node_tasks.count("no_log: true"), 3)


if __name__ == "__main__":
    unittest.main()
