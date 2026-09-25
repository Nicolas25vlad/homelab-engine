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

`inventory` and `plan` do not change the host. `inventory --save` writes a snapshot into the private repository; snapshots contain host and workload metadata, never environment values. `apply` only acts on services marked `managed: true` with a checked-in `source_file`. It starts, stops, or updates only the declared Compose service, preserves named volumes and host `.env` files, and restores the previous Compose file if an update fails. `archived` has the same runtime effect as `stopped`; it never means delete.

Domains use `domains/<name>/domain.json` and are checked against [`schemas/domain.schema.json`](schemas/domain.schema.json). To add a Compose service, create its Compose YAML under the private config's `domains/<name>/`, then add a service entry with `kind: "compose"`, `managed: true`, the container `runtime_id`, host `compose_file`, exact `compose_service`, and Git-relative `source_file`. The host directory containing `compose_file` must already exist. Provision any required `.env` file on the host and declare its required key names; never commit secret values. Run `validate` and review `plan` before dispatching `apply`. A missing service is started from its checked-in Compose file; a changed file on a running service appears as `update` in the plan. Set an adopted service to `managed: true` only after its checked-in configuration matches the host. Unmanaged services are reported without action.

## Limits

The engine does not install packages, change network/firewall/SSH settings, rotate secrets, remove containers or volumes, or perform backups. Secret checks report variable names and presence only. Compose file changes roll back when `docker compose up` fails; systemd changes do not have automatic rollback.

Run checks with `python -m unittest discover -s tests`. The current workflows validate the engine only; deployment-specific validation runs against the private configuration checkout.
