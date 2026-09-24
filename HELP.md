# NetScaler HA fleet upgrade runbook

This is the operator runbook for the current repository version. Read
[README.md](README.md), this file, [TROUBLESHOOTING.md](TROUBLESHOOTING.md),
and [DISCLAIMER.md](DISCLAIMER.md) before a change.

## 1. Scope and operator responsibility

This workflow supports normal two-node NetScaler HA pairs discovered through
NetScaler Console. It does not support standalone appliances, clusters, or an
invented one-node pair.

The current playbooks do not create:

- NetScaler system backups.
- Configuration exports.
- Hypervisor snapshots.
- Application rollback points.

The command save ns config appears in Stage 2 only to persist changes to haSync
and haProp. It is not a backup operation.

Before running the automation, the operator must decide whether organizational
policy requires an appliance backup, configuration export, VM snapshot, or
other rollback artifact and create it outside this project. The operator also
owns maintenance-window approval, application validation, rollback decisions,
and vendor compatibility checks.

## 2. Record the execution baseline

Run from the repository root:

~~~bash
cd /path/to/Ansible_NetScaler_Upgrade

git status --short
git pull --ff-only
git rev-parse HEAD

ansible --version
ansible-galaxy collection list netscaler.adc
command -v sshpass
~~~

Required baseline:

- Ansible Core 2.16 or newer.
- netscaler.adc 2.17.0 or newer.
- sshpass available.
- No unresolved Git conflict.
- Controller routing to Console and every NSIP.

If git status displays local modifications, review them before pulling. Do not
discard local credentials or configuration blindly.

## 3. Install controller dependencies

Ubuntu example:

~~~bash
sudo apt update
sudo apt install -y git python3-venv python3-pip sshpass

ansible-galaxy collection install 'netscaler.adc:>=2.17.0' --force
ansible-galaxy collection list netscaler.adc
~~~

A virtual environment is optional when the system Ansible version satisfies the
requirement. Always install and run the collection with the same Linux account
that runs the playbooks.

## 4. Create and validate Ansible Vault

~~~bash
mkdir -p group_vars/all
ansible-vault create group_vars/all/vault.yml
~~~

Enter:

~~~yaml
---
vault_nitro_user: nsroot
vault_nitro_pass: "NetScaler-password"
vault_console_user: automation-api
vault_console_pass: "Console-password"
~~~

Validate and edit:

~~~bash
ansible-vault view group_vars/all/vault.yml
ansible-vault edit group_vars/all/vault.yml
~~~

Do not place credentials in inventory.ini, variables.yaml, an -e argument, or a
committed Vault-password file.

## 5. Configure target, Console, and concurrency

~~~bash
grep -E \
  '^(netscaler_target_version|netscaler_console_url|netscaler_console_ca_bundle|netscaler_console_insecure|minimum_var_free_gb|prep_devices_parallel|ha_pairs_parallel|firmware_local_directory|firmware_remote_directory):' \
  variables.yaml
~~~

Production-oriented example:

~~~yaml
netscaler_target_version: "14.1-73.33"
netscaler_console_url: "https://console.example.com"
netscaler_console_ca_bundle: "/path/to/console-ca.pem"
netscaler_console_insecure: false

firmware_local_directory: "new_firmware"
firmware_remote_directory: "/var/nsinstall"
minimum_var_free_gb: 5
prep_devices_parallel: 20
ha_pairs_parallel: 2
~~~

Concurrency meanings:

- prep_devices_parallel prepares individual appliances.
- ha_pairs_parallel schedules complete pair workflows.
- A pair's Secondary and Primary are upgraded sequentially.
- ansible.cfg has forks = 50; ensure it is sufficient for selected concurrency.

Current reboot safety floors are enforced by the node task:

- Up to 300 seconds to observe TCP/22 stop.
- Up to 1,200 seconds for TCP/22 to return.
- 180 seconds of post-SSH stabilization by default.
- At least 600 seconds for authenticated CLI/version polling.

The playbook sends only one reboot request. It does not automatically issue a
second reboot after a timeout.

## 6. Review security defaults

The repository currently disables SSH host-key checking, bypasses known_hosts
for SCP, and defaults NITRO certificate validation to false. These settings are
convenient for a lab but are not a production security baseline. Before
production use, evaluate pinned SSH keys, CA validation, restricted
credentials, and a dedicated automation account.

## 7. Select and validate firmware

Keep exactly one archive in new_firmware:

~~~bash
ls -lh new_firmware/

find new_firmware -maxdepth 1 -type f \
  \( -name '*.tgz' -o -name '*.tar.gz' \) -print

