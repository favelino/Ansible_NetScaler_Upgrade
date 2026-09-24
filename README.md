# NetScaler HA fleet upgrade

This repository automates staged upgrades for large fleets of NetScaler HA pairs
discovered from NetScaler Console. It is based on the upstream NetScaler
Automation Toolkit normal-mode HA upgrade and adds dynamic inventory,
configurable concurrency, progress messages, preparation gates, resumable
installation tracking, and JSON/Markdown reports.

> Read [HELP.md](HELP.md) before the first production run. It contains the
> complete command-by-command operating and recovery runbook.

## Workflow

| Stage | Entry point | Purpose |
|---|---|---|
| 1 — Prepare | `upgrade_prep.yaml` | Discover all HA pairs, generate `inventory.ini`, check SSH and `/var`, upload firmware, verify SHA-256, extract it, and create the preparation gate. |
| 2 — Upgrade | `upgrade_perform.yaml` | Upgrade complete HA pairs concurrently while keeping the two nodes inside each pair strictly ordered. |
| Stage 2 implementation | `ha_upgrade.yaml` | Maintained underlying HA workflow imported by `upgrade_perform.yaml`. |

Stage 2 cannot start successfully unless Stage 1 prepared every discovered HA
appliance, the generated inventory still matches its recorded SHA-256, and the
prepared target version still equals `netscaler_target_version`. Standalone
Console instances are reported and skipped; malformed HA topology is rejected.

## Requirements

- Ansible controller running Linux.
- Ansible Core 2.16 or newer.
- `netscaler.adc` collection 2.17.0 or newer.
- `sshpass` for password-authenticated SCP.
- Controller connectivity to NetScaler Console and every NSIP on TCP/22 and the
  configured NITRO protocol.
- One supported NetScaler firmware archive.
- Working HA pairs with known Primary and Secondary roles.

Ubuntu installation:

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip sshpass

ansible --version
ansible-galaxy collection install 'netscaler.adc:>=2.17.0' --force
ansible-galaxy collection list netscaler.adc
```

Using Ubuntu's packaged Ansible is supported when it meets the version
requirement. A virtual environment is optional:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install 'ansible-core>=2.16'
ansible-galaxy collection install 'netscaler.adc:>=2.17.0'
```

Always run `ansible`, `ansible-playbook`, and `ansible-galaxy` as the same
Linux user so that they use the same configuration and collection paths.

## Secure credentials

Create the encrypted Vault interactively:

```bash
mkdir -p group_vars/all
ansible-vault create group_vars/all/vault.yml
```

Enter only YAML content in the editor:

```yaml
---
vault_nitro_user: nsroot
vault_nitro_pass: "replace-with-the-real-NetScaler-password"
vault_console_user: automation-api
vault_console_pass: "replace-with-the-real-Console-password"
```

Useful Vault commands:

```bash
ansible-vault view group_vars/all/vault.yml
ansible-vault edit group_vars/all/vault.yml
ansible-vault rekey group_vars/all/vault.yml
```

Never commit plaintext credentials, put them in `inventory.ini`, pass them with
`-e`, or save the Vault password inside this repository. Vault files,
generated inventory, reports, preparation metadata, and firmware archives are
excluded from Git.

## Configuration

Edit `variables.yaml`. Example for a self-signed lab Console:

```yaml
netscaler_target_version: "14.1-73.33"
netscaler_console_url: "https://10.100.71.200"
netscaler_console_ca_bundle: ""
netscaler_console_insecure: true

prep_devices_parallel: 2
ha_pairs_parallel: 2
minimum_var_free_gb: 5

reboot_ping_check_enabled: false
reboot_poll_interval: 5
reboot_down_timeout: 300
reboot_up_timeout: 1200
post_reboot_stabilization_seconds: 180
post_reboot_cli_timeout: 600
ha_poll_interval: 5
ha_transition_timeout: 180

firmware_local_directory: "new_firmware"
firmware_remote_directory: "/var/nsinstall"
```

Use a trusted CA bundle and `netscaler_console_insecure: false` in production.
`prep_devices_parallel` counts individual appliances. `ha_pairs_parallel`
counts complete HA pairs.

The playbook enforces safety floors of 300 seconds to observe SSH stop, 1,200
seconds for SSH to return, 180 seconds of continuous post-SSH stabilization,
and 600 seconds for authenticated CLI validation. Higher configured values are
honored.

`ansible.cfg` defaults to 50 forks. The forks value must be high enough for the
selected concurrency.

## Firmware

Place exactly one `.tgz` or `.tar.gz` file in `new_firmware/`:

```bash
ls -lh new_firmware/
find new_firmware -maxdepth 1 -type f \
  \( -name '*.tgz' -o -name '*.tar.gz' \) -print
sha256sum new_firmware/*
```

Stage 1 inspects the archive locally, requires exactly one `installns` entry,
requires both the filename and internal `ns-<version>.gz` image to match
`netscaler_target_version`,
uploads the archive to `/var/nsinstall`, verifies its SHA-256 on the appliance,
extracts it with `tar -xzf`, verifies the extracted installer, and writes a
checksum-specific readiness marker.

## Stage 1 — prepare the fleet

Run from the repository root. Do not pass a manually maintained inventory;
Stage 1 obtains the current topology from Console and generates
`inventory.ini`.

```bash
git pull --ff-only

ansible-playbook upgrade_prep.yaml --syntax-check

ansible-playbook upgrade_prep.yaml \
  --ask-vault-pass \
  -e prep_devices_parallel=2
```

