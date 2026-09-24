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
        self.assertIn("prepared_target_version", playbook)
        self.assertIn("prepared_firmware_remote_installns", node_tasks)

    def test_preparation_binds_filename_image_and_target_version(self):
        prep = (ROOT / "upgrade_prep.yaml").read_text(encoding="utf-8")
        example = (ROOT / "prepared_firmware.yml.example").read_text(encoding="utf-8")
        self.assertIn("prep_filename_version_matches", prep)
        self.assertIn("prep_target_image_matches", prep)
        self.assertIn("prepared_target_version", prep)
        self.assertIn("prepared_target_version", example)

    def test_playbook_limits_parallelism_by_pair(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        self.assertIn("hosts: netscaler_ha_pairs", playbook)
        self.assertIn('serial: "{{ ha_pairs_parallel | int }}"', playbook)
        self.assertIn("STEP 12/12", playbook)
        self.assertNotIn("ansible.builtin.shell: >-", playbook)

    def test_playbook_isolates_and_restores_ha_controls(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        initialization_play, upgrade_play = playbook.split(
            "- name: Upgrade NetScaler HA pairs", 1
        )
        for regex_var in (
            "ha_role_primary_regex",
            "ha_role_secondary_regex",
            "ha_node_up_regex",
        ):
            self.assertNotIn(regex_var, initialization_play)
            self.assertIn(regex_var, upgrade_play)
        disable = playbook.index("Disable HA sync and propagation")
        upgrade_secondary = playbook.index("Upgrade original Secondary")
        restore = playbook.index("Restore HA synchronization and command propagation")
        report = playbook.index("Build pair report record")
        matching_versions = playbook.index("Require matching versions before HA reconciliation")
        normalize = playbook.index("Normalize HA sync and propagation before isolation")
        baseline_sync = playbook.index("Force a clean synchronization before isolation")
        isolation_started = playbook.index("Mark HA isolation as started")
        self.assertLess(matching_versions, normalize)
        self.assertLess(normalize, baseline_sync)
        self.assertLess(baseline_sync, isolation_started)
        self.assertLess(isolation_started, disable)
        self.assertLess(disable, upgrade_secondary)
        self.assertLess(restore, report)
        self.assertNotIn("netscaler.adc.hanode:", playbook)
        self.assertNotIn("netscaler.adc.hasync:", playbook)
        self.assertIn("Require healthy HA controls before isolation after reconciliation", playbook)
        self.assertIn("Require matching versions before HA reconciliation", playbook)
        self.assertIn("Normalize HA sync and propagation before isolation", playbook)
        self.assertIn("Force a clean synchronization before isolation", playbook)
        self.assertIn(
            "ssh_netscaler_adc set ha node -hasync DISABLED -haprop DISABLED",
            playbook,
        )
        self.assertIn(
            "ssh_netscaler_adc set ha node -hasync ENABLED -haprop ENABLED",
            playbook,
        )
        self.assertEqual(playbook.count("ssh_netscaler_adc save ns config"), 3)
        self.assertIn("running configuration has not changed", playbook)
        self.assertIn("Sync State", playbook)
        self.assertIn("Propagation", playbook)
        self.assertIn("Verify restored HA controls", playbook)
        self.assertIn(
            "Permit HA restoration only with matching versions and original roles",
            playbook,
        )
        self.assertIn("MANUAL_RECOVERY_REQUIRED", playbook)
        self.assertIn("recovery_primary_version == recovery_secondary_version", playbook)
        self.assertIn("ha_restore_commands.unreachable", playbook)
        self.assertGreaterEqual(playbook.count("ignore_unreachable: true"), 4)
        self.assertIn("ignore_errors: true", playbook)
        self.assertNotIn("ssh_netscaler_adc nscli", playbook)
        self.assertNotRegex(playbook, r"ssh_netscaler_adc show ha node\s*(?:\n|$)(?!\s*0)")
        self.assertGreaterEqual(playbook.count("ssh_netscaler_adc show ha node 0"), 9)
        self.assertEqual(
            playbook.count("ssh_netscaler_adc force ha failover -force"), 3
        )
        self.assertIn("Restore original roles when both upgraded nodes are inverted", playbook)
        self.assertEqual(playbook.count("ssh_netscaler_adc force ha sync"), 2)

    def test_playbook_writes_reports_before_final_assertion(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        json_report = playbook.index("Write JSON upgrade report")
        markdown_report = playbook.index("Write Markdown upgrade report")
        final_assertion = playbook.index("Return a failing exit code")
        self.assertLess(json_report, final_assertion)
        self.assertLess(markdown_report, final_assertion)
        self.assertIn("ansible_failed_task.action", playbook)
        self.assertNotIn("ansible_failed_task.name", playbook)

    def test_node_upgrade_uses_simple_synchronous_installns(self):
        node_tasks = (ROOT / "tasks" / "upgrade_node.yml").read_text(encoding="utf-8")
        self.assertNotIn("{{ upgrade_run_id }}", node_tasks)
        self.assertEqual(node_tasks.count("hostvars['localhost'].upgrade_run_id"), 4)
        self.assertNotIn("async:", node_tasks)
        self.assertNotIn("nohup", node_tasks)
        self.assertNotIn("pgrep", node_tasks)
        self.assertNotIn(".pid", node_tasks)
        self.assertNotIn(".ansible-installns-", node_tasks)
        self.assertNotIn("INSTALLNS_RC", node_tasks)
        self.assertNotIn(".log", node_tasks)
        self.assertNotIn("/var/tmp", node_tasks)
        self.assertIn("./installns -Y -n", node_tasks)
        self.assertIn("register: node_install_result", node_tasks)
        self.assertIn("node_install_result.rc | default(0) == 0", node_tasks)
        self.assertIn("TARGET_ALREADY_STAGED", node_tasks)
        self.assertIn("TARGET_STAGED_OK", node_tasks)
        self.assertGreaterEqual(node_tasks.count("ansible.builtin.raw:"), 5)
        self.assertEqual(node_tasks.count("ssh_netscaler_adc show ns version"), 2)
        self.assertNotIn("PREPARED_INSTALLER_OK", node_tasks)
        self.assertNotIn("PREPARED_INSTALLER_MISSING", node_tasks)
        self.assertIn(
            "(prepared_firmware_remote_installns | dirname) | quote", node_tasks
        )
        self.assertNotIn("ansible.builtin.shell:", node_tasks)
        self.assertIn("failed_when: false", node_tasks)
        self.assertIn("No second reboot was sent", node_tasks)
        self.assertIn("post_reboot_stabilization_seconds | default(180)", node_tasks)
        self.assertIn("[reboot_up_timeout | int, 1200] | max", node_tasks)
        self.assertIn("[post_reboot_cli_timeout | int, 600] | max", node_tasks)
        ssh_return = node_tasks.index("Wait for SSH after reboot")
        stabilization = node_tasks.index("Allow NetScaler services to stabilize")
        cli_version = node_tasks.index("Read final {{ upgrade_role_label }} version")
        self.assertLess(ssh_return, stabilization)
        self.assertLess(stabilization, cli_version)
        self.assertIn("node_version_after_raw.unreachable", node_tasks)
        self.assertIn("ignore_unreachable: true", node_tasks)
        self.assertIn("/var/nsinstall/installns_state", node_tasks)
        self.assertIn("/flash/boot/loader.conf", node_tasks)
        self.assertIn("Preserve installns result on the Ansible controller", node_tasks)
        self.assertIn("Preserve post-reboot result on the Ansible controller", node_tasks)
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
        self.assertIn("X-NITRO-PASS", node_tasks)
        reboot_section = node_tasks.split("Request one reboot", 1)[1].split(
            "Confirm {{ upgrade_role_label }} actually started rebooting", 1
        )[0]
        self.assertIn("no_log: true", reboot_section)

    def test_local_ha_role_fixtures_do_not_confuse_combined_output(self):
        fixtures = ROOT / "tests" / "fixtures"
        primary = (fixtures / "show_ha_node_0_primary.txt").read_text()
        secondary = (fixtures / "show_ha_node_0_secondary.txt").read_text()
        combined = (fixtures / "show_ha_node_all.txt").read_text()
        primary_pattern = r"(?im)^\s*Master State\s*:\s*Primary\s*$"
        secondary_pattern = r"(?im)^\s*Master State\s*:\s*Secondary\s*$"
        self.assertRegex(primary, primary_pattern)
        self.assertNotRegex(primary, secondary_pattern)
        self.assertRegex(secondary, secondary_pattern)
        self.assertNotRegex(secondary, primary_pattern)
        self.assertRegex(combined, primary_pattern)
        self.assertRegex(combined, secondary_pattern)

    def test_secondary_failure_blocks_primary_upgrade(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        upgrade_secondary = playbook.index("Upgrade original Secondary")
        require_health = playbook.index("Require both nodes UP before failover")
        upgrade_primary = playbook.index("Upgrade original Primary")
        self.assertLess(upgrade_secondary, require_health)
        self.assertLess(require_health, upgrade_primary)


class TemplateBackslashRegressionTests(unittest.TestCase):
    """Reject backslashes in Jinja literals used by templated task values."""

    CONDITIONAL_KEYS = {"when", "that", "until", "failed_when", "changed_when"}

    def _offenders(self, relative):
        import yaml

        offenders = []

        def walk(node, key=None, task=None):
            if isinstance(node, dict):
                if "name" in node and any(k.startswith("ansible.") for k in node):
                    task = node["name"]
                for child_key, value in node.items():
                    inherited_key = key if key in self.CONDITIONAL_KEYS else child_key
                    walk(value, inherited_key, task)
            elif isinstance(node, list):
                for value in node:
                    walk(value, key, task)
            elif (
                isinstance(node, str)
                and "{{" in node
                and "\\" in node
                and key not in self.CONDITIONAL_KEYS
            ):
                offenders.append(f"{relative}: {task} ({key})")

        walk(yaml.safe_load((ROOT / relative).read_text(encoding="utf-8")))
        return offenders

    def test_no_backslashes_in_templated_non_conditional_values(self):
        offenders = []
        for relative in (
            "ha_upgrade.yaml",
            "tasks/upgrade_node.yml",
            "upgrade_prep.yaml",
        ):
            offenders.extend(self._offenders(relative))
        self.assertEqual(offenders, [])

    def test_raw_shell_commands_are_not_wrapped_in_outer_quote_filter(self):
        import yaml

        tasks = yaml.safe_load(
            (ROOT / "tasks" / "upgrade_node.yml").read_text(encoding="utf-8")
        )
        offenders = []

        def walk(node, task=None):
            if isinstance(node, dict):
                task = node.get("name", task)
                raw_value = node.get("ansible.builtin.raw")
                if isinstance(raw_value, str):
                    normalized = raw_value.strip()
                    if normalized.startswith("{{") and re.search(
                        r"[|]\s*quote\s*}}$", normalized, re.S
                    ):
                        offenders.append(task)
                for value in node.values():
                    walk(value, task)
            elif isinstance(node, list):
                for value in node:
                    walk(value, task)

        walk(tasks)
        self.assertEqual(offenders, [])

    def test_role_regex_variables_match_real_output(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        primary = re.search(
            r"^\s*ha_role_primary_regex: '(.+)'$", playbook, re.M
        ).group(1)
        secondary = re.search(
            r"^\s*ha_role_secondary_regex: '(.+)'$", playbook, re.M
        ).group(1)
        fixtures = ROOT / "tests" / "fixtures"
        primary_out = (fixtures / "show_ha_node_0_primary.txt").read_text(
            encoding="utf-8"
        )
        secondary_out = (fixtures / "show_ha_node_0_secondary.txt").read_text(
            encoding="utf-8"
        )
        self.assertRegex(primary_out, primary)
        self.assertNotRegex(primary_out, secondary)
        self.assertRegex(secondary_out, secondary)
        self.assertNotRegex(secondary_out, primary)

    def test_boot_loader_needle_has_no_escaped_quote(self):
        node = (ROOT / "tasks" / "upgrade_node.yml").read_text(encoding="utf-8")
        self.assertNotIn('\\"', node)
        self.assertEqual(node.count("boot_kernel_needle | quote"), 2)


if __name__ == "__main__":
    unittest.main()
