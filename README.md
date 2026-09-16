# NetScaler HA upgrade with dynamic inventory

This repository contains the NetScaler Automation Toolkit **normal-mode HA upgrade**
template plus a generator that builds `inventory.ini` from the live HA state reported
by NetScaler Console.

Source template: [`netscaler/automation-toolkit`](https://github.com/netscaler/automation-toolkit/tree/main/golden_templates/upgrade-netscaler/high-availability/normal-mode).

## What the generator does

`generate_netscaler_inventory.py` calls:

```text
GET https://<console>/nitro/v2/config/ns
```

It requests only `hostname`, `ns_ip_address`, `ha_ip_address`,
`ha_master_state`, `ha_sync`, `instance_state`, and `is_ha_configured`. Before
writing the inventory it verifies that:

- Every instance is reachable (`Up`).
- HA is configured.
- HA sync matches Console's healthy role-specific state: Primary is `ENABLED` and Secondary is `SUCCESS`.
- Every peer is present and points back to its partner.
- Every pair has exactly one Primary and one Secondary.

The resulting groups match `ha_upgrade.yaml`:

```ini
[primary_netscaler]
10.100.48.1 nsip=10.100.48.1 validate_certs=no

[secondary_netscaler]
10.100.48.2 nsip=10.100.48.2 validate_certs=no
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
ansible-galaxy collection install git+https://github.com/citrix/citrix-adc-ansible-modules.git#/ansible-collections/adc
```

The upstream template documents Ansible 4.9.0. Test newer Ansible versions in a
non-production HA pair before using them for an upgrade.

## Generate `inventory.ini`

The safest interactive method is to provide the Console username and let the
script prompt for its password without displaying it:

```bash
python3 generate_netscaler_inventory.py \
  --console-url https://console.example.com \
  --console-user automation-api \
  --output inventory.ini
```

Use a trusted CA bundle when Console uses a private CA:

```bash
python3 generate_netscaler_inventory.py \
  --console-url https://console.example.com \
  --console-user automation-api \
  --ca-bundle /path/to/company-ca.pem \
  --output inventory.ini
```

For non-interactive automation, inject `NETSCALER_CONSOLE_USER` and
`NETSCALER_CONSOLE_PASS` from your secrets platform. Do not place them in this
repository. `--insecure` is available for a lab but disables TLS verification.

If `inventory.ini` already exists, the generator refuses to replace it unless
you add `--force`.

## Store `nsroot` credentials safely

Do not put `nitro_pass` in `inventory.ini`. The playbook reads it as an Ansible
variable, so an encrypted `group_vars` file works without modifying the inventory.

```bash
mkdir -p group_vars/all
ansible-vault create group_vars/all/vault.yml
```

Enter this content in the Vault editor:

```yaml
vault_nitro_user: nsroot
vault_nitro_pass: "replace-with-the-real-password"
```

The committed `group_vars/all/main.yml` maps these encrypted values to the
`nitro_user` and `nitro_pass` variables expected by the upstream playbook.
The local `vault.yml` and common Vault password-file names are ignored by Git.
Never commit a plaintext Vault password file.

Run the upgrade with:

```bash
ansible-playbook ha_upgrade.yaml -i inventory.ini --ask-vault-pass
```

For enterprise automation, prefer a dedicated least-privilege NetScaler account
and retrieve its secret at runtime with the appropriate Ansible lookup plugin for
HashiCorp Vault, CyberArk, AWS Secrets Manager, or Azure Key Vault. Keep the
authentication token in the CI/CD platform's protected secret store.

## Configure the target build

Edit `variables.yaml`:

- `netscaler_build_location`: location on the NetScaler, including trailing `/`.
- `netscaler_build_file_name`: target build archive name.
- `netscaler_target_version`: release and build, for example `14.1-47.48`.
- `want_to_copy_build`: `yes` to upload from the controller, otherwise `no`.
- `local_build_file_full_path_with_name`: local archive path when copying.

The template also requires passwordless SSH between the controller and both
NetScalers.

## Test without calling Console

```bash
python3 -m unittest -v
python3 generate_netscaler_inventory.py \
  --input-json tests/console_response.json \
  --output /tmp/inventory.ini
```

Review the generated inventory and the current HA status immediately before an
upgrade. HA roles can change after the inventory is generated.

## Important operational note

The upstream workflow upgrades the current Secondary first, forces failover, and
then upgrades the old Primary. Run the inventory generator immediately before the
playbook; do not reuse a stale inventory after a failover or topology change.
