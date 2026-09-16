#!/usr/bin/env python3
"""Generate an Ansible inventory from NetScaler Console HA state."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import ssl


ATTRS = (
    "hostname",
    "ns_ip_address",
    "ha_ip_address",
    "ha_master_state",
    "ha_sync",
    "instance_state",
    "is_ha_configured",
)
EXPECTED_SYNC = {"primary": "ENABLED", "secondary": "SUCCESS"}


class InventoryError(RuntimeError):
    """Raised when Console data is unsafe or unsuitable for an HA upgrade."""


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def extract_instances(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("ns"), list):
        raise InventoryError("Console response must contain an 'ns' array")
    if any(not isinstance(item, dict) for item in payload["ns"]):
        raise InventoryError("Every entry in the 'ns' array must be an object")
    return payload["ns"]


def fetch_instances(
    console_url: str,
    username: str,
    password: str,
    *,
    verify: bool | str = True,
    page_size: int = 100,
    timeout: float = 30,
    opener: Any = None,
) -> list[dict[str, Any]]:
    """Read every /nitro/v2/config/ns page from NetScaler Console."""
    endpoint = console_url.rstrip("/") + "/nitro/v2/config/ns"
    headers = {
        "Accept": "application/json",
        "X-NITRO-USER": username,
        "X-NITRO-PASS": password,
    }
    instances: list[dict[str, Any]] = []
    seen_ips: set[str] = set()

    for page_number in range(1, 10001):
        params = {
            "attrs": ",".join(ATTRS),
            "pagesize": page_size,
            "pageno": page_number,
        }
        request = Request(endpoint + "?" + urlencode(params), headers=headers)
        if verify is False:
            context = ssl._create_unverified_context()
        elif isinstance(verify, str):
            context = ssl.create_default_context(cafile=verify)
        else:
            context = ssl.create_default_context()
        open_request = opener or urlopen
        with open_request(request, timeout=timeout, context=context) as response:
            page = extract_instances(json.loads(response.read().decode("utf-8")))

        for item in page:
            nsip = _text(item.get("ns_ip_address"))
            if nsip and nsip in seen_ips:
                raise InventoryError(
                    f"Console returned duplicate ns_ip_address {nsip!r} across pages"
                )
            if nsip:
                seen_ips.add(nsip)
            instances.append(item)

        if len(page) < page_size:
            return instances

    raise InventoryError("Console pagination exceeded 10,000 pages")


def validate_instances(
    instances: Iterable[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Validate HA health and return sorted (Primary, Secondary) pairs."""
    nodes = list(instances)
    if not nodes:
        raise InventoryError("Console returned no NetScaler instances")

    by_ip: dict[str, dict[str, Any]] = {}
    for node in nodes:
        missing = [name for name in ATTRS if not _text(node.get(name))]
        if missing:
            raise InventoryError(
                f"Instance {_text(node.get('hostname')) or '<unknown>'} is missing: "
                + ", ".join(missing)
            )

        hostname = _text(node["hostname"])
        nsip = _text(node["ns_ip_address"])
        role = _text(node["ha_master_state"]).lower()
        sync = _text(node["ha_sync"]).upper()

        if nsip in by_ip:
            raise InventoryError(f"Duplicate ns_ip_address in Console response: {nsip}")
        if _text(node["instance_state"]).lower() != "up":
            raise InventoryError(f"{hostname} ({nsip}) is not Up")
        if _text(node["is_ha_configured"]).lower() not in {"true", "yes", "1"}:
            raise InventoryError(f"{hostname} ({nsip}) is not configured for HA")
        if role not in EXPECTED_SYNC:
            raise InventoryError(f"{hostname} ({nsip}) has invalid HA role {node['ha_master_state']!r}")
        if sync != EXPECTED_SYNC[role]:
            raise InventoryError(
                f"{hostname} ({nsip}) has ha_sync={sync!r}; expected "
                f"{EXPECTED_SYNC[role]!r} for {role.title()}"
            )
        by_ip[nsip] = node

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    visited: set[str] = set()
    for nsip in sorted(by_ip):
        if nsip in visited:
            continue
        node = by_ip[nsip]
        peer_ip = _text(node["ha_ip_address"])
        peer = by_ip.get(peer_ip)
        if peer is None:
            raise InventoryError(f"{node['hostname']} ({nsip}) references missing HA peer {peer_ip}")
        if peer_ip == nsip:
            raise InventoryError(f"{node['hostname']} ({nsip}) references itself as its HA peer")
        if _text(peer["ha_ip_address"]) != nsip:
            raise InventoryError(
                f"HA peer relationship is not reciprocal: {nsip} -> {peer_ip}, "
                f"but {peer_ip} -> {_text(peer['ha_ip_address'])}"
            )

        pair = [node, peer]
        primaries = [n for n in pair if _text(n["ha_master_state"]).lower() == "primary"]
        secondaries = [n for n in pair if _text(n["ha_master_state"]).lower() == "secondary"]
        if len(primaries) != 1 or len(secondaries) != 1:
            raise InventoryError(
                f"HA pair {nsip}/{peer_ip} must contain exactly one Primary and one Secondary"
            )
        pairs.append((primaries[0], secondaries[0]))
        visited.update({nsip, peer_ip})

    if visited != set(by_ip):
        raise InventoryError("One or more instances could not be assigned to an HA pair")
    return sorted(pairs, key=lambda pair: _text(pair[0]["ns_ip_address"]))


