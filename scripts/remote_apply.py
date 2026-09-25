#!/usr/bin/env python3
"""Apply only explicitly managed service start/stop actions; never delete data."""

import json
import subprocess
import sys
from pathlib import Path


def main():
    actions = json.load(sys.stdin)
    for action in actions:
        if not action.get("managed") or action.get("action") not in {"start", "stop"}:
            raise SystemExit("refusing an unmanaged or unsupported action")
        kind = action["kind"]
        if kind == "compose":
            compose_file = Path(action["compose_file"])
            if not compose_file.is_absolute():
                raise SystemExit("compose_file must be an absolute path")
            command = ["docker", "compose", "--project-directory", str(compose_file.parent), "-f", str(compose_file)]
            command += ["up", "-d"] if action["action"] == "start" else ["stop"]
            command.append(action["compose_service"])
        elif kind == "systemd":
            command = ["sudo", "-n", "systemctl"]
            command += ["enable", "--now"] if action["action"] == "start" else ["disable", "--now"]
            command.append(action["unit"])
        else:
            raise SystemExit(f"unsupported service kind: {kind}")
        result = subprocess.run(command, check=False)
        if result.returncode:
            raise SystemExit(result.returncode)
        print(f"{action['domain']}/{action['service']}: {action['action']} applied")


if __name__ == "__main__":
    main()
