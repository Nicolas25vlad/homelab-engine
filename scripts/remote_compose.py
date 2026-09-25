#!/usr/bin/env python3
"""Return SHA-256 hashes for explicitly requested remote Compose files."""

import hashlib
import json
import sys
from pathlib import Path


def main():
    paths = json.load(sys.stdin)
    if not isinstance(paths, list):
        raise SystemExit("expected a list of Compose paths")
    result = {}
    for value in paths:
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or path.name not in {"compose.yaml", "compose.yml"} or path.is_symlink():
            raise SystemExit("Compose path is invalid")
        try:
            result[value] = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            result[value] = None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