def render_inventory(pairs: Iterable[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    pairs = list(pairs)
    lines = [
        "# Generated from NetScaler Console. Do not add credentials here.",
        "[primary_netscaler]",
    ]
    for primary, _ in pairs:
        ip = _text(primary["ns_ip_address"])
        lines.append(f"{ip} nsip={ip} validate_certs=no")

    lines.extend(["", "[secondary_netscaler]"])
    for _, secondary in pairs:
        ip = _text(secondary["ns_ip_address"])
        lines.append(f"{ip} nsip={ip} validate_certs=no")
    return "\n".join(lines) + "\n"


def write_inventory(path: Path, content: str, *, force: bool = False) -> None:
    path = path.expanduser()
    if path.exists() and not force:
        raise InventoryError(f"Refusing to overwrite existing file: {path} (use --force)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--console-url", help="NetScaler Console base URL")
    source.add_argument("--input-json", type=Path, help="Read a saved Console response")
    parser.add_argument("--console-user", default=os.getenv("NETSCALER_CONSOLE_USER"))
    parser.add_argument("--console-pass", default=os.getenv("NETSCALER_CONSOLE_PASS"))
    parser.add_argument("--ca-bundle", help="PEM CA bundle used to verify Console TLS")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification (lab only)")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path, default=Path("inventory.ini"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.page_size < 1:
        parser.error("--page-size must be greater than zero")
    if args.ca_bundle and args.insecure:
        parser.error("--ca-bundle and --insecure cannot be used together")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.input_json:
            with args.input_json.open(encoding="utf-8") as handle:
                instances = extract_instances(json.load(handle))
        else:
            parsed = urlparse(args.console_url)
            if parsed.scheme not in {"https", "http"} or not parsed.netloc:
                raise InventoryError("--console-url must be an absolute HTTP(S) URL")
            if parsed.scheme != "https" and not args.insecure:
                raise InventoryError("HTTP Console URLs require --insecure; HTTPS is recommended")
            username = args.console_user or input("NetScaler Console username: ").strip()
            if not username:
                raise InventoryError("Console username cannot be empty")
            password = args.console_pass or getpass.getpass("NetScaler Console password: ")
            if not password:
                raise InventoryError("Console password cannot be empty")
            verify: bool | str = False if args.insecure else (args.ca_bundle or True)
            instances = fetch_instances(
                args.console_url,
                username,
                password,
                verify=verify,
                page_size=args.page_size,
                timeout=args.timeout,
            )

        pairs = validate_instances(instances)
        write_inventory(args.output, render_inventory(pairs), force=args.force)
        print(f"Wrote {args.output} with {len(pairs)} healthy HA pair(s).")
        return 0
    except (InventoryError, json.JSONDecodeError, OSError, HTTPError, URLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
