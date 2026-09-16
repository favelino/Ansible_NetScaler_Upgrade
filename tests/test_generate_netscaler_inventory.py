import json
from pathlib import Path
import tempfile
import unittest

from generate_netscaler_inventory import (
    InventoryError,
    fetch_instances,
    render_inventory,
    validate_instances,
    write_inventory,
)


FIXTURE = Path(__file__).with_name("console_response.json")


def sample_nodes():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["ns"]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def __call__(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return FakeResponse(next(self.pages))


class InventoryTests(unittest.TestCase):
    def test_valid_pair_and_inventory(self):
        pairs = validate_instances(sample_nodes())
        self.assertEqual(len(pairs), 1)
        output = render_inventory(pairs)
        self.assertIn("[netscaler_nodes]", output)
        self.assertIn("ns_192_0_2_10 ansible_host=192.0.2.10", output)
        self.assertIn("[primary_netscaler]\nns_192_0_2_10", output)
        self.assertIn("[secondary_netscaler]\nns_192_0_2_11", output)
        self.assertIn("[netscaler_ha_pairs]", output)
        self.assertIn(
            "pair_001 pair_name=\"lab-ns-primary / lab-ns-secondary\" "
            "primary_host=ns_192_0_2_10 secondary_host=ns_192_0_2_11",
            output,
        )
        self.assertNotIn("password", output.lower())
        self.assertNotIn("nitro_pass", output)

    def test_renders_multiple_pairs_as_independent_jobs(self):
        nodes = sample_nodes()
        second_pair = json.loads(json.dumps(nodes))
        second_pair[0].update(
            hostname="lab-ns-primary-2",
            ns_ip_address="192.0.2.20",
            ha_ip_address="192.0.2.21",
        )
        second_pair[1].update(
            hostname="lab-ns-secondary-2",
            ns_ip_address="192.0.2.21",
            ha_ip_address="192.0.2.20",
        )
        output = render_inventory(validate_instances(nodes + second_pair))
        self.assertIn("pair_001", output)
        self.assertIn("pair_002", output)
        self.assertEqual(output.count(" primary_host="), 2)

    def test_rejects_role_specific_sync_mismatch(self):
        nodes = sample_nodes()
        nodes[1]["ha_sync"] = "ENABLED"
        with self.assertRaisesRegex(InventoryError, "expected 'SUCCESS'"):
            validate_instances(nodes)

    def test_rejects_down_instance(self):
        nodes = sample_nodes()
        nodes[0]["instance_state"] = "Down"
        with self.assertRaisesRegex(InventoryError, "is not Up"):
            validate_instances(nodes)

    def test_rejects_missing_peer(self):
        with self.assertRaisesRegex(InventoryError, "missing HA peer"):
            validate_instances(sample_nodes()[:1])

    def test_rejects_nonreciprocal_peer(self):
        nodes = sample_nodes()
        nodes[1]["ha_ip_address"] = "192.0.2.99"
        with self.assertRaisesRegex(InventoryError, "not reciprocal"):
            validate_instances(nodes)

    def test_fetches_all_pages_and_requests_only_required_attrs(self):
        nodes = sample_nodes()
        opener = FakeOpener([{"ns": [nodes[0]]}, {"ns": [nodes[1]]}, {"ns": []}])
        result = fetch_instances(
            "https://console.example.com",
            "api-user",
            "secret",
            page_size=1,
            opener=opener,
        )
        self.assertEqual(result, nodes)
        self.assertEqual(
            [call[0].full_url.rsplit("pageno=", 1)[1] for call in opener.calls],
            ["1", "2", "3"],
        )
        self.assertNotIn("secret", opener.calls[0][0].full_url)

    def test_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.ini"
            path.write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(InventoryError, "Refusing to overwrite"):
                write_inventory(path, "replacement")
            self.assertEqual(path.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
