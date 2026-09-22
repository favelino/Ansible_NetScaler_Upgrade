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

    def test_device_preparation_does_not_require_remote_python(self):
        prep = (ROOT / "upgrade_prep.yaml").read_text(encoding="utf-8")
        device_play = prep.split(
            "- name: Check, upload, verify, and extract firmware on every NetScaler",
            1,
        )[1].split("- name: Write preparation reports and readiness metadata", 1)[0]
        self.assertNotIn("ansible.builtin.ping:", device_play)
        self.assertNotIn("ansible.builtin.shell:", device_play)
        self.assertNotIn("ansible.builtin.file:", device_play)
        self.assertNotIn("ansible.builtin.copy:", device_play)
        self.assertNotIn("ansible.builtin.stat:", device_play)
        self.assertGreaterEqual(device_play.count("ansible.builtin.raw:"), 8)
        self.assertNotIn("ssh_netscaler_adc ", device_play)
        self.assertIn("{{ 'df -Pk /var' | quote }}", device_play)
        self.assertGreaterEqual(device_play.count("| quote }}"), 6)
        self.assertIn("prep_space_checked: false", device_play)
        self.assertIn("if (prep_space_checked | bool)", device_play)
        self.assertIn("ANSIBLE_RAW_CONNECTION_OK", device_play)
        self.assertIn("sshpass", device_play)
        self.assertIn("- scp", device_play)
        self.assertIn("SSHPASS:", device_play)
        self.assertIn("no_log: true", device_play)
        self.assertIn("without remote Python", device_play)

    def test_prep_free_space_parser_accepts_13_1_and_attached_banner(self):
        prep = (ROOT / "upgrade_prep.yaml").read_text(encoding="utf-8")
        pattern = r"([0-9]+) +[0-9]+%"
        sample = (
            "Filesystem  1024-blocks Used Avail Capacity Mounted on\n"
            "/dev/da0s1e 14519676 3597664 9760438 27% "
            "/var########################################\n"
        )
        self.assertIn(pattern.replace("\\", "\\\\"), prep)
        self.assertEqual(re.findall(pattern, sample), ["9760438"])

    def test_prep_sha_parser_accepts_attached_login_banner(self):
        prep = (ROOT / "upgrade_prep.yaml").read_text(encoding="utf-8")
        pattern = r"(?im)^([0-9a-f]{64})(?![0-9a-f])"
        digest = "60d7c52fa7edf91e12a202ce6afe3e23ae87ed40801403f427dbceca810c7936"
        sample = digest + "########################################\n"
        self.assertEqual(prep.count(pattern), 3)
        self.assertEqual(re.findall(pattern, sample), [digest])

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
        self.assertNotIn("netscaler.adc.hanode:", playbook)
        self.assertNotIn("netscaler.adc.hasync:", playbook)
        self.assertIn("Require healthy HA controls before isolation", playbook)
        self.assertIn(
            "ssh_netscaler_adc set ha node -hasync DISABLED -haprop DISABLED",
            playbook,
        )
        self.assertIn(
            "ssh_netscaler_adc set ha node -hasync ENABLED -haprop ENABLED",
            playbook,
        )
        self.assertEqual(playbook.count("ssh_netscaler_adc save ns config"), 2)
        self.assertIn("running configuration has not changed", playbook)
        self.assertIn("Sync State", playbook)
        self.assertIn("Propagation", playbook)
        self.assertIn("Verify restored HA controls", playbook)
        self.assertIn(
            "Wait for both nodes to accept SSH before HA restoration", playbook
        )
        self.assertIn("ha_restore_connectivity.failed", playbook)
        self.assertIn("ha_restore_commands.unreachable", playbook)
        self.assertGreaterEqual(playbook.count("ignore_unreachable: true"), 4)
        self.assertGreaterEqual(playbook.count("retries: 12"), 3)
        self.assertIn("ignore_errors: true", playbook)
        self.assertNotIn("ssh_netscaler_adc nscli", playbook)
        self.assertEqual(playbook.count("ssh_netscaler_adc show ha node"), 9)
        self.assertEqual(playbook.count("ssh_netscaler_adc show ha node 0"), 3)
        self.assertEqual(
            playbook.count("ssh_netscaler_adc force ha failover -force"), 2
        )
        self.assertEqual(playbook.count("ssh_netscaler_adc force ha sync"), 1)

    def test_playbook_writes_reports_before_final_assertion(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        json_report = playbook.index("Write JSON upgrade report")
        markdown_report = playbook.index("Write Markdown upgrade report")
        final_assertion = playbook.index("Return a failing exit code")
        self.assertLess(json_report, final_assertion)
        self.assertLess(markdown_report, final_assertion)
        self.assertIn("ansible_failed_task.action", playbook)
        self.assertNotIn("ansible_failed_task.name", playbook)

    def test_node_upgrade_uses_persistent_resume_tracking(self):
        node_tasks = (ROOT / "tasks" / "upgrade_node.yml").read_text(encoding="utf-8")
        self.assertNotIn("\n      async:", node_tasks)
        self.assertIn("INSTALLNS_STAGED", node_tasks)
        self.assertIn("INSTALLNS_RUNNING", node_tasks)
        self.assertIn("node_install_status.rc | default(1)", node_tasks)
        self.assertGreaterEqual(node_tasks.count("ansible.builtin.raw:"), 10)
        self.assertEqual(node_tasks.count("ssh_netscaler_adc show ns version"), 2)
        self.assertNotIn("ssh_netscaler_adc nscli", node_tasks)
        self.assertNotIn("ssh_netscaler_adc test", node_tasks)
        self.assertNotIn("ssh_netscaler_adc sha256", node_tasks)
        self.assertIn(
            "test -f {{ prepared_firmware_remote_marker | quote }}", node_tasks
        )
        self.assertIn(
            "cd {{ (prepared_firmware_remote_installns | dirname) | quote }}",
            node_tasks,
        )
        self.assertIn("cat {{ node_install_rc | quote }}", node_tasks)
        self.assertNotIn("{{ ('cat ' ~ node_install_rc) | quote }}", node_tasks)
        self.assertNotIn("{{ ('rm -f ' ~ node_install_log", node_tasks)
        self.assertIn("without remote Python", node_tasks)
        self.assertIn("Wait for management IP to answer ping", node_tasks)
        self.assertIn("node_version_after_raw.unreachable", node_tasks)
        self.assertIn("ignore_unreachable: true", node_tasks)
        self.assertIn("nohup /bin/sh", node_tasks)
        self.assertNotIn("ansible.builtin.shell:", node_tasks)
        self.assertIn("Start installns without remote Python", node_tasks)
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
