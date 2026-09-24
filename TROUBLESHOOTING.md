# NetScaler HA upgrade troubleshooting

Use this document while Stage 1 or Stage 2 is running or after a failed report.
Commands are observational unless a section explicitly says that it changes HA
state.

Do not start a second playbook, installns process, or reboot until the current
state is understood.

## 1. Always start in the repository directory

Many inventory and Vault errors are caused by running from the wrong directory.

~~~bash
cd /path/to/Ansible_NetScaler_Upgrade
pwd
ls -l ansible.cfg inventory.ini variables.yaml
~~~

If Ansible says it cannot parse /home/user/inventory.ini, the command was run
from the wrong directory or with the wrong inventory path.

## 2. Determine whether Ansible is still running

Preparation:

~~~bash
pgrep -af 'ansible-playbook.*upgrade_prep.yaml'
~~~

Upgrade:

~~~bash
pgrep -af 'ansible-playbook.*upgrade_perform.yaml'
~~~

All related processes:

~~~bash
ps -eo pid,ppid,etime,stat,pcpu,pmem,args |
grep -E '[a]nsible-playbook|[s]cp|[s]shpass|[s]sh '
~~~

Interpretation:

- A parent ansible-playbook plus worker processes is normal.
- scp/sshpass/ssh during PREP 5/8 means firmware transfer is active.
- No matching process means the controller run ended or was disconnected.
- Never start another Stage 2 while one is running.

## 3. Preserve the terminal output

For future runs, capture the complete controller output:

~~~bash
mkdir -p reports

ansible-playbook upgrade_perform.yaml \
  -i inventory.ini \
  --ask-vault-pass \
  -e ha_pairs_parallel=1 |
tee "reports/controller-$(date -u +%Y%m%dT%H%M%SZ).log"
~~~

Because Vault input is read separately, tee does not record the Vault password.
Do not add verbose flags that could expose sensitive data without reviewing the
output handling.

## 4. Stage 1 appears stopped during upload

PREP 5/8 uses quiet SCP and no_log to avoid exposing the password environment.
A quiet screen during a multi-gigabyte upload is expected.

Check controller processes:

~~~bash
ps -eo pid,etime,stat,pcpu,pmem,args |
grep -E '[s]cp|[s]shpass|[s]sh '
~~~

Check active TCP sessions and counters:

~~~bash
ss -tinp | grep -A2 ':22'
~~~

Run the command again after 30 to 60 seconds. Increasing bytes_sent indicates
progress.

Check the destination file on selected appliances:

~~~bash
ansible -i inventory.ini \
  'ns_10_100_48_1:ns_10_100_48_2' \
  -m ansible.builtin.raw \
  -a "ssh_netscaler_adc shell 'ls -lh /var/nsinstall/build-14.1-73.33_nc_64.tgz 2>/dev/null; df -h /var; exit 0'" \
  --ask-vault-pass
~~~

Replace aliases and filename with values from inventory.ini and variables.yaml.
Investigate only when the process, TCP counters, and remote file size all remain
unchanged for a meaningful period.

## 5. Stage 1 appears stopped during checksum or extraction

Remote checksum and extraction can take several minutes after SCP exits.

Check archive, checksum, extracted installer, and space:

~~~bash
ansible -i inventory.ini ns_10_100_48_1 \
  -m ansible.builtin.raw \
  -a "ssh_netscaler_adc shell 'echo ===ARCHIVE===; ls -lh /var/nsinstall/build-14.1-73.33_nc_64.tgz 2>/dev/null; echo ===SHA256===; sha256 -q /var/nsinstall/build-14.1-73.33_nc_64.tgz 2>/dev/null; echo ===INSTALLNS===; ls -l /var/nsinstall/installns 2>/dev/null; echo ===SPACE===; df -h /var; exit 0'" \
  --ask-vault-pass
~~~

Compare controller checksum:

~~~bash
sha256sum new_firmware/build-14.1-73.33_nc_64.tgz
~~~

If checksums match, a Stage 1 rerun reuses the remote archive instead of
uploading it again. Extraction still runs again and is safe for the prepared
firmware directory.

## 6. Check Stage 1 reports and gate

~~~bash
LATEST_PREP_MD="$(ls -t reports/prep-*.md | head -n 1)"
LATEST_PREP_JSON="$(ls -t reports/prep-*.json | head -n 1)"

less "$LATEST_PREP_MD"
python3 -m json.tool "$LATEST_PREP_JSON" | less

grep -E \
  '^(prepared_firmware_successful|prepared_target_version|prepared_device_count|expected_device_count|prepared_at):' \
  prepared_firmware.yml
~~~

Common statuses:

- PREPARED: all Stage 1 checks passed.
- UNREACHABLE: TCP/22 or authenticated SSH failed.
- INSUFFICIENT_SPACE: parsed /var free space was below the threshold.
- FAILED: upload, checksum, extraction, installns validation, or marker failed.