The syntax check can warn that `netscaler_nodes` does not exist. That warning
is expected because syntax checking does not execute the first play that creates
the dynamic group.

Stage 1 processes appliances in configurable parallel batches and performs:

1. TCP/22 reachability test.
2. Authenticated raw SSH test.
3. `/var` free-space check; at least 5 GiB is required.
4. Creation of `/var/nsinstall`.
5. Controller-side `scp -O` upload without requiring appliance Python.
6. Local/remote SHA-256 comparison.
7. Firmware extraction.
8. Validation of the extracted `installns` and readiness marker.

Outputs:

- `inventory.ini`
- `prepared_firmware.yml`
- `reports/prep-<UTC-ID>.json`
- `reports/prep-<UTC-ID>.md`

Verify the gate:

```bash
grep -E \
  '^(prepared_firmware_successful|prepared_target_version|prepared_device_count|expected_device_count|prepared_at):' \
  prepared_firmware.yml
```

All values must confirm success before Stage 2:

```yaml
prepared_firmware_successful: true
prepared_target_version: "14.1-73.33"
prepared_device_count: 6
expected_device_count: 6
```

### Silent upload, checksum, and extraction periods

The 1+ GiB upload is intentionally executed with `scp -q` and
`no_log: true` to prevent credentials from reaching console output. A quiet
screen during PREP 5/8 does not mean the playbook stopped.

In a second terminal:

```bash
pgrep -af 'ansible-playbook.*upgrade_prep.yaml'

ps -eo pid,etime,stat,pcpu,pmem,args |
grep -E '[a]nsible-playbook|[s]cp|[s]shpass'

watch -n 10 'ss -tinp | grep -A2 -E "10\.100\.[0-9]+\.[0-9]+:22"'
```

Increasing `bytes_sent` confirms progress. After SCP finishes, remote SHA-256
and extraction can also take several minutes on VPX appliances.

## Stage 2 — upgrade HA pairs

Only after a fully successful preparation:

```bash
ansible-playbook upgrade_perform.yaml \
  -i inventory.ini \
  --ask-vault-pass \
  -e ha_pairs_parallel=2
```

Two pairs in parallel means up to two independent pair workflows run
simultaneously. The nodes inside each pair are never upgraded simultaneously.

For each pair, Stage 2:

1. Verifies the preparation gate, target version, inventory SHA-256, SSH
   reachability, and both local HA roles with `show ha node 0`.
2. Disables and saves `haSync` and `haProp` on both nodes.
3. Creates a full system backup, installs and validates the original Secondary,
   requests one reboot, and validates the authenticated CLI and target version.
4. Requires both nodes locally `UP`; only then fails over to the upgraded
   Secondary.
5. Creates a backup, installs, reboots, and validates the original Primary.
6. Fails back and polls until the original roles are restored.
7. Validates final roles and versions.
8. Re-enables and saves `haSync` and `haProp` on both nodes.
9. Forces a final synchronization from the restored original Primary.

HA controls are changed with documented NetScaler CLI commands through the
collection's `netscaler.adc.ssh_netscaler_adc` connection plugin. Local node
state is verified before isolation, after isolation, and after restoration. If
a previous interrupted run left only one node with disabled controls, Stage 2
first requires matching versions and original healthy roles, enables both
nodes, saves, forces a clean sync, and verifies the healthy baseline before
isolating the pair again. This
avoids a `netscaler.adc.hanode` 2.17.0 duplicate-primary-key read failure on
two-node HA responses.

If the original Secondary fails installation, reboot, CLI validation, target
version validation, or the local `UP` health gate, the original Primary is not
upgraded. That pair stops and the next scheduled pair continues.

HA recovery is in an Ansible `always` section. It restores `haSync` and
`haProp` only when both live versions are known and equal and the original
roles are confirmed. Mixed versions, an unreachable node, or ambiguous roles
remain isolated as `MANUAL_RECOVERY_REQUIRED`. Equal-version nodes with
inverted roles receive one conditional, verified failback.

The official `./installns -Y -n` command runs directly and synchronously from
the installer path recorded by Stage 1 through the
NetScaler SSH connection. No background process or remote PID/RC/log tracking
files are created. A retry recognizes a completed target installation in
`/var/nsinstall/installns_state` and continues with the controlled reboot
instead of reinstalling the image.

An HTTP connection can close because the appliance has started rebooting. The
playbook confirms reboot by observing TCP/22 stop, waits for TCP/22 and the CLI
to return, requires TCP/22 to remain available through a 180-second
stabilization window, and then polls the authenticated CLI for up to 600 more
seconds. It never sends a second automatic reboot. ICMP ping is optional and
disabled by default.

## Final validation and reports

Stage 2 writes:

- `reports/upgrade-<UTC-ID>.json`
- `reports/upgrade-<UTC-ID>.md`

The process returns a non-zero exit code after writing the reports if any pair
fails. A pair is successful only when both nodes run the target version, the
original HA roles are restored, HA controls are enabled and saved, and final HA
synchronization succeeds.

```bash
ls -lt reports/upgrade-* | head
sed -n '1,240p' reports/upgrade-<UTC-ID>.md
```

## Test the repository

```bash
python3 -m unittest -v
ansible-playbook upgrade_prep.yaml --syntax-check
ansible-playbook upgrade_perform.yaml \
  -i inventory.ini.example \
  --syntax-check
```

For operational troubleshooting, snapshot recovery, retry rules, monitoring
commands, and manual validation, see [HELP.md](HELP.md).