sha256sum new_firmware/*
tar -tzf new_firmware/*.tgz | grep -E '(^|/)installns$'
tar -tzf new_firmware/*.tgz | grep -E '(^|/)ns-[0-9].*[.]gz$'
~~~

Stage 1 requires exactly one archive and installns entry, a filename matching
build-<target>_*.tgz, and an internal ns-<target>.gz image.

## 8. Validate the repository

~~~bash
python3 -m unittest -v
ansible-playbook upgrade_prep.yaml --syntax-check
ansible-playbook upgrade_perform.yaml \
  -i inventory.ini.example \
  --syntax-check
~~~

The Stage 1 syntax check may warn that no inventory or netscaler_nodes group was
parsed because Console discovery has not executed. YAML errors, missing
collections, and module errors are not expected.

## 9. Run Stage 1 preparation

~~~bash
ansible-playbook upgrade_prep.yaml \
  --ask-vault-pass \
  -e prep_devices_parallel=20
~~~

Stage 1 generates inventory from Console; do not pass a manually maintained
inventory to this command.

| Step | Operation | Disruptive |
|---|---|---|
| Discovery | Query Console, skip standalone entries, validate reciprocal HA pairs, and create inventory.ini. | No |
| PREP 1/8 | Test TCP/22. | No |
| PREP 2/8 | Test authenticated raw SSH. | No |
| PREP 3/8 | Require configured free space in /var. | No |
| PREP 4/8 | Ensure /var/nsinstall exists. | No |
| PREP 5/8 | Reuse or upload the archive with controller-side scp -O. | No |
| PREP 6/8 | Verify the remote SHA-256. | No |
| PREP 7/8 | Extract the archive in /var/nsinstall. | No |
| PREP 8/8 | Verify installns and write the readiness marker. | No |

Stage 1 does not run installns, change the boot loader, reboot, change HA roles,
disable HA controls, or create a backup.

Outputs:

- inventory.ini
- prepared_firmware.yml
- reports/prep-<UTC-ID>.md
- reports/prep-<UTC-ID>.json

## 10. Handle standalone appliances

The generator skips an instance when Console explicitly reports
is_ha_configured as false. A missing attribute is invalid data and stops
discovery. Skipped instances are recorded under skipped_non_ha_instances in
JSON and under Standalone/non-HA Console instances skipped in Markdown.

~~~bash
LATEST_PREP_MD="$(ls -t reports/prep-*.md | head -n 1)"
less "$LATEST_PREP_MD"

sed -n '/Standalone\/non-HA Console instances skipped/,/### Unreachable/p' \
  "$LATEST_PREP_MD"

LATEST_PREP_JSON="$(ls -t reports/prep-*.json | head -n 1)"
jq '.skipped_non_ha_instances' "$LATEST_PREP_JSON"
~~~

Required operator action:

- If intentionally standalone, remove it from the HA change scope and use a
  separate standalone runbook.
- Plan its own outage, external backup/rollback, and application validation.
- If it should be HA, correct appliance and Console topology, wait for healthy
  HA state, and rerun Stage 1.
- Never manually add it to netscaler_ha_pairs or invent a peer.

Standalone appliances are not included in prepared_device_count or
expected_device_count. Those counts apply only to validated HA nodes.

## 11. Review the preparation gate

~~~bash
ls -lt reports/prep-* | head
less "$(ls -t reports/prep-*.md | head -n 1)"

grep -E \
  '^(prepared_firmware_successful|prepared_target_version|prepared_firmware_sha256|prepared_device_count|expected_device_count|prepared_at):' \
  prepared_firmware.yml
~~~

Required result:

~~~yaml
prepared_firmware_successful: true
prepared_target_version: "14.1-73.33"
prepared_device_count: 6
expected_device_count: 6
~~~

Do not continue if the success flag is false, HA-node counts differ, or any HA
node failed connectivity, authentication, space, upload, checksum, extraction,
or installer validation.

A rerun reuses an existing remote archive when its SHA-256 matches.

## 12. Validate inventory and live state

~~~bash
ansible-inventory -i inventory.ini --graph

ansible -i inventory.ini netscaler_nodes \
  -m ansible.builtin.raw \
  -a 'echo CONNECTION_OK' \
  --ask-vault-pass

ansible -i inventory.ini netscaler_nodes \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ns version' \
  --ask-vault-pass

ansible -i inventory.ini netscaler_nodes \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ha node 0' \
  --ask-vault-pass
~~~

Each pair must have one local Primary, one local Secondary, both nodes UP, and
equal readable versions. If roles changed after Stage 1, rerun Stage 1.

## 13. Run Stage 2

Confirm no other upgrade is active:

~~~bash
pgrep -af 'ansible-playbook.*upgrade'
~~~

Start conservatively:

~~~bash
ansible-playbook upgrade_perform.yaml \
  -i inventory.ini \
  --ask-vault-pass \
  -e ha_pairs_parallel=1
~~~

After validation, use configured concurrency:

~~~bash
ansible-playbook upgrade_perform.yaml \
  -i inventory.ini \
  --ask-vault-pass \
  -e ha_pairs_parallel=2
~~~

Use upgrade_perform.yaml as the operator entry point. Never run it and
ha_upgrade.yaml simultaneously.

## 14. Current Stage 2 sequence

| Display | Current operation |
|---|---|
| Initialization | Verify concurrency, preparation success, target version, and inventory SHA-256. |
| Preflight | Test TCP/22 for all HA nodes and summarize reachability. |
| STEP 01/12 | Initialize pair result tracking. |
| STEP 02/12 | Require preflight; validate local roles and UP state with show ha node 0; require equal versions; disable and save haSync/haProp; verify isolation. |
| STEP 03-05/12 | Upgrade and validate the original Secondary, or skip install/reboot if already at target. |
| STEP 06/12 | Require both nodes UP, fail over, and verify inverted roles. |
| STEP 07-09/12 | Upgrade and validate the original Primary, or skip install/reboot if already at target. |
| STEP 10/12 | Require both nodes UP in inverted roles and fail back. |
| STEP 11/12 | Confirm original roles are restored. |
| STEP 12/12 | Require both nodes at target and original roles restored. |
| Always | Read live versions/roles, restore HA controls only when safe, and build the pair report. |
| Final sync | Force synchronization from the restored original Primary after pair success and HA-control restoration. |

There is no backup task and no pre-isolation forced synchronization in the
current implementation.

Per-node behavior:

- Already running target: skip installns and reboot, then validate.
- Target selected in loader.conf with target kernel present: skip installns and
  continue with controlled reboot.
- Otherwise: run native ./installns -Y -n synchronously.
- Send one NITRO reboot request.
- Observe TCP/22 stop and return, stabilize, then poll show ns version.

If the original Secondary fails, the original Primary is not upgraded.
If one node starts at target and its peer does not, the pair stops at the
equal-version gate before isolation. When both already run target, node install
and reboot are skipped, but pair isolation, failover, failback, HA-control
restoration, and final synchronization still run.

## 15. Monitor the change

~~~bash
pgrep -af 'ansible-playbook.*upgrade_perform.yaml'

ps -eo pid,ppid,etime,stat,pcpu,pmem,args |
grep -E '[a]nsible-playbook|[s]sh|[s]cp|[s]shpass'
~~~

The installns task is synchronous, so the terminal remains on that task until
the appliance command returns. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for
appliance process, loader, install-state, reboot, and HA commands.

## 16. Read reports

~~~bash
ls -lt reports/prep-* | head
ls -lt reports/upgrade-* | head

LATEST_UPGRADE_MD="$(ls -t reports/upgrade-*.md | head -n 1)"
echo "$LATEST_UPGRADE_MD"
less "$LATEST_UPGRADE_MD"
~~~

In less, use /FAILED, press n for the next match, and q to exit.

~~~bash
grep -nEi -A12 -B3 'FAILED|MANUAL_RECOVERY|Failures|error' \
  "$LATEST_UPGRADE_MD"

LATEST_UPGRADE_JSON="$(ls -t reports/upgrade-*.json | head -n 1)"
python3 -m json.tool "$LATEST_UPGRADE_JSON" | less
~~~

With jq:

~~~bash
jq '.results[] | {
  pair,
  status,
  original_primary,
  original_secondary,
  primary_before,
  primary_after,
  secondary_before,
  secondary_after,
  error
}' "$LATEST_UPGRADE_JSON"
~~~

Reports are written before the final failing assertion. That assertion is
expected when one or more pairs are not SUCCESS.

Per-node controller evidence:

~~~bash
ls -lt reports/upgrade-*-installns.txt reports/upgrade-*-postboot.txt 2>/dev/null
~~~

## 17. Post-change validation

~~~bash
ansible -i inventory.ini netscaler_nodes \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ns version' \
  --ask-vault-pass

ansible -i inventory.ini netscaler_nodes \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ha node 0' \
  --ask-vault-pass
~~~

Expected final state:

- Every HA node runs the target build.
- Original Primary and Secondary roles are restored.
- Every node is UP.
- Propagation is ENABLED.
- Sync State is ENABLED or SUCCESS.
- Every report row is SUCCESS.

Application, data-plane, certificate, license, interface, routing, GSLB,
Gateway, WAF, and monitoring validation remain environment-specific operator
responsibilities.

## 18. Retry rules

- Never run two Stage 2 processes simultaneously.
- Never retry blindly after MANUAL_RECOVERY_REQUIRED.
- Rerun Stage 1 after firmware, inventory, topology, role, or snapshot changes.
- Never trust old prepared_firmware.yml after restoring an appliance snapshot.
- Before retrying, check versions, roles, node state, HA controls, installns,
  loader target, and the latest report.
- Never start a second installns while one is active.
- Never force HA synchronization across mixed or unknown versions.
- Keep mixed or unreachable pairs isolated until controlled recovery.

## 19. Audit evidence

Retain the Git SHA, Ansible/collection versions, sanitized configuration,
firmware SHA-256, Stage 1 and Stage 2 reports, per-node evidence, Console
topology export, external backup evidence, and application-validation records.

~~~bash
git rev-parse HEAD
git status --short
ansible --version
ansible-galaxy collection list netscaler.adc
sha256sum new_firmware/*
~~~

## 20. Disclaimer

This code and documentation are provided on a best-effort, as-is basis with no
warranty or guarantee. Use is at the operator's risk. The authors and
contributors have no obligation to provide future support, maintenance,
upgrades, compatibility work, or bug fixes. See [DISCLAIMER.md](DISCLAIMER.md).
