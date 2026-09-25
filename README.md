# Homelab Engine

Small, read-only-first tooling for inventorying and reconciling one homelab over SSH. Hostnames, addresses, paths, and enabled domains belong in a separate private configuration repository.

## Commands

Install the single runtime dependency and point the engine at the private checkout:

```sh
python3 -m pip install -r requirements.txt
export HOMELAB_CONFIG="$HOME/homelab-config"
python3 scripts/homelab.py validate
python3 scripts/homelab.py inventory
python3 scripts/homelab.py inventory --json
python3 scripts/homelab.py plan
python3 scripts/homelab.py apply
python3 scripts/homelab.py secrets
```

`inventory` and `plan` do not change the host. `inventory --save` writes a snapshot into the private repository; snapshots contain host and workload metadata, never environment values. `apply` only acts on services marked `managed: true` with an existing checked-in `source_file`, and supports start/stop while preserving volumes and files. `archived` has the same runtime effect as `stopped`; it never means delete.

Domains use `domains/<name>/domain.json` and are checked against [`schemas/domain.schema.json`](schemas/domain.schema.json). Set a service to `managed: true` only after its checked-in configuration matches the host. Unmanaged services are reported without action.

## Limits

The engine does not install packages, change network/firewall/SSH settings, rotate secrets, remove containers or volumes, or perform backups. Secret checks report variable names and presence only. There is no automatic rollback; restore a known-good Compose/systemd definition manually if a managed change fails.

Run checks with `python -m unittest discover -s tests`. The current workflows validate the engine only; deployment-specific validation runs against the private configuration checkout.
