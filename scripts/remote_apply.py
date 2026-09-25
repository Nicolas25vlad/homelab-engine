#!/usr/bin/env python3
"""Apply only explicitly managed service start/stop actions; never delete data."""

import json
import subprocess
import sys
from pathlib import Path


def main():
    actions = json.load(sys.stdin)
    for action in actions:
        if not action.get("managed") or action.get("action") not in {"start", "update", "stop"}:
            raise SystemExit("refusing an unmanaged or unsupported action")
        kind = action["kind"]
        if kind == "compose":
            compose_file = Path(action["compose_file"])
            if not compose_file.is_absolute() or ".." in compose_file.parts or compose_file.name not in {"compose.yaml", "compose.yml"} or compose_file.is_symlink():
                raise SystemExit("compose_file path is invalid")
            command = ["docker", "compose", "--project-directory", str(compose_file.parent), "-f", str(compose_file)]
            old_content = compose_file.read_bytes() if compose_file.is_file() else None
            if action["action"] in {"start", "update"}:
                content = action.get("compose_content")
                if not isinstance(content, str) or not compose_file.parent.is_dir():
                    raise SystemExit("Compose content or target directory is invalid")
                try:
                    _replace_compose(compose_file, content.encode("utf-8"))
                except subprocess.CalledProcessError as exc:
                    raise SystemExit(exc.returncode) from exc
            command += ["up", "-d"] if action["action"] in {"start", "update"} else ["stop"]
            command.append(action["compose_service"])
        elif kind == "systemd":
            command = ["sudo", "-n", "systemctl"]
            command += ["enable", "--now"] if action["action"] == "start" else ["disable", "--now"]
            command.append(action["unit"])
        else:
            raise SystemExit(f"unsupported service kind: {kind}")
        result = subprocess.run(command, check=False)
        if result.returncode:
            if kind == "compose" and action["action"] in {"start", "update"}:
                if old_content is not None:
                    _replace_compose(compose_file, old_content)
                else:
                    subprocess.run(["sudo", "-n", "rm", "--", str(compose_file)], check=False)
            raise SystemExit(result.returncode)
        print(f"{action['domain']}/{action['service']}: {action['action']} applied")


def _replace_compose(path, content):
    exists = path.exists()
    old = path.stat() if exists else path.parent.stat()
    command = ["sudo", "-n", "python3", "-c", "import json,os,sys,tempfile; a=json.load(sys.stdin); p=a['path']; f=tempfile.NamedTemporaryFile(dir=os.path.dirname(p),delete=False); f.write(a['content'].encode()); f.flush(); os.fsync(f.fileno()); f.close(); os.chown(f.name,a['uid'],a['gid']); os.chmod(f.name,a['mode']); os.replace(f.name,p)"]
    result = subprocess.run(command, input=json.dumps({"path": str(path), "content": content.decode("utf-8"), "uid": old.st_uid, "gid": old.st_gid, "mode": old.st_mode & 0o777 if exists else 0o644}), text=True, check=False)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command)


if __name__ == "__main__":
    main()