Do not manually change prepared_firmware_successful to true.

## 7. Review standalone appliances

~~~bash
sed -n '/Standalone\/non-HA Console instances skipped/,/### Unreachable/p' \
  "$LATEST_PREP_MD"

jq '.skipped_non_ha_instances' "$LATEST_PREP_JSON"
~~~

A skipped standalone appliance is not an error and is not prepared or upgraded.
Use a separate standalone runbook. If it was expected to be HA, correct the HA
and Console state and rerun Stage 1. A missing is_ha_configured attribute is
treated as invalid Console data and stops discovery rather than being skipped.

## 8. Test connectivity and authentication

TCP/22 from the controller without loading inventory or Vault:

~~~bash
nc -zv -w 3 10.100.48.1 22
~~~

Authenticated raw connection:

~~~bash
ansible -i inventory.ini ns_10_100_48_1 \
  -m ansible.builtin.raw \
  -a 'echo CONNECTION_OK' \
  --ask-vault-pass
~~~

CLI command routing:

~~~bash
ansible -i inventory.ini ns_10_100_48_1 \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ns version' \
  --ask-vault-pass
~~~

If ping succeeds but Ansible reports an incorrect password, the NSIP is
reachable and authentication is the problem. Edit Vault and retest:

~~~bash
ansible-vault edit group_vars/all/vault.yml
~~~

## 9. Stage 2 appears stopped on installns

The current workflow runs ./installns -Y -n synchronously. The controller task
does not advance until installns returns.

Check all three original Secondaries:

~~~bash
ansible -i inventory.ini \
  'ns_10_100_48_2:ns_10_100_49_2:ns_10_100_50_2' \
  -m ansible.builtin.raw \
  -a "ssh_netscaler_adc shell 'echo ===PROCESS===; ps -axo pid,etime,stat,comm,args | grep [i]nstallns; echo ===INSTALL_STATE===; tail -n 30 /var/nsinstall/installns_state 2>/dev/null; echo ===BOOT_LOADER===; grep kernel /flash/boot/loader.conf 2>/dev/null; echo ===TARGET_KERNEL===; ls -ld /flash/ns-* 2>/dev/null; echo ===DISK===; df -h /flash /var; exit 0'" \
  --ask-vault-pass
~~~

Interpretation:

- installns process present: wait; do not run another installer or reboot.
- loader.conf points at target and target files exist: target is staged.
- END_TIME in installns_state normally indicates native installer completion.
- No process, old loader, and no install state: inspect the controller failure.
- No process with target staged: a retry can skip installns and continue with
  the controlled reboot.

The project does not create remote .rc, .pid, background log, or
.ansible_async tracking files.

## 10. Read controller-side install evidence

~~~bash
ls -lt reports/upgrade-*-installns.txt 2>/dev/null
less "$(ls -t reports/upgrade-*-installns.txt 2>/dev/null | head -n 1)"
~~~

The evidence records version before, whether the target was previously staged,
installns return code, stdout, and stderr. It exists only after that evidence
task completed.

## 11. Determine whether reboot started

Observe TCP/22 from the controller:

~~~bash
watch -n 5 'date; nc -zv -w 3 10.100.48.2 22'
~~~

The result changes from success to failure when SSH stops and back to success
when the port returns. Press Ctrl+C to stop watch. This check does not load
inventory or Vault and does not change the appliance.

TCP/22 open does not prove NetScaler services are ready. The playbook waits an
additional stabilization period and then polls authenticated CLI.

## 12. Check versions and local HA state

All HA nodes:

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

One pair:

~~~bash
ansible -i inventory.ini \
  'ns_10_100_48_1:ns_10_100_48_2' \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ha node 0' \
  --ask-vault-pass
~~~

Always use show ha node 0 for local-role validation. show ha node without an ID
can list both nodes and contains both Primary and Secondary strings.

## 13. Understand HA control states

During a normal pair upgrade, both local nodes temporarily show disabled sync
and propagation. After successful completion they should show:

- Original Primary: Master State Primary, Node State UP.
- Original Secondary: Master State Secondary, Node State UP.
- Propagation ENABLED.
- Sync State ENABLED or SUCCESS.

If versions differ, do not force HA synchronization and do not blindly enable
propagation. The playbook intentionally reports MANUAL_RECOVERY_REQUIRED and
retains isolation when it cannot prove that versions match.

## 14. Safe observation before manual recovery

Collect all evidence first:

~~~bash
ansible -i inventory.ini \
  'ns_10_100_48_1:ns_10_100_48_2' \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ns version' \
  --ask-vault-pass

ansible -i inventory.ini \
  'ns_10_100_48_1:ns_10_100_48_2' \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc show ha node 0' \
  --ask-vault-pass
~~~

Confirm:

1. Both nodes are reachable through authenticated CLI.
2. Both running versions are known.
3. Original local roles are known.
4. Both nodes are UP.
5. No installns process is active.
6. The report and hypervisor console have been preserved.

