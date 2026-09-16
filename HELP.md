# NetScaler Fleet Upgrade Runbook

## 1. Controller prerequisites

- Run from the repository root.
- Install Ansible and the `citrix.adc` collection described in `README.md`.
- Install `sshpass` when the NetScalers use password-based SSH.
- Ensure the controller can reach NetScaler Console and every NSIP on TCP/22 and
  the configured NITRO management protocol.
- Confirm `ansible.cfg` provides enough forks for the selected concurrency.
- Test the workflow against a non-production HA pair before a fleet rollout.

## 2. Configure secure credentials

Create `group_vars/all/vault.yml` with `ansible-vault create`. Define:

```yaml
vault_nitro_user: nsroot
vault_nitro_pass: "NetScaler-password"
vault_console_user: automation-api
vault_console_pass: "Console-password"
```

Console credentials can alternatively come from `NETSCALER_CONSOLE_USER` and
`NETSCALER_CONSOLE_PASS`. Do not put passwords in inventory, variables, command
history, or a committed Vault password file.

## 3. Select the firmware

Remove old archives from `new_firmware/` and place exactly one target `.tgz` or
`.tar.gz` file there. Configure `netscaler_target_version` in `variables.yaml`.

Preparation lists the archive with `tar -tzf`, requires exactly one `installns`
entry, uploads it to `/var/nsinstall/`, compares SHA-256 checksums, extracts it
with `tar -xzf`, and verifies the resulting installer path. This supports archives
where `installns` is at the root or inside a build subdirectory.

## 4. Run Stage 1 — preparation

```bash
ansible-playbook upgrade_prep.yaml \
  --ask-vault-pass \
  -e netscaler_console_url=https://your-console.example.com
```

Optional concurrency override:

```bash
-e prep_devices_parallel=25
```

Expected outputs:

- `inventory.ini`: all discovered reciprocal HA pairs.
- `reports/prep-<UTC-ID>.json`: structured fleet preparation result.
- `reports/prep-<UTC-ID>.md`: readable preparation report.
- `prepared_firmware.yml`: Stage 2 safety gate and checksums.

The summary explicitly separates:

- `UNREACHABLE`: TCP/22 or authenticated Ansible connection failed.
- `INSUFFICIENT_SPACE`: `/var` has less than 5 GB free.
- `FAILED`: upload, SHA-256, extraction, or installer validation failed.
- `PREPARED`: upload and extraction were verified successfully.

Do not proceed while Stage 1 returns a non-zero exit code. Correct the listed
devices and rerun the entire preparation stage. The generated inventory is safely
replaced by the preparation playbook.

## 5. Review the preparation gate

Confirm the report shows every device as `PREPARED`. Verify that the device count
is twice the HA-pair count and that the selected firmware SHA-256 is consistent.
Do not manually edit `inventory.ini` or `prepared_firmware.yml`; Stage 2 verifies
the saved inventory checksum and rejects modifications.

## 6. Run Stage 2 — upgrade

Start conservatively, for example two HA pairs at a time:

```bash
ansible-playbook upgrade_perform.yaml \
  -i inventory.ini \
  --ask-vault-pass \
  -e ha_pairs_parallel=2
```

Increase only after observing a successful batch. The serial limit applies to
whole HA pairs. The two nodes of a pair are never upgraded simultaneously.

## 7. Monitor progress

The console displays fleet preflight counts and pair-specific steps. A pair is
blocked if either NSIP becomes unreachable or if the live roles no longer match
the inventory created during preparation. Other healthy pairs continue.

The disruptive sequence is always:

```text
original Secondary -> validate -> failover -> original Primary -> validate
-> fail back -> final version and role validation
```

## 8. Confirm completion

Review both files under `reports/upgrade-<UTC-ID>.*`. A pair is successful only if:

- Both NetScalers report `netscaler_target_version`.
- The original Primary is Primary again.
- The original Secondary is Secondary again.
- All commands in the ordered workflow completed.

The final assertion produces a non-zero process exit status when any pair fails,
but only after the reports are safely written.

## 9. Retry rules

- Never launch Stage 2 after a failed or partial Stage 1.
- Rerun Stage 1 after changing firmware, inventory, Console topology, or HA roles.
- Review a failed pair before retrying; do not blindly increase concurrency.
- Keep the reports for audit and change-control evidence.
