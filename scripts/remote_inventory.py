#!/usr/bin/env python3
"""Read-only host inventory. Deliberately excludes all environment values."""

import json
import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path


def command(args):
    env = os.environ.copy()
    env.update({"SYSTEMD_COLORS": "0", "SYSTEMD_PAGER": "cat"})
    result = subprocess.run(args, text=True, capture_output=True, check=False, env=env)
    return result.stdout.strip() if result.returncode == 0 else ""


def lines(args):
    return command(args).splitlines()


def memory_bytes(value):
    match = re.fullmatch(r"\s*([0-9.]+)\s*([kmgt]?i?b)\s*", value, re.IGNORECASE)
    if not match:
        return None
    unit = match[2].lower()
    powers = {"b": 0, "kb": 1, "kib": 1, "mb": 2, "mib": 2, "gb": 3, "gib": 3, "tb": 4, "tib": 4}
    base = 1024 if unit.endswith("ib") else 1000
    return int(float(match[1]) * base ** powers[unit])


def units(args, state_index):
    result = {}
    for line in lines(args):
        fields = line.split()
        if len(fields) > state_index:
            result[fields[0]] = fields[state_index]
    return result


def containers():
    result = []
    stats = {}
    for line in lines(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"]):
        name, separator, detail = line.partition("|")
        cpu, separator2, memory = detail.partition("|")
        used = memory.partition("/")[0].strip() if separator and separator2 else ""
        try:
            cpu_value = float(cpu.strip().rstrip("%"))
        except ValueError:
            cpu_value = None
        stats[name] = {"cpu_percent": cpu_value, "memory_used_bytes": memory_bytes(used)}
    for line in lines(["docker", "ps", "-a", "--format", "{{json .}}"]):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = row.get("Names", "")
        detail = command([
            "docker", "inspect", "--format",
            '{{.HostConfig.RestartPolicy.Name}}|{{.HostConfig.Memory}}|{{.HostConfig.NanoCpus}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{range .Mounts}}{{.Type}}:{{.Source}}=>{{.Destination}};{{end}}',
            name,
        ]).split("|", 5)
        detail += [""] * (6 - len(detail))
        result.append({
            "name": name,
            "image": row.get("Image", ""),
            "status": row.get("Status", ""),
            "ports": row.get("Ports", ""),
            "restart": detail[0],
            "memory_limit_bytes": int(detail[1]) if detail[1].isdigit() and int(detail[1]) > 0 else None,
            "cpu_limit_nanos": int(detail[2]) if detail[2].isdigit() and int(detail[2]) > 0 else None,
            "compose_project": detail[3],
            "compose_service": detail[4],
            "mounts": [item for item in detail[5].split(";") if item],
            "observed_cpu_percent": stats.get(name, {}).get("cpu_percent"),
            "observed_memory_bytes": stats.get(name, {}).get("memory_used_bytes"),
        })
    return result


def listening_ports():
    result = []
    for line in lines(["ss", "-H", "-lntu"]):
        fields = line.split()
        if len(fields) < 5:
            continue
        local = fields[4]
        host, separator, port = local.rpartition(":")
        if separator and port.isdigit():
            result.append({"protocol": fields[0], "address": host.strip("[]"), "port": int(port)})
    return sorted(result, key=lambda item: (item["port"], item["protocol"], item["address"]))


def main():
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')

    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            memory[key] = int(value.split()[0]) * 1024

    running_services = units(
        ["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--no-pager", "--plain"], 2
    )
    all_services = units(
        ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"], 2
    )
    enabled_units = {}
    for line in lines(["systemctl", "list-unit-files", "--state=enabled", "--no-legend", "--no-pager", "--plain"]):
        fields = line.split()
        if len(fields) > 1:
            enabled_units[fields[0]] = fields[1]
    timers = units(
        ["systemctl", "list-units", "--type=timer", "--all", "--no-legend", "--no-pager", "--plain"], 2
    )
    try:
        custom_units = sorted(path.name for path in Path("/etc/systemd/system").iterdir() if not path.is_symlink() and path.is_file() and path.suffix in {".service", ".timer"})
    except OSError:
        custom_units = []

    disk = shutil.disk_usage("/")
    uptime = float(Path("/proc/uptime").read_text().split()[0])
    podman = shutil.which("podman")
    cron = subprocess.run(["crontab", "-l"], text=True, capture_output=True, check=False)
    cron_count = sum(1 for item in cron.stdout.splitlines() if item.strip() and not item.lstrip().startswith("#")) if cron.returncode == 0 else None

    print(json.dumps({
        "apiVersion": "homelab.inventory.v1",
        "host": {
            "name": socket.gethostname(),
            "os": release.get("PRETTY_NAME", platform.system()),
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "cpu_cores": os.cpu_count(),
            "uptime_seconds": int(uptime),
            "memory_total_bytes": memory.get("MemTotal"),
            "memory_available_bytes": memory.get("MemAvailable"),
            "root_disk_total_bytes": disk.total,
            "root_disk_used_bytes": disk.used,
            "root_disk_free_bytes": disk.free,
        },
        "network": {"listening_ports": listening_ports()},
        "systemd": {
            "services": all_services,
            "running_services": sorted(running_services),
            "enabled_units": enabled_units,
            "timers": timers,
            "custom_units": custom_units,
        },
        "docker": {
            "available": bool(shutil.which("docker")),
            "containers": containers() if shutil.which("docker") else [],
            "networks": lines(["docker", "network", "ls", "--format", "{{.Name}}"]),
            "volumes": lines(["docker", "volume", "ls", "--format", "{{.Name}}"]),
        },
        "podman": {"available": bool(podman)},
        "user_cron_entries": cron_count,
    }, indent=2))


if __name__ == "__main__":
    main()
