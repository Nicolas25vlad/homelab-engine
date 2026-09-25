# Homelab Engine

- Keep deployment-specific hostnames, addresses, paths, and secrets in the private configuration repository.
- Inventory and plan are read-only. Never print environment values, secret values, or full container inspect output.
- Discovered services start with `managed: false`; unmanaged services are report-only.
- Apply may start or stop explicitly managed services. It must never remove volumes, delete data, or stop an unknown workload.
- Run `python -m unittest discover -s tests` and `python scripts/homelab.py --config <private-config> validate` before publishing changes.
