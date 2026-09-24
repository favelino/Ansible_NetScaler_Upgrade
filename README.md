# NetScaler HA fleet upgrade

This repository automates firmware preparation and ordered upgrades for
two-node NetScaler HA pairs discovered from NetScaler Console. It is intended
for large fleets and supports configurable concurrency, progress messages,
restart-aware installation, safety gates, and JSON/Markdown reports.

> Read [HELP.md](HELP.md) before operating the workflow and keep
> [TROUBLESHOOTING.md](TROUBLESHOOTING.md) available during the change.

## Important scope and limitations

- This workflow supports normal two-node HA pairs only.
- Standalone appliances are detected, reported, and skipped. They are not added
  to `inventory.ini` and must be upgraded with a separate standalone procedure.
- Malformed HA topology, such as a missing or nonreciprocal peer, stops inventory
  generation.
- The current code does **not** create a NetScaler system backup, configuration
  export, hypervisor snapshot, or rollback point.
- `save ns config` is used only to persist temporary HA-control changes. It is
  not a backup.
- The operator is responsible for backups, application validation, maintenance
  windows, rollback planning, and compliance with NetScaler release guidance.

See [DISCLAIMER.md](DISCLAIMER.md) for the best-effort, no-warranty, and
no-support terms.

## Workflow

| Stage | Entry point | What it does |
|---|---|---|
| Prepare | `upgrade_prep.yaml` | Discovers HA pairs, writes `inventory.ini`, checks SSH and `/var`, uploads and verifies firmware, extracts it, and creates the preparation gate. |
| Upgrade | `upgrade_perform.yaml` | Imports `ha_upgrade.yaml` and upgrades complete HA pairs with configurable pair-level concurrency. |
| Node tasks | `tasks/upgrade_node.yml` | Runs native `installns`, requests one reboot, waits for SSH/CLI, and validates the running version. |

Stage 2 requires a successful Stage 1 result, an unchanged inventory SHA-256,
and the same target version that was prepared.

## Requirements

- Linux Ansible controller.
- Ansible Core 2.16 or newer.
- `netscaler.adc` collection 2.17.0 or newer.
- `sshpass` for the password-authenticated SCP used by Stage 1.
- Controller access to NetScaler Console and every NSIP on TCP/22 and the
  configured NITRO protocol.
- Exactly one supported `.tgz` or `.tar.gz` firmware archive.
- Healthy two-node HA pairs with known Primary and Secondary roles.

Ubuntu example:

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip sshpass

ansible --version
ansible-galaxy collection install 'netscaler.adc:>=2.17.0' --force
ansible-galaxy collection list netscaler.adc
```

Use the same Linux account for `ansible`, `ansible-playbook`, and
`ansible-galaxy` so collection and configuration paths remain consistent.

## Credentials

Create an encrypted Vault:

```bash
mkdir -p group_vars/all
ansible-vault create group_vars/all/vault.yml
```

Vault content:

```yaml
---
vault_nitro_user: nsroot
vault_nitro_pass: "replace-with-the-NetScaler-password"
vault_console_user: automation-api
vault_console_pass: "replace-with-the-Console-password"
```

Useful commands:

```bash
ansible-vault view group_vars/all/vault.yml
ansible-vault edit group_vars/all/vault.yml
ansible-vault rekey group_vars/all/vault.yml
```

Do not commit plaintext credentials, generated inventory, reports, preparation
metadata, firmware archives, or a Vault password file.

## Configuration

Edit `variables.yaml` before preparation:

```yaml
netscaler_target_version: "14.1-73.33"
netscaler_console_url: "https://console.example.com"
netscaler_console_ca_bundle: "/path/to/console-ca.pem"
netscaler_console_insecure: false

firmware_local_directory: "new_firmware"
firmware_remote_directory: "/var/nsinstall"
minimum_var_free_gb: 5
prep_devices_parallel: 20
ha_pairs_parallel: 2
```

`prep_devices_parallel` counts individual appliances.
`ha_pairs_parallel` counts complete HA pairs. The two nodes in one pair are
never installed or rebooted simultaneously.

The repository currently disables SSH host-key checking and defaults NITRO
certificate validation to false. Those defaults are convenient for a lab but
must be reviewed and hardened before production use.

## Firmware

Place exactly one archive in `new_firmware/`:

```bash
find new_firmware -maxdepth 1 -type f \
  \( -name '*.tgz' -o -name '*.tar.gz' \) -print

