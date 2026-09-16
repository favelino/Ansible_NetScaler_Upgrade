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
        self.assertIn("[primary_netscaler]\n10.100.48.1", output)
        self.assertIn("[secondary_netscaler]\n10.100.48.2", output)
        self.assertNotIn("password", output.lower())
        self.assertNotIn("nitro_pass", output)

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
        nodes[1]["ha_ip_address"] = "10.100.48.99"
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
