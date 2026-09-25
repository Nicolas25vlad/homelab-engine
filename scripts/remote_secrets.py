#!/usr/bin/env python3
"""Report env-file key presence without returning or logging any values."""

import json
import sys
from pathlib import Path


def present_keys(text):
    result = set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, _ = line.partition("=")
        if separator and key and key.replace("_", "").isalnum() and key.upper() == key:
            result.add(key)
    return result


def main():
    report = {}
    for item in json.load(sys.stdin):
        try:
            keys = present_keys(Path(item["path"]).read_text(encoding="utf-8"))
            state = {name: "OK" if name in keys else "MISSING" for name in item["required"]}
            state.update({name: "OPTIONAL" if name in keys else "OPTIONAL/MISSING" for name in item["optional"]})
        except PermissionError:
            state = {name: "UNREADABLE" for name in item["required"]}
            state.update({name: "OPTIONAL/UNREADABLE" for name in item["optional"]})
        except OSError:
            state = {name: "MISSING" for name in item["required"]}
            state.update({name: "OPTIONAL/MISSING" for name in item["optional"]})
        report[item["id"]] = state
    print(json.dumps(report))


if __name__ == "__main__":
    main()
