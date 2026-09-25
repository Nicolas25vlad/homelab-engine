#!/usr/bin/env python3
"""Small SSH-based inventory and safe service reconciler for homelabs."""

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None


ENGINE = Path(__file__).resolve().parents[1]
DOMAIN_SCHEMA = ENGINE / "schemas" / "domain.schema.json"
SECRET_VALUE = re.compile(r"(?i)\b(?:password|secret|token|api[_-]?key|private[_-]?key)\s*[=:]\s*[^$<{\s][^\s,;]*")
PROTECTED_UNITS = {"sshd.service", "systemd-networkd.service", "systemd-resolved.service", "firewalld.service", "docker.service", "containerd.service", "tailscaled.service"}


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def config_path(value):
    value = value or os.environ.get("HOMELAB_CONFIG")
    if not value:
        raise ValueError("pass --config or set HOMELAB_CONFIG to the private config repository")
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"configuration directory not found: {path}")
    return path


def load_model(config):
    if Draft202012Validator is None:
        raise ValueError("install engine requirements with: python3 -m pip install -r requirements.txt")
    schema = load_json(DOMAIN_SCHEMA)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    domains = []
    names = set()
    runtime_ids = set()
    for path in sorted((config / "domains").glob("*/domain.json")):
        domain = load_json(path)
        errors = sorted(validator.iter_errors(domain), key=lambda item: list(item.path))
        if errors:
            raise ValueError(f"{path}: {errors[0].message}")
        if domain["name"] in names:
            raise ValueError(f"duplicate domain name: {domain['name']}")
        names.add(domain["name"])
        service_names = set()
        for service in domain["services"]:
            if service["name"] in service_names:
                raise ValueError(f"{path}: duplicate service name {service['name']}")
            service_names.add(service["name"])
            identity = (service["kind"], service["runtime_id"])
            if identity in runtime_ids:
                raise ValueError(f"{path}: runtime already declared: {service['runtime_id']}")
            runtime_ids.add(identity)
            if service["managed"]:
                source = (config / service["source_file"]).resolve()
                if config.resolve() not in source.parents or not source.is_file():
                    raise ValueError(f"{path}: managed service source_file is missing or outside config: {service['source_file']}")
        if SECRET_VALUE.search(path.read_text(encoding="utf-8")):
            raise ValueError(f"{path}: looks like a secret value; declare names only")
        domains.append(domain)
    if not domains:
        raise ValueError(f"no domain definitions under {config / 'domains'}")
    hosts = load_json(config / "inventory" / "hosts.json")
    target = hosts.get("host", {}).get("ssh_target", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+", target):
        raise ValueError("inventory/hosts.json needs a simple user@host ssh_target")
    return hosts, domains


def remote_json(hosts, helper):
    target = hosts["host"]["ssh_target"]
    source = (ENGINE / "scripts" / helper).read_text(encoding="utf-8")
    try:
        result = subprocess.run(
            ["ssh", "-T", target, "python3", "-"],
            input=source,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"could not run ssh: {exc}") from exc
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"remote command failed with exit {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("remote inventory returned invalid JSON") from exc


def remote_python_command(source):
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    runner = "import base64,sys; exec(base64.b64decode(sys.argv[1]))"
    return "python3 -c " + shlex.quote(runner) + " " + shlex.quote(encoded)


def secret_declarations(domains, selected=None):
    requests = []
    unchecked = []
    selected = set(selected) if selected is not None else None
    for domain in domains:
        for service in domain["services"]:
            identifier = domain["name"] + "/" + service["name"]
            if selected is not None and identifier not in selected:
                continue
            secret_set = service.get("secrets", domain.get("secrets", {"provider": "none"}))
            provider = secret_set.get("provider")
            if provider == "env-file":
                requests.append({"id": identifier, "path": secret_set["path"], "required": secret_set["required"], "optional": secret_set["optional"]})
            elif provider not in {"none", None}:
                unchecked.append((identifier, provider))
    return requests, unchecked


def check_env_files(hosts, requests):
    if not requests:
        return {}
    source = (ENGINE / "scripts" / "remote_secrets.py").read_text(encoding="utf-8")
    result = subprocess.run(
        ["ssh", "-T", hosts["host"]["ssh_target"], remote_python_command(source)],
        input=json.dumps(requests), text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "remote secret check failed")
    return json.loads(result.stdout)


def secrets_ready(secret_set, values):
    provider = secret_set.get("provider", "none")
    if provider == "none":
        return True
    if provider != "env-file":
        return False
    return all(values.get(name) == "OK" for name in secret_set.get("required", []))


def actual_state(service, actual):
    if service["kind"] == "compose":
        container = next((item for item in actual["docker"]["containers"] if item["name"] == service["runtime_id"]), None)
        if not container:
            return "missing"
        status = container["status"].lower()
        return "running" if status.startswith("up ") else "stopped" if status.startswith("exited") else status
    unit = service["unit"]
    if unit.endswith(".timer"):
        value = actual["systemd"]["timers"].get(unit, "missing")
        return {"active": "running", "waiting": "running", "failed": "failed", "inactive": "stopped"}.get(value, value)
    value = actual["systemd"]["services"].get(unit, "missing")
    return {"active": "running", "running": "running", "failed": "failed", "inactive": "stopped", "dead": "stopped", "exited": "stopped"}.get(value, value)


def service_action(domain, service, actual):
    desired = service.get("state", domain["state"])
    observed = actual_state(service, actual)
    if not service["managed"]:
        return {"domain": domain["name"], "service": service["name"], "kind": service["kind"], "runtime_id": service["runtime_id"], "managed": False, "desired": desired, "actual": observed, "action": "none", "result": "unmanaged", **{key: service[key] for key in ("compose_file", "compose_service", "unit", "source_file") if key in service}}
    should_run = desired == "running"
    enabled = service["kind"] == "systemd" and service["unit"] in actual["systemd"]["enabled_units"]
    action = "start" if should_run and (observed != "running" or not enabled) else "stop" if not should_run and (observed == "running" or enabled) else "none"
    if action != "none" and service["kind"] == "systemd" and service["unit"] in PROTECTED_UNITS:
        action = "none"
        result = "manual-review"
    else:
        result = action if action != "none" else "unchanged"
    return {"domain": domain["name"], "service": service["name"], "kind": service["kind"], "runtime_id": service["runtime_id"], "managed": True, "desired": desired, "actual": observed, "action": action, "result": result, **{key: service[key] for key in ("compose_file", "compose_service", "unit", "source_file") if key in service}}


def make_plan(domains, actual):
    rows = [service_action(domain, service, actual) for domain in domains for service in domain["services"]]
    declared = {row["runtime_id"] for row in rows if row["kind"] == "compose"}
    unknown = [item["name"] for item in actual["docker"]["containers"] if item["name"] not in declared]
    declared_units = {row["unit"] for row in rows if row["kind"] == "systemd"}
    unknown_units = [unit for unit in actual["systemd"].get("custom_units", []) if unit not in declared_units]
    return {"services": rows, "unknown_containers": unknown, "unknown_systemd_units": unknown_units, "summary": {"start": sum(row["action"] == "start" for row in rows), "stop": sum(row["action"] == "stop" for row in rows), "unchanged": sum(row["result"] == "unchanged" for row in rows), "unmanaged": sum(row["result"] == "unmanaged" for row in rows), "manual_review": sum(row["result"] == "manual-review" for row in rows), "unknown": len(unknown) + len(unknown_units), "destructive": 0}}


def show_inventory(actual, as_json=False):
    if as_json:
        print(json.dumps(actual, indent=2))
        return
    host = actual["host"]
    gib = 1024 ** 3
    print("HOMELAB INVENTORY")
    print(f"Host: {host['name']} | {host['os']} | {host['kernel']} | {host['cpu_cores']} CPU")
    print(f"Memory: {host['memory_total_bytes'] / gib:.1f} GiB total, {host['memory_available_bytes'] / gib:.1f} GiB available")
    print(f"Root disk: {host['root_disk_used_bytes'] / gib:.1f}/{host['root_disk_total_bytes'] / gib:.1f} GiB used")
    print(f"Systemd: {len(actual['systemd']['running_services'])} running services, {len(actual['systemd']['enabled_units'])} enabled units, {len(actual['systemd']['timers'])} timers")
    print(f"Docker: {len(actual['docker']['containers'])} containers, {len(actual['docker']['volumes'])} volumes, {len(actual['docker']['networks'])} networks")
    print("Containers:")
    for item in actual["docker"]["containers"]:
        used = item.get("observed_memory_bytes")
        memory = f"{used / (1024 ** 2):.1f} MiB" if used is not None else "memory n/a"
        cpu = item.get("observed_cpu_percent")
        cpu_text = f"{cpu:.2f}%" if cpu is not None else "CPU n/a"
        print(f"  {item['name']}: {item['image']} ({item['status']}) {cpu_text}, {memory}; ports {item['ports']}")
    print("Listening ports:")
    for item in actual["network"]["listening_ports"]:
        print(f"  {item['protocol']} {item['address']}:{item['port']}")


def make_actions(plan):
    return [row for row in plan["services"] if row["action"] in {"start", "stop"}]


def print_plan(plan):
    print("HOMELAB PLAN (read-only)")
    for row in plan["services"]:
        print(f"{row['domain']}/{row['service']}: desired={row['desired']} actual={row['actual']} managed={str(row['managed']).lower()} result={row['result']}")
    for name in plan["unknown_containers"]:
        print(f"unknown container: {name} (unmanaged workload)")
    for unit in plan["unknown_systemd_units"]:
        print(f"unknown systemd unit: {unit} (potential unmanaged workload)")
    summary = plan["summary"]
    print(f"SUMMARY: {summary['start']} start, {summary['stop']} stop, {summary['unchanged']} unchanged, {summary['unmanaged']} unmanaged, {summary['manual_review']} manual review, {summary['unknown']} unknown, {summary['destructive']} destructive")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="path to the private homelab-config checkout")
    sub = parser.add_subparsers(dest="command", required=True)
    inventory_cmd = sub.add_parser("inventory")
    inventory_cmd.add_argument("--json", action="store_true")
    inventory_cmd.add_argument("--save", action="store_true", help="save a secret-free snapshot under inventory/snapshots")
    sub.add_parser("plan")
    sub.add_parser("apply")
    sub.add_parser("validate")
    sub.add_parser("secrets")
    args = parser.parse_args()

    try:
        config = config_path(args.config)
        hosts, domains = load_model(config)
        if args.command == "validate":
            print(f"valid: {len(domains)} domains")
            observed = config / "inventory" / "observed.json"
            if observed.exists():
                capacity = load_json(observed).get("host", {}).get("memory_gib")
                reservations = sum(domain.get("resources", {}).get("memory_mib", {}).get("reserved") or 0 for domain in domains)
                if capacity is not None and reservations > capacity * 1024:
                    print(f"warning: declared memory reservations {reservations} MiB exceed host capacity {capacity} GiB")
            return 0
        if args.command == "secrets":
            requests, unchecked = secret_declarations(domains)
            if not requests and not unchecked:
                print("No env-file secret declarations.")
                return 0
            for identifier, provider in unchecked:
                print(f"{identifier}: {provider} integration is not configured")
            for identifier, values in check_env_files(hosts, requests).items():
                for name, status in values.items():
                    print(f"{identifier} {name}: {status}")
            return 0
        actual = remote_json(hosts, "remote_inventory.py")
        if args.command == "inventory":
            show_inventory(actual, args.json)
            if args.save:
                folder = config / "inventory" / "snapshots"
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{date.today().isoformat()}.json"
                target.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
                print(f"snapshot saved: {target}")
            return 0
        plan = make_plan(domains, actual)
        print_plan(plan)
        if args.command == "plan":
            return 0
        actions = make_actions(plan)
        if not actions:
            print("No managed service changes to apply.")
            return 0
        starts = {row["domain"] + "/" + row["service"] for row in actions if row["action"] == "start"}
        requests, unchecked = secret_declarations(domains, starts)
        if unchecked:
            raise ValueError("refusing start until secret providers are configured: " + ", ".join(identifier for identifier, _ in unchecked))
        reports = check_env_files(hosts, requests)
        for domain in domains:
            for service in domain["services"]:
                identifier = domain["name"] + "/" + service["name"]
                if identifier not in starts:
                    continue
                secret_set = service.get("secrets", domain.get("secrets", {"provider": "none"}))
                if not secrets_ready(secret_set, reports.get(identifier, {})):
                    raise ValueError(f"refusing start because required secret keys are missing or unreadable: {identifier}")
        source = (ENGINE / "scripts" / "remote_apply.py").read_text(encoding="utf-8")
        result = subprocess.run(
            ["ssh", "-T", hosts["host"]["ssh_target"], remote_python_command(source)],
            input=json.dumps(actions),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise ValueError(result.stderr.strip() or "remote apply failed")
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        actual = remote_json(hosts, "remote_inventory.py")
        verified = make_plan(domains, actual)
        failed = [row for row in verified["services"] if row["domain"] + "/" + row["service"] in {item["domain"] + "/" + item["service"] for item in actions} and row["result"] not in {"unchanged", "unmanaged"}]
        if failed:
            raise ValueError("post-apply verification failed: " + ", ".join(row["domain"] + "/" + row["service"] for row in failed))
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
