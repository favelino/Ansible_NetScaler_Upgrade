# NetScaler HA upgrade with dynamic inventory

This repository extends the NetScaler Automation Toolkit **normal-mode HA upgrade**
with live NetScaler Console discovery, bounded parallel execution by HA pair,
progress feedback, final validation, and JSON/Markdown reports.

Source template: [`netscaler/automation-toolkit`](https://github.com/netscaler/automation-toolkit/tree/main/golden_templates/upgrade-netscaler/high-availability/normal-mode).

## Safety model

Every generated HA pair becomes one Ansible job. Within a pair, the order is fixed:

1. Confirm both NSIPs passed preflight.
2. Upgrade and validate the original Secondary.
3. Fail over to the upgraded Secondary.
4. Upgrade and validate the original Primary.
5. Fail back and restore the original roles.
6. Verify both versions and both final HA roles.

Different pairs may run concurrently. `ha_pairs_parallel` controls the maximum
number of complete pairs in flight; it never parallelizes the two nodes within the
same pair.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
ansible-galaxy collection install git+https://github.com/citrix/citrix-adc-ansible-modules.git#/ansible-collections/adc
```

The upstream template documents Ansible 4.9.0. Test your installed Ansible and
collection versions against a non-production HA pair first.

## Generate the inventory from NetScaler Console

The generator calls:

```text
GET https://<console>/nitro/v2/config/ns
```

It handles pagination and requests only `hostname`, `ns_ip_address`,
`ha_ip_address`, `ha_master_state`, `ha_sync`, `instance_state`, and
`is_ha_configured`. It refuses to write an inventory unless:

- Every instance is `Up` and configured for HA.
- Primary sync is `ENABLED`; Secondary sync is `SUCCESS`.
- Every peer exists and points back to its partner.
- Every pair contains exactly one Primary and one Secondary.

```bash
python3 generate_netscaler_inventory.py \
  --console-url https://console.example.com \
  --console-user automation-api \
  --ca-bundle /path/to/company-ca.pem \
  --output inventory.ini
```

The password is requested without echoing. For automation, inject
`NETSCALER_CONSOLE_USER` and `NETSCALER_CONSOLE_PASS` from a protected secrets
store. `--insecure` disables TLS verification and is intended only for a lab.
Existing output is protected unless `--force` is supplied.

The generated inventory contains real NetScaler hosts plus synthetic pair hosts:

```ini
[netscaler_nodes]
ns_192_0_2_10 ansible_host=192.0.2.10 nsip=192.0.2.10 netscaler_hostname="lab-ns-primary" validate_certs=no
ns_192_0_2_11 ansible_host=192.0.2.11 nsip=192.0.2.11 netscaler_hostname="lab-ns-secondary" validate_certs=no

[netscaler_ha_pairs]
pair_001 pair_name="lab-ns-primary / lab-ns-secondary" primary_host=ns_192_0_2_10 secondary_host=ns_192_0_2_11 primary_nsip=192.0.2.10 secondary_nsip=192.0.2.11
```

## Store credentials with Ansible Vault

Never place NetScaler or Console passwords in `inventory.ini`.

```bash
mkdir -p group_vars/all
ansible-vault create group_vars/all/vault.yml
```

Store this schema in the encrypted file:

```yaml
vault_nitro_user: nsroot
vault_nitro_pass: "replace-with-the-real-password"
```

`group_vars/all/main.yml` maps those encrypted values to the variables required by
the playbook. Local `vault.yml` and common Vault password-file names are ignored by
Git. Never commit a plaintext Vault password file. For enterprise automation, use
a dedicated least-privilege account and an external secrets lookup.

## Configure the build and concurrency

Edit `variables.yaml` for the target build and controller paths. The concurrency
default is:

```yaml
ha_pairs_parallel: 2
```

You can override it for one execution. This example upgrades at most five complete
HA pairs simultaneously:

```bash
ansible-playbook ha_upgrade.yaml \
  -i inventory.ini \
  --ask-vault-pass \
  -e ha_pairs_parallel=5
```

Ansible `forks` must be at least as large as the desired concurrency. This
repository defaults to 50 forks in `ansible.cfg`; override with `--forks` when
required.

## Live progress and reachability feedback

Before upgrading, the playbook checks TCP/22 on every NSIP and prints a fleet
summary such as:

```text
Connecting to 50 devices: 47 reachable, 3 not reachable.
Unreachable devices: lab-ns-03 (192.0.2.13), lab-ns-08 (192.0.2.18), lab-ns-21 (192.0.2.31).
```

A pair with either node unreachable is marked `FAILED` and skipped. Other healthy
pairs continue. During execution, task names contain the pair and current step:

```text
STEP 03-05/12 | lab-ns-primary / lab-ns-secondary | Run installns on Original Secondary
STEP 06/12    | lab-ns-primary / lab-ns-secondary | Fail over to upgraded Secondary
STEP 12/12    | lab-ns-primary / lab-ns-secondary | Validate versions and restored roles
```

`installns` uses Ansible async polling, so the controller continues to show polling
feedback instead of appearing idle during the long installation step. Secret-bearing
commands use `no_log` so credentials do not appear in progress output.

## Reports and final result

Every run writes two control-node artifacts under `reports/`:

- `upgrade-<UTC-run-id>.json` for automation and ingestion.
- `upgrade-<UTC-run-id>.md` for a readable audit report.

Each pair record includes original NSIPs, versions before and after, target version,
duration, status, and failure detail. A pair is `SUCCESS` only after both nodes match
the target version and `show ha node` confirms the original Primary and Secondary
roles were restored.

The final console message reports succeeded and failed pair counts. The playbook
returns a non-zero exit code if any pair fails final validation, after writing both
reports.

## Test without calling Console

```bash
python3 -m unittest -v
python3 generate_netscaler_inventory.py \
  --input-json tests/console_response.json \
  --output /tmp/inventory.ini
```

Generate the inventory immediately before the upgrade. Do not reuse it after a
failover or topology change because the recorded Primary/Secondary roles may be stale.