sha256sum new_firmware/*
tar -tzf new_firmware/*.tgz | grep -E '(^|/)installns$'
```

Stage 1 requires:

- One firmware archive.
- One `installns` entry.
- A filename matching `build-<target>_*.tgz`.
- An internal `ns-<target>.gz` image.

## Stage 1: prepare

```bash
ansible-playbook upgrade_prep.yaml --syntax-check

ansible-playbook upgrade_prep.yaml \
  --ask-vault-pass \
  -e prep_devices_parallel=20
```

The syntax check may warn that `netscaler_nodes` does not exist because the
dynamic groups are created only when the first play runs.

Preparation performs:

1. Console discovery and reciprocal HA-pair validation.
2. TCP/22 and authenticated raw-SSH checks.
3. `/var` free-space validation with a minimum of 5 GiB.
4. Creation of `/var/nsinstall` when needed.
5. Reuse of an existing archive when its SHA-256 matches.
6. Controller-side `scp -O` upload when required.
7. Remote SHA-256 verification and extraction.
8. `installns` path verification and creation of a readiness marker.

Preparation does not run `installns`, reboot an appliance, or create a backup.

Outputs:

- `inventory.ini`
- `prepared_firmware.yml`
- `reports/prep-<UTC-ID>.json`
- `reports/prep-<UTC-ID>.md`

## Standalone appliances

Console instances explicitly reported with `is_ha_configured` set to false are
written to the preparation report under `Standalone/non-HA Console instances
skipped`. A missing `is_ha_configured` attribute is treated as invalid Console
data and stops discovery instead of silently skipping the appliance.

For each skipped appliance, the operator must:

1. Decide whether it is intentionally standalone.
2. If it should be HA, correct its HA/Console state and rerun Stage 1.
3. If it is intentionally standalone, schedule it under a separate standalone
   upgrade procedure, with its own outage, backup, rollback, and validation
   plan.
4. Never add it to `netscaler_ha_pairs` or invent a peer to make this HA
   workflow accept it.

List skipped standalone appliances from the latest report:

```bash
LATEST_PREP_JSON="$(ls -t reports/prep-*.json | head -n 1)"
jq '.skipped_non_ha_instances' "$LATEST_PREP_JSON"
```

Without `jq`:

```bash
python3 -m json.tool "$LATEST_PREP_JSON" | less
```

Standalone appliances do not count toward `prepared_device_count` or
`expected_device_count`; those fields describe only validated HA nodes.

## Stage 2: perform the HA upgrade

Review the Stage 1 report and gate first:

```bash
less "$(ls -t reports/prep-*.md | head -n 1)"

grep -E \
  '^(prepared_firmware_successful|prepared_target_version|prepared_device_count|expected_device_count|prepared_at):' \
  prepared_firmware.yml
```

Then run:

```bash
ansible-playbook upgrade_perform.yaml \
  -i inventory.ini \
  --ask-vault-pass \
  -e ha_pairs_parallel=2
```

For each pair, Stage 2:

1. Verifies fleet reachability, local HA roles, node state, and equal starting
   versions.
2. Disables and saves `haSync` and `haProp` on both nodes.
3. Reads the original Secondary version.
4. Skips `installns` and reboot if that node already runs the target.
5. Otherwise, reuses a previously staged target when detected or runs native
   `./installns -Y -n`, requests one reboot, and validates the final version.
6. Requires both nodes to be `UP`, then fails over to the upgraded Secondary.
7. Applies the same node logic to the original Primary.
8. Fails back and confirms the original Primary/Secondary roles.
9. Restores and saves `haSync` and `haProp` only when live versions match and
   the original roles are confirmed.
10. Forces final HA synchronization from the restored original Primary.

If the Secondary fails, the Primary is not upgraded. Other scheduled pairs can
continue. Mixed or unknown versions remain isolated and are reported as
`MANUAL_RECOVERY_REQUIRED`.

If only one node already runs the target and its peer runs an older build, the
pair fails the equal-starting-version gate before isolation. If both nodes
already run the target, node installation and reboot are skipped, but the
current pair workflow still performs HA isolation, verified failover/failback,
HA-control restoration, and final synchronization.

## Reports

Latest preparation report:

```bash
LATEST_PREP_MD="$(ls -t reports/prep-*.md | head -n 1)"
echo "$LATEST_PREP_MD"
less "$LATEST_PREP_MD"
```

Latest upgrade report:

```bash
LATEST_UPGRADE_MD="$(ls -t reports/upgrade-*.md | head -n 1)"
echo "$LATEST_UPGRADE_MD"
less "$LATEST_UPGRADE_MD"
```

Show only upgrade failures:

```bash
grep -nEi -A10 -B3 'FAILED|MANUAL_RECOVERY|Failures|error' \
  "$LATEST_UPGRADE_MD"
```

Pretty-print the latest JSON report:

```bash
LATEST_UPGRADE_JSON="$(ls -t reports/upgrade-*.json | head -n 1)"
python3 -m json.tool "$LATEST_UPGRADE_JSON" | less
```

With `jq`:

```bash
jq '.results[] | {pair,status,primary_before,primary_after,secondary_before,secondary_after,error}' \
  "$LATEST_UPGRADE_JSON"
```

Reports are written before the final failing exit code. A failed final Ansible
assertion does not mean the report was lost.

## Validation and tests

```bash
python3 -m unittest -v
ansible-playbook upgrade_prep.yaml --syntax-check
ansible-playbook upgrade_perform.yaml \
  -i inventory.ini.example \
  --syntax-check
```

For the complete operator sequence, see [HELP.md](HELP.md). For process,
install, reboot, HA-state, report, and recovery diagnostics, see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Disclaimer

This project is provided on a best-effort, as-is basis without warranties or
guarantees. Use is entirely at the operator's risk. The authors and contributors
accept no responsibility for outages, failed upgrades, configuration loss, data
loss, security impact, or other damages. There is no commitment or obligation
to provide support, maintenance, future upgrades, compatibility updates, or bug
fixes. See [DISCLAIMER.md](DISCLAIMER.md).
