# NetScaler Fleet Upgrade Runbook

## 1. Controller prerequisites

- Run from the repository root.
- Install Ansible and the `netscaler.adc` collection described in `README.md`.
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
entry, requires its filename and internal `ns-<version>.gz` image to match
`netscaler_target_version`, uploads it to `/var/nsinstall/`, compares SHA-256 checksums, extracts it
with `tar -xzf`, and verifies the resulting installer path. This supports archives
where `installns` is at the root or inside a build subdirectory.

The preparation and upgrade appliance commands are Python-free. NetScaler 13.1
does not need to execute `ansible.builtin.ping`, `copy`, `stat`, `file`, or
`shell` modules. The controller uses `sshpass` with `scp -O` for the firmware
transfer; disk checks, checksums, extraction, markers, and `installns` execution
use raw SSH. Do not install or modify Python packages on a NetScaler appliance.

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

Standalone/non-HA Console entries are listed as skipped and do not abort
discovery. A device claiming HA with a missing or nonreciprocal peer still
aborts inventory generation.

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

During `installns`, the controller polls persistent `.pid`, `.log`, and
`.rc` files under `/var/tmp`; it does not depend on
`~/.ansible_async`, which is not reliably available on NetScaler. Repeated
poll messages mean the installer is still running, not that another installer
was started. The persistent launch and polling use raw SSH and remain usable if
the appliance's bundled Python runtime is unavailable before or after staging.

The ordered sequence for each pair is:

```text
validate roles
-> normalize, save, and synchronize haSync/haProp when a prior run left partial isolation
-> disable and save haSync/haProp on both nodes
-> full backup, original Secondary upgrade and one reboot request
-> require target version and both local nodes UP
-> failover
-> full backup, original Primary upgrade and one reboot request
-> verified failback
-> final version and role validation
-> enable and save haSync/haProp on both nodes
-> force final synchronization from the restored original Primary
```

The HA settings are changed with the documented NetScaler CLI commands sent
through the collection's `netscaler.adc.ssh_netscaler_adc` connection plugin.
They are not BSD shell commands. This avoids a `netscaler.adc.hanode` 2.17.0
read defect where the NITRO endpoint can return both HA nodes for `id=0` and
the module stops with a duplicate-primary-key error before changing anything.
The playbook always reads local node 0; it never accepts a two-node response as
proof of a local role. If the original Secondary fails installation, boot, CLI
validation, target-version validation, or the local `UP` gate, the original
Primary is not upgraded and processing advances to the next scheduled pair.

The recovery section reads both live versions. It re-enables HA controls only
when the versions are known and equal and the original roles are confirmed.
Mixed versions, an unreachable node, or ambiguous roles remain isolated and
are marked `MANUAL_RECOVERY_REQUIRED`. If equal-version nodes are inverted,
one conditional failback is issued and polled. A forced synchronization runs
only after both nodes reach the target and the original roles are restored.

## 8. Confirm completion

Review both files under `reports/upgrade-<UTC-ID>.*`. A pair is successful only if:

- Both NetScalers report `netscaler_target_version`.
- The original Primary is Primary again.
- The original Secondary is Secondary again.
- `haSync` and `haProp` were re-enabled and saved on both nodes.
- The final forced HA synchronization completed.
- All commands in the ordered workflow completed.

The final assertion produces a non-zero process exit status when any pair fails,
but only after the reports are safely written.

## 9. Retry rules

- Never launch Stage 2 after a failed or partial Stage 1.
- Rerun Stage 1 after changing firmware, inventory, Console topology, or HA roles.
- Review a failed pair before retrying; do not blindly increase concurrency.
- If the report says HA control restoration failed, manually verify and restore
  `haSync ENABLED` and `haProp ENABLED` on both nodes before retrying.
- A retry checks `/var/nsinstall/installns_state`. When it contains the target
  `VERSION` and `END_TIME`, the playbook does not run `installns` again; it
  resumes with the controlled reboot and post-boot validation.
- If the state is incomplete, inspect the persistent log named
  `/var/tmp/ansible-installns-<target>.log` before retrying.
- A non-zero persistent `.rc` is retained. Review the log first; only then may
  an operator authorize one retry with `-e retry_failed_installns=true`.
- Keep the reports for audit and change-control evidence.


## 10. Complete command sequence

Run this sequence from the repository root for a normal change.

### 10.1 Record the code and tool versions

~~~bash
git status --short
git pull --ff-only
git rev-parse HEAD
ansible --version
ansible-galaxy collection list netscaler.adc
~~~

Required baseline:

- Ansible Core 2.16 or newer.
- netscaler.adc collection 2.17.0 or newer.
- sshpass installed when SCP uses password authentication.
- No unexplained local Git changes.

Install or update the collection with the same Linux user that runs Ansible:

~~~bash
ansible-galaxy collection install 'netscaler.adc:>=2.17.0' --force
ansible-galaxy collection list netscaler.adc
~~~

### 10.2 Verify configuration and firmware

~~~bash
grep -E \
  '^(netscaler_target_version|netscaler_console_url|netscaler_console_insecure|prep_devices_parallel|ha_pairs_parallel|minimum_var_free_gb):' \
  variables.yaml

ls -lh new_firmware/

find new_firmware -maxdepth 1 -type f \
  \( -name '*.tgz' -o -name '*.tar.gz' \) -print

sha256sum new_firmware/*
tar -tzf new_firmware/*.tgz | grep -E '(^|/)installns$'
~~~

There must be one archive and one installns entry. The archive target must agree
with netscaler_target_version.

Lab configuration example:

~~~yaml
netscaler_target_version: "14.1-73.33"
netscaler_console_url: "https://10.100.71.200"
netscaler_console_insecure: true
minimum_var_free_gb: 5
prep_devices_parallel: 2
ha_pairs_parallel: 2
~~~

Use trusted certificates and insecure false in production.

The repository defaults (`host_key_checking=False`, SCP
`StrictHostKeyChecking=no`, and `netscaler_validate_certs: false`) are suitable
for this lab only. Before production, pin appliance SSH host keys, remove the
SCP bypass options, set `host_key_checking=True`, and validate Console/NITRO TLS
with your CA bundle. Do not reuse the lab `nsroot` password in production.

### 10.3 Validate Vault and SSH

~~~bash
ansible-vault view group_vars/all/vault.yml
~~~

After inventory.ini exists, test a node:

~~~bash
ansible -i inventory.ini \
  ns_10_100_48_1 \
  -m ansible.builtin.raw \
  -a 'echo CONNECTION_OK' \
  --ask-vault-pass
~~~

Test the exact Python-free disk command:

~~~bash
ansible -i inventory.ini \
  ns_10_100_48_1 \
  -m ansible.builtin.raw \
  -a "'df -Pk /var'" \
  --ask-vault-pass
~~~

The Avail column must be at least 5 GiB. For example, 9760438 KiB is
approximately 9.31 GiB.

### 10.4 Syntax checks

~~~bash
ansible-playbook upgrade_prep.yaml --syntax-check

ansible-playbook upgrade_perform.yaml \
  -i inventory.ini.example \
  --syntax-check
~~~

The Stage 1 syntax check can warn that no inventory was parsed and that
netscaler_nodes could not be matched. This is expected because syntax checking
does not execute the Console discovery play. YAML errors and missing module
errors are not expected.

### 10.5 Run preparation

~~~bash
ansible-playbook upgrade_prep.yaml \
  --ask-vault-pass \
  -e prep_devices_parallel=2
~~~

Preparation sequence:

| Step | Operation | Disruptive |
|---|---|---|
| Inventory | Query Console and validate reciprocal HA topology. | No |
| PREP 1/8 | Test NSIP TCP/22. | No |
| PREP 2/8 | Verify authenticated raw SSH. | No |
| PREP 3/8 | Read /var free space and require at least 5 GiB. | No |
| PREP 4/8 | Ensure /var/nsinstall exists. | No |
| PREP 5/8 | Upload firmware using controller-side scp -O. | No |
| PREP 6/8 | Compare local and remote SHA-256. | No |
| PREP 7/8 | Extract firmware. | No |
| PREP 8/8 | Verify installns and write the firmware marker. | No |

Preparation never runs installns and never reboots an appliance. It does not
require the appliance Python runtime.

### 10.6 Monitor preparation

The SCP task uses quiet mode and no_log to protect its password environment.
A quiet screen while transferring a 1.2 GiB image is normal.

Open a second controller session:

~~~bash
pgrep -af 'ansible-playbook.*upgrade_prep.yaml'

ps -eo pid,etime,stat,pcpu,pmem,args |
grep -E '[a]nsible-playbook|[s]cp|[s]shpass'
~~~

With prep_devices_parallel=2, two SCP processes mean both devices are being
uploaded concurrently. A parent Ansible process plus worker processes is normal.

Watch network counters:

~~~bash
watch -n 10 'ss -tinp | grep -A2 -E "10\.100\.[0-9]+\.[0-9]+:22"'
~~~

Increasing bytes_sent confirms progress. Ctrl+C in this second terminal stops
watch only; it does not stop the playbook.

Check remote size if needed:

~~~bash
ansible -i inventory.ini \
  'ns_10_100_48_1:ns_10_100_48_2' \
  -m ansible.builtin.raw \
  -a "'ls -lh /var/nsinstall/build-14.1-73.33_nc_64.tgz 2>/dev/null || echo UPLOAD_IN_PROGRESS'" \
  --ask-vault-pass
~~~

After SCP ends, remote checksum calculation and extraction may also take several
minutes. Investigate only if process, network, and file-size counters remain
unchanged for approximately ten minutes.

### 10.7 Validate the preparation gate

~~~bash
ls -lt reports/prep-* | head

sed -n '1,260p' "$(ls -t reports/prep-*.md | head -n 1)"

grep -E \
  '^(prepared_firmware_successful|prepared_target_version|prepared_device_count|expected_device_count|prepared_at):' \
  prepared_firmware.yml
~~~

Example for three pairs:

~~~yaml
prepared_firmware_successful: true
prepared_target_version: "14.1-73.33"
prepared_device_count: 6
expected_device_count: 6
~~~

Do not start Stage 2 when the success flag is false, counts differ, or any
device is unreachable, below the free-space threshold, or failed upload,
checksum, extraction, or installer validation.

A preparation rerun safely checks an existing archive checksum and can reuse a
matching remote file.

### 10.8 Run the upgrade

Confirm that another upgrade process is not active:

~~~bash
pgrep -af 'ansible-playbook.*upgrade'
~~~

Start two full pairs concurrently:

~~~bash
ansible-playbook upgrade_perform.yaml \
  -i inventory.ini \
  --ask-vault-pass \
  -e ha_pairs_parallel=2
~~~

Use upgrade_perform.yaml as the supported operator entry point. Do not run it
and ha_upgrade.yaml at the same time.

## 11. Detailed Stage 2 sequence

| Display | Operation |
|---|---|
| STEP 01/12 | Initialize result tracking and announce original roles. |
| STEP 02/12 | Validate matching versions, reachability, local roles and `UP`; reconcile a partial prior isolation; force sync; then disable and save haSync/haProp. |
| STEP 03–05/12 | Create a full backup; upgrade, reboot once, reconnect, and validate the original Secondary. |
| STEP 06/12 | Fail over to the upgraded original Secondary and validate both roles. |
| STEP 07–09/12 | Create a full backup; upgrade, reboot once, reconnect, and validate the original Primary. |
| STEP 10/12 | Fail back and poll until the original roles return. |
| STEP 11/12 | Read final HA state from both nodes. |
| STEP 12/12 | Validate both target versions and restored roles. |
| Always | Read live versions and roles; restore HA controls only when safe, otherwise require manual recovery. |
| Final sync | Force synchronization from the restored original Primary. |

Nodes in the same pair are never upgraded simultaneously. Two independent pair
workflows can advance together when ha_pairs_parallel is 2.

For every reboot, the playbook requires a zero installer return code, completed
target install state, a target kernel under `/flash`, and a matching kernel
selection in `/flash/boot/loader.conf`. Only then does it send exactly one
reboot request, observe TCP/22 stop, wait for TCP/22 to return, require a
180-second post-SSH stabilization window, and poll the authenticated CLI for up
to 600 seconds for the exact target.
An HTTP connection close is accepted only when TCP/22 subsequently stops. ICMP
ping is optional (`reboot_ping_check_enabled`) and disabled by default.

Polling occurs every five seconds. The stabilization window is intentionally
fixed because TCP/22 can open while appliance services are still booting. The
playbook allows at least 1,200 seconds for SSH to return and never sends a
second reboot when a boot exceeds the timeout.

## 12. Monitor installns and reboot

Controller process:

~~~bash
pgrep -af 'ansible-playbook.*upgrade_perform.yaml'
~~~

Repeated FAILED - RETRYING messages during polling normally mean that the
persistent return-code file is not ready. They do not indicate that Ansible
started another installer.

From an appliance BSD shell:

~~~sh
ps -axo pid,etime,stat,command | grep '[i]nstallns'
ls -l /var/tmp/ansible-installns-*
cat /var/tmp/ansible-installns-14.1-73.33.rc
tail -n 100 /var/tmp/ansible-installns-14.1-73.33.log
grep -E '^(VERSION|END_TIME)' /var/nsinstall/installns_state
~~~

Interpretation:

- Active installns: do not manually reboot or launch another installer.
- Missing return-code file: installation has not published completion.
- Return code 0: the installer process exited successfully, but this alone does
  not authorize a reboot.
- A retry resumes at controlled reboot only when return code 0, target VERSION,
  END_TIME, target `/flash` kernel, and target `loader.conf` entry all agree.
- Non-zero return code: inspect the persistent log before retrying.

The playbook preserves the last 200 installer-log lines and the pre-reboot boot
evidence on the controller:

~~~bash
ls -lt reports/upgrade-*-installns.txt reports/upgrade-*-postboot.txt
~~~

## 13. Completion and reports

~~~bash
ls -lt reports/upgrade-* | head
sed -n '1,300p' "$(ls -t reports/upgrade-*.md | head -n 1)"
~~~

A pair succeeds only when both nodes run the target version, original roles are
restored, haSync and haProp are enabled and saved, final synchronization
succeeds, and all ordered tasks completed.

Reports are written before the playbook returns a failing exit code for any
failed pair.

## 14. Snapshot and retry rules

If any NetScaler VM snapshot is restored, rerun Stage 1 for the full fleet.
Snapshots can remove the uploaded archive, extracted files, marker, install
state, or role changes while the controller still contains newer metadata.
Never trust an old prepared_firmware.yml after snapshot restoration.

If Stage 2 stops after installns, inspect process, return code, and install state
before retrying:

~~~sh
ps -axo pid,etime,stat,command | grep '[i]nstallns'
cat /var/tmp/ansible-installns-14.1-73.33.rc
grep -E '^(VERSION|END_TIME)' /var/nsinstall/installns_state
~~~

Never launch a second installns while one is active. Target VERSION and END_TIME
without a matching return code, target kernel, and loader entry are treated as
stale or incomplete state; the playbook does not reboot from that evidence.

If HA control restoration fails, manually confirm original roles, haSync
ENABLED, haProp ENABLED, healthy RPC/HA state, and configuration synchronization
on both nodes before retrying.

## 15. Troubleshooting commands

Free-space parser:

~~~bash
git pull --ff-only
grep -nF "regex_findall('([0-9]+) +[0-9]+%')" upgrade_prep.yaml

ansible -i inventory.ini ns_10_100_48_1 \
  -m ansible.builtin.raw \
  -a "'df -Pk /var'" \
  --ask-vault-pass
~~~

A parser failure is not automatically insufficient space. The playbook reports
INSUFFICIENT_SPACE only after parsing an actual value.

Incorrect password:

~~~bash
ansible-vault edit group_vars/all/vault.yml

ansible -i inventory.ini ns_10_100_48_1 \
  -m ansible.builtin.raw \
  -a 'echo CONNECTION_OK' \
  --ask-vault-pass
~~~

Appliance Python or libcrypto error: do not modify NetScaler system libraries.
Update the repository and collection. Preparation and installer operations are
designed to use raw SSH.

~~~bash
git pull --ff-only
ansible-galaxy collection list netscaler.adc
~~~

The maintained workflow does not use the unreliable NetScaler Ansible async
directory. installns uses persistent PID, log, and return-code files under
/var/tmp.

### SHA-256 appears missing or mismatched

Current preparation accepts a NetScaler login banner appended directly to a
valid 64-character digest. Update before retrying:

~~~bash
git pull --ff-only
git log -1 --oneline

ansible -i inventory.ini ns_10_100_48_1 \
  -m ansible.builtin.raw \
  -a "'sha256 -q /var/nsinstall/build-14.1-73.33_nc_64.tgz'" \
  --ask-vault-pass

sha256sum new_firmware/build-14.1-73.33_nc_64.tgz
~~~

If the digests match, rerunning Stage 1 reuses the already uploaded archive.
A true mismatch causes a fresh upload and validation.

### HA role check fails immediately

If all pairs fail at Confirm original Primary role is still current with a
non-zero return code, update the repository. NetScaler CLI commands must be sent
directly through the connection-plugin bypass marker; BSD commands must use the
plugin's shell path.

~~~bash
git pull --ff-only
git log -1 --oneline

ansible -i inventory.ini ns_10_100_48_1 \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ha node' \
  --ask-vault-pass
~~~

The output must contain Master State : Primary for the recorded Primary.
Failure at this check occurs before HA controls, installns, or reboot are
changed, so it is safe to correct the command routing and retry.

### hanode reports a duplicate primary key

Collection 2.17.0 can authenticate to NITRO successfully but fail while reading
`hanode id=0` because the endpoint returns both HA nodes. The diagnostic ends
with text similar to `Found more than one resource with the same primary key`.
This is a collection read-path problem, not a bad password and not evidence that
the appliance was modified.

Update the repository. The maintained playbook avoids this module path and uses
the collection's SSH connection plugin with the documented local-node commands:

~~~bash
git pull --ff-only
git log -1 --oneline

ansible -i inventory.ini netscaler_nodes \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ha node 0' \
  --ask-vault-pass
~~~

Before Stage 2, each local node must show `Propagation: ENABLED`; `Sync State`
must be `ENABLED` on the Primary and normally `SUCCESS` on the Secondary.

### installns launch says the complete command is not found

If every pair fails at `Start installns without remote Python` with output
similar to the following, no installer was started:

~~~text
sh: cd /var/nsinstall && (nohup ...): not found
ERROR: Export failed.
~~~

The `netscaler.adc.ssh_netscaler_adc` connection plugin automatically prefixes
BSD commands with the NetScaler `shell` command. Quoting the complete compound
command adds a second shell-quoting layer and makes the appliance interpret the
entire line as one executable name. The maintained playbook quotes paths and the
inner `/bin/sh -c` script only, not the complete outer command.

Update and perform a syntax check before retrying:

~~~bash
git pull --ff-only
git log -1 --oneline

ansible-playbook upgrade_perform.yaml \
  -i inventory.ini \
  --syntax-check
~~~

The failed launch does not run `installns` and does not reboot the appliance.
The recovery section first compares live versions and roles; it re-enables
`haSync` and `haProp` only when safe. A Secondary response of `Warning: The
running configuration has not changed` during `save ns config` is accepted only
when subsequent state verification passes.

## 16. SSH resets while restoring HA controls

A Secondary can reset TCP/22 while rebooting and the NITRO HTTP request can end
with `Remote end closed connection without response`. That message alone is not
a failed reboot. The current playbook verifies that TCP/22 stops, waits up to
1,200 seconds for it to return, waits another 180 seconds for boot stabilization,
and then polls authenticated CLI output for up to 600 seconds. If the Secondary does not
complete these checks, the original Primary is skipped and the next pair can
continue. Reports are still written even if a pair returns no normal completion
record.

If this occurs on an older checkout, stop that controller run before it starts
another pair, wait for both nodes, and restore the controls explicitly:

~~~bash
# First Ctrl+C in the running Ansible terminal, then A to abort.

ansible -i inventory.ini localhost -c local -m ansible.builtin.wait_for \
  -a 'host=10.100.48.2 port=22 state=started delay=5 sleep=5 timeout=600' \
  --ask-vault-pass

ansible -i inventory.ini \
  'ns_10_100_48_1:ns_10_100_48_2' \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc set ha node -hasync ENABLED -haprop ENABLED' \
  --ask-vault-pass

ansible -i inventory.ini \
  'ns_10_100_48_1:ns_10_100_48_2' \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc save ns config' \
  --ask-vault-pass

ansible -i inventory.ini \
  'ns_10_100_48_1:ns_10_100_48_2' \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ha node 0' \
  --ask-vault-pass
~~~

Repeat with the affected pair addresses. Require `Propagation: ENABLED` and
`Sync State: ENABLED` or `SUCCESS` on both nodes. Then check running versions
and live roles before deciding whether the idempotent upgrade can be resumed.
Do not force HA synchronization until both nodes are reachable, healthy, and
running compatible versions.

## 17. VM console repeats CAM/SCSI Busy during boot

Messages such as these are produced by the appliance kernel, not by Ansible:

~~~text
CAM status: SCSI Status Error
SCSI status: Busy
Retrying command
~~~

They indicate that the guest cannot complete disk I/O during boot. The playbook
does not send another reboot. It times out that Secondary, does not start the
original Primary, retains HA isolation when the live versions cannot be proven
equal, records `MANUAL_RECOVERY_REQUIRED`, and continues scheduling other pairs.

For a lab, inspect the hypervisor datastore/controller and reset the affected
VM once if your change procedure permits it. In production, treat repeated disk
errors as an infrastructure incident: capture the console, validate storage
latency/errors and the virtual SCSI controller, and involve the platform team.
Do not solve recurring disk I/O faults by increasing the Ansible timeout.

## 18. Audit evidence

Retain:

- Git commit SHA.
- Ansible and collection versions.
- Sanitized variables.
- Firmware SHA-256.
- Preparation JSON and Markdown reports.
- Upgrade JSON and Markdown reports.
- Console topology export.
- Application validation evidence.
- Manual recovery actions.

Record the execution baseline:

~~~bash
git rev-parse HEAD
git status --short
ansible --version
ansible-galaxy collection list netscaler.adc
~~~