Do not execute the state-changing commands in the next section unless both
versions match and the original roles are confirmed.

## 15. Restore HA controls only when proven safe

The following commands change appliance configuration. Use them only after the
checks above prove equal versions and original roles.

~~~bash
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

Only after both nodes confirm restored controls should synchronization be
forced from the original Primary:

~~~bash
ansible -i inventory.ini ns_10_100_48_1 \
  -m ansible.builtin.raw \
  -a 'ssh_netscaler_adc force ha sync' \
  --ask-vault-pass
~~~

Do not run force ha sync when versions are mixed, a node is unreachable, or
roles are ambiguous.

## 16. Final synchronization reports no Secondary response

Collect local HA state from both nodes. Confirm the Secondary is reachable, UP,
and on the same version. Check HA/RPC connectivity and appliance events.

Do not keep repeating force ha sync. A timeout after controls were restored is
reported as FAILED so the operator can investigate the HA communication path.

The current code does not force synchronization before isolation. If the
terminal shows Force a clean synchronization before isolation, the controller
is running an older checkout:

~~~bash
git pull --ff-only
git log -1 --oneline
~~~

## 17. VM console loops on CAM/SCSI Busy

Messages such as:

~~~text
CAM status: SCSI Status Error
SCSI status: Busy
Retrying command
~~~

are guest storage errors, not Ansible progress messages. Capture the console
and involve the hypervisor/storage team. The playbook does not send a second
automatic reboot. In production, do not hide a recurring storage fault by only
increasing Ansible timeouts.

## 18. NetScaler Python or libcrypto failure

Do not install Python packages or replace system libraries on the appliance.
The maintained preparation and installation path uses raw SSH and native
NetScaler tools. Verify the repository and collection versions:

~~~bash
git log -1 --oneline
ansible-galaxy collection list netscaler.adc
~~~

## 19. Snapshot restoration

After restoring any appliance snapshot:

1. Stop any controller run that still assumes the newer state.
2. Validate live versions, roles, and HA controls.
3. Rerun Stage 1 for the fleet.
4. Review the new inventory, checksums, report, and preparation gate.
5. Start Stage 2 only after the new gate succeeds.

An old prepared_firmware.yml cannot prove the restored VM still contains the
archive, extracted files, marker, loader state, or topology that it records.

## 20. Read the latest upgrade report

~~~bash
LATEST_UPGRADE_MD="$(ls -t reports/upgrade-*.md | head -n 1)"
LATEST_UPGRADE_JSON="$(ls -t reports/upgrade-*.json | head -n 1)"

less "$LATEST_UPGRADE_MD"

grep -nEi -A12 -B3 'FAILED|MANUAL_RECOVERY|Failures|error' \
  "$LATEST_UPGRADE_MD"

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

An error such as ansible.builtin.raw: non-zero return code may not contain the
complete appliance stdout. Correlate it with the captured controller log,
per-node evidence files, versions, HA state, and the hypervisor console.

## 21. Report shows unknown versions

Unknown -> unknown normally means the pair failed before the node-upgrade task
recorded its before/after versions. Check the terminal log for STEP 02 failures:

- Preflight failure.
- Stale role compared with inventory.
- Node State not UP.
- Starting versions differ.
- HA isolation command or verification failure.

This does not by itself prove installns or reboot occurred. Review the exact
failed task and live state.

## 22. Stage 2 final assertion fails

The final assertion intentionally returns nonzero when any pair is FAILED or
MANUAL_RECOVERY_REQUIRED. Reports are written first.

~~~bash
ls -lt reports/upgrade-* | head
less "$(ls -t reports/upgrade-*.md | head -n 1)"
~~~

Do not treat the assertion itself as the root cause. Read the pair failure
record and controller output.

## 23. When to rerun Stage 1

Rerun Stage 1 after:

- Firmware or target-version changes.
- Console topology or HA-role changes.
- inventory.ini modification.
- A VM snapshot restore.
- Missing archive, extracted installer, or readiness metadata.
- Any failed or incomplete Stage 1.

A normal Stage 2 failure does not automatically require Stage 1 again when the
inventory, target, remote prepared files, and preparation gate remain valid.

## 24. What this project does not recover automatically

The project does not repair:

- Hypervisor or storage failures.
- Corrupted boot volumes.
- Unsupported firmware paths.
- Application-specific failures.
- License, certificate, routing, interface, GSLB, Gateway, or WAF problems.
- Standalone appliance upgrades.
- Backups or rollback points.

Escalate those conditions through the appropriate NetScaler, platform, network,
security, or application support process.

## 25. Disclaimer

These troubleshooting steps are provided on a best-effort, as-is basis. They
do not guarantee recovery and do not replace vendor support or an approved
change/rollback plan. There is no commitment to future support, updates, or bug
fixes. See [DISCLAIMER.md](DISCLAIMER.md).
