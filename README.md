# NetScaler HA fleet upgrade

This repository provides a two-stage Ansible workflow for upgrading large fleets
of NetScaler HA pairs discovered from NetScaler Console.

1. `upgrade_prep.yaml` discovers HA pairs, creates `inventory.ini`, validates every
   appliance, uploads and verifies firmware, extracts it under `/var/nsinstall`,
   and unlocks the upgrade only when every device is ready.
2. `upgrade_perform.yaml` upgrades multiple HA pairs concurrently while preserving
   the safe Secondary → failover → original Primary → restore-roles sequence.

The original `ha_upgrade.yaml` remains as the underlying Stage 2 workflow for
backward compatibility. See [HELP.md](HELP.md) for the complete operating runbook.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
ansible-galaxy collection install 'netscaler.adc:>=2.0.0'
```

Password-based SSH also requires `sshpass` on the Ansible controller.

The workflow uses the upstream NetScaler Automation Toolkit normal-mode template
as its foundation.

## Configure credentials

```bash
ansible-vault create group_vars/all/vault.yml
```

```yaml
vault_nitro_user: nsroot
vault_nitro_pass: "replace-with-the-real-password"
vault_console_user: automation-api
vault_console_pass: "replace-with-the-console-password"
```

Passwords never belong in `inventory.ini`. The inventory, preparation metadata,
reports, Vault file, and firmware archives are excluded from Git.

## Stage 1: prepare all NetScalers

Place exactly one NetScaler `.tgz` or `.tar.gz` archive in `new_firmware/`.
Set the target version and real Console URL in `variables.yaml`, or override the
URL at runtime:

```bash
ansible-playbook upgrade_prep.yaml \
  --ask-vault-pass \
  -e netscaler_console_url=https://console.example.org
```

Preparation performs the following operations:

- Queries every Console API page and creates `inventory.ini` with all reciprocal
  HA pairs, including currently unhealthy pairs so they can be reported.
- Processes up to `prep_devices_parallel` appliances simultaneously (default 20).
- Lists TCP-unreachable and authentication-failed NetScalers.
- Checks `/var` and blocks any device with less than 5 GB free.
- Uploads the archive from `new_firmware/` to `/var/nsinstall/`.
- Compares local and remote SHA-256 checksums.
- Inspects the archive to locate its exact `installns` path, extracts with
  `tar -xzf`, and verifies that `installns` exists.
- Writes a verified marker on each prepared appliance.
- Writes JSON and Markdown reports under `reports/`.
- Creates `prepared_firmware.yml`, including the firmware and inventory checksums.

Stage 1 returns a non-zero exit code if any device is unreachable, has less than
5 GB free, or fails upload, checksum, or extraction validation. Stage 2 remains
locked until all discovered NetScalers are prepared successfully.

## Stage 2: perform the HA upgrades

After Stage 1 reports success:

```bash
ansible-playbook upgrade_perform.yaml \
  -i inventory.ini \
  --ask-vault-pass \
  -e ha_pairs_parallel=5
```

`ha_pairs_parallel` limits complete HA pairs, not individual nodes. Within every
pair the workflow remains strictly ordered:

1. Verify the preparation gate, inventory checksum, reachability, and live HA roles.
2. Disable and save `haSync` and `haProp` on both nodes with
   `netscaler.adc.hanode`.
3. Upgrade and validate the original Secondary.
4. Fail over to the upgraded Secondary.
5. Upgrade and validate the original Primary.
6. Fail back to restore the original roles.
7. Confirm both versions and both final roles.
8. Re-enable and save `haSync` and `haProp` on both nodes, then force a final
   synchronization from the restored original Primary.

The HA controls are restored from the playbook's `always` section, including
failed pair runs. A restoration failure changes the pair result to `FAILED` and
is recorded in the final report; it must be corrected manually before another
upgrade attempt. The final forced synchronization runs only after the complete
pair upgrade and original-role validation succeed.

Live task names show `STEP 01/12` through `STEP 12/12`. Because NetScaler
appliances do not provide Ansible's normal async-job directory reliably,
`installns` is launched as a persistent remote process and polled through
controller-independent PID, log, and return-code files in `/var/tmp`.

Before launching `installns`, Stage 2 inspects
`/var/nsinstall/installns_state`. If the target image has an `END_TIME`, a
retry safely resumes at the controlled reboot instead of reinstalling the
already-staged image. Running versions are parsed from the complete `show
version` output so login banners or blank leading lines do not hide the result.

The final JSON and Markdown reports contain per-pair before/after versions,
duration, status, and failure details. The playbook exits non-zero after writing
the reports if any pair fails.

## Main configuration

```yaml
netscaler_target_version: "14.1-xx.xx"
prep_devices_parallel: 20
ha_pairs_parallel: 2
minimum_var_free_gb: 5
firmware_local_directory: new_firmware
firmware_remote_directory: /var/nsinstall
```

For 100+ appliances, begin with conservative values, observe controller CPU,
network throughput, and NetScaler management responsiveness, then increase
`prep_devices_parallel` or `ha_pairs_parallel`. `ansible.cfg` defaults to 50 forks;
`--forks` must be high enough for the requested concurrency.

## Validate locally

```bash
python3 -m unittest -v
ansible-playbook upgrade_prep.yaml --syntax-check
ansible-playbook upgrade_perform.yaml -i inventory.ini.example --syntax-check
```
