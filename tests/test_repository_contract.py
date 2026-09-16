from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    def test_playbook_limits_parallelism_by_pair(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        self.assertIn("hosts: netscaler_ha_pairs", playbook)
        self.assertIn('serial: "{{ ha_pairs_parallel | int }}"', playbook)
        self.assertIn("STEP 12/12", playbook)

    def test_playbook_writes_reports_before_final_assertion(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        json_report = playbook.index("Write JSON upgrade report")
        markdown_report = playbook.index("Write Markdown upgrade report")
        final_assertion = playbook.index("Return a failing exit code")
        self.assertLess(json_report, final_assertion)
        self.assertLess(markdown_report, final_assertion)

    def test_secret_bearing_commands_are_hidden(self):
        playbook = (ROOT / "ha_upgrade.yaml").read_text(encoding="utf-8")
        node_tasks = (ROOT / "tasks" / "upgrade_node.yml").read_text(encoding="utf-8")
        self.assertNotIn("nitro_pass=", playbook)
        self.assertGreaterEqual(playbook.count("no_log: true"), 4)
        self.assertGreaterEqual(node_tasks.count("no_log: true"), 3)


if __name__ == "__main__":
    unittest.main()

