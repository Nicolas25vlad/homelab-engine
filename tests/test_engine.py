import contextlib
import io
import json
import sys
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import homelab
import remote_inventory
import remote_apply


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.actual = {
            "docker": {"containers": [{"name": "db", "status": "Up 2 hours"}]},
            "systemd": {"services": {"worker.service": "failed"}, "timers": {}, "enabled_units": {}, "custom_units": []},
        }

    def test_unmanaged_drift_is_report_only(self):
        domain = {"name": "app", "state": "running"}
        service = {"name": "worker", "kind": "systemd", "runtime_id": "worker.service", "unit": "worker.service", "managed": False}
        row = homelab.service_action(domain, service, self.actual)
        self.assertEqual((row["actual"], row["action"], row["result"]), ("failed", "none", "unmanaged"))

    def test_managed_service_starts_when_missing(self):
        domain = {"name": "app", "state": "running"}
        service = {"name": "worker", "kind": "systemd", "runtime_id": "worker.service", "unit": "worker.service", "managed": True}
        row = homelab.service_action(domain, service, self.actual)
        self.assertEqual(row["action"], "start")

    def test_running_compose_service_is_unchanged(self):
        domain = {"name": "app", "state": "running"}
        service = {"name": "db", "kind": "compose", "runtime_id": "db", "compose_file": "/srv/db/compose.yaml", "compose_service": "db", "managed": True}
        self.actual["compose_files"] = {"/srv/db/compose.yaml": {"expected": "same", "actual": "same"}}
        row = homelab.service_action(domain, service, self.actual)
        self.assertEqual((row["action"], row["result"]), ("none", "unchanged"))

    def test_running_compose_service_updates_on_file_drift(self):
        domain = {"name": "app", "state": "running"}
        service = {"name": "db", "kind": "compose", "runtime_id": "db", "compose_file": "/srv/db/compose.yaml", "compose_service": "db", "managed": True}
        self.actual["compose_files"] = {"/srv/db/compose.yaml": {"expected": "new", "actual": "old"}}
        row = homelab.service_action(domain, service, self.actual)
        self.assertEqual((row["action"], row["result"]), ("update", "update"))

    def test_missing_managed_compose_service_starts(self):
        domain = {"name": "app", "state": "running"}
        service = {"name": "app", "kind": "compose", "runtime_id": "app-1", "compose_file": "/srv/app/compose.yaml", "compose_service": "app", "managed": True}
        self.actual["docker"]["containers"] = []
        self.actual["compose_files"] = {"/srv/app/compose.yaml": {"expected": "new", "actual": None}}
        row = homelab.service_action(domain, service, self.actual)
        self.assertEqual((row["action"], row["result"]), ("start", "start"))

    def test_compose_file_drift_waits_until_a_stopped_service_starts(self):
        domain = {"name": "app", "state": "stopped"}
        service = {"name": "db", "kind": "compose", "runtime_id": "db", "compose_file": "/srv/db/compose.yaml", "compose_service": "db", "managed": True}
        self.actual["docker"]["containers"][0]["status"] = "Exited (0)"
        self.actual["compose_files"] = {"/srv/db/compose.yaml": {"expected": "new", "actual": "old"}}
        row = homelab.service_action(domain, service, self.actual)
        self.assertEqual((row["action"], row["result"]), ("none", "config-drift"))

    def test_protected_systemd_unit_requires_manual_review(self):
        domain = {"name": "core", "state": "stopped"}
        service = {"name": "ssh", "kind": "systemd", "runtime_id": "sshd.service", "unit": "sshd.service", "managed": True}
        self.actual["systemd"]["services"]["sshd.service"] = "active"
        row = homelab.service_action(domain, service, self.actual)
        self.assertEqual((row["action"], row["result"]), ("none", "manual-review"))

    def test_archived_domain_stops_but_does_not_delete(self):
        service = {"name": "db", "kind": "compose", "runtime_id": "db", "compose_file": "/srv/app/compose.yaml", "compose_service": "db", "managed": True}
        domain = {"name": "app", "state": "archived", "services": [service]}
        row = homelab.service_action(domain, service, self.actual)
        self.assertEqual(row["action"], "stop")
        self.assertEqual(homelab.make_plan([domain], self.actual)["summary"]["stop"], 1)
        self.assertEqual(homelab.make_plan([domain], self.actual)["summary"]["destructive"], 0)

    def test_required_secrets_must_be_readable(self):
        secret_set = {"provider": "env-file", "required": ["PASSWORD"]}
        self.assertFalse(homelab.secrets_ready(secret_set, {}))
        self.assertFalse(homelab.secrets_ready(secret_set, {"PASSWORD": "UNREADABLE"}))
        self.assertTrue(homelab.secrets_ready(secret_set, {"PASSWORD": "OK"}))
        self.assertTrue(homelab.secrets_ready({"provider": "none"}, {}))

    def test_enabled_systemd_unit_is_disabled_for_stopped_domain(self):
        domain = {"name": "app", "state": "stopped"}
        service = {"name": "worker", "kind": "systemd", "runtime_id": "worker.service", "unit": "worker.service", "managed": True}
        self.actual["systemd"]["services"]["worker.service"] = "inactive"
        self.actual["systemd"]["enabled_units"]["worker.service"] = "enabled"
        self.assertEqual(homelab.service_action(domain, service, self.actual)["action"], "stop")

class SchemaTests(unittest.TestCase):
    def test_example_matches_schema(self):
        from jsonschema import Draft202012Validator

        root = Path(__file__).resolve().parents[1]
        schema = homelab.load_json(root / "schemas" / "domain.schema.json")
        example = homelab.load_json(root / "examples" / "domain.json")
        Draft202012Validator(schema).validate(example)

    def test_docker_memory_units(self):
        self.assertEqual(remote_inventory.memory_bytes("30.5MiB"), int(30.5 * 1024 ** 2))

    def test_remote_python_command_quotes_shell_arguments(self):
        parts = shlex.split(homelab.remote_python_command("print('safe')"))
        self.assertEqual(parts[:2], ["python3", "-c"])
        self.assertEqual(len(parts), 4)

    def test_schema_rejects_managed_service_without_source(self):
        from jsonschema import Draft202012Validator

        root = Path(__file__).resolve().parents[1]
        schema = homelab.load_json(root / "schemas" / "domain.schema.json")
        domain = homelab.load_json(root / "examples" / "domain.json")
        domain["services"] = [{"name": "db", "kind": "compose", "runtime_id": "db", "managed": True, "compose_file": "/srv/db/compose.yaml", "compose_service": "db"}]
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(domain)))


class ComposeApplyTests(unittest.TestCase):
    def run_apply(self, action, docker_status=0, action_name="update", existing=True):
        real_run = subprocess.run
        events = []

        def fake_run(command, **kwargs):
            if command[:3] == ["sudo", "-n", "python3"]:
                result = real_run([sys.executable, *command[3:]], **kwargs)
                events.append("write")
                return result
            events.append("docker")
            return subprocess.CompletedProcess(command, docker_status)

        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.yaml"
            if existing:
                compose_file.write_text("services: {}\n", encoding="utf-8")
                compose_file.chmod(0o640)
            action.update({
                "managed": True,
                "kind": "compose",
                "action": action_name,
                "compose_file": str(compose_file),
                "compose_service": "app",
                "compose_content": "services:\n  app:\n    image: alpine\n",
                "domain": "test",
                "service": "app",
            })
            with patch("remote_apply.subprocess.run", side_effect=fake_run), \
                 patch("remote_apply.sys.stdin", io.StringIO(json.dumps([action]))), \
                 contextlib.redirect_stdout(io.StringIO()):
                if docker_status:
                    with self.assertRaises(SystemExit):
                        remote_apply.main()
                else:
                    remote_apply.main()
            return compose_file.read_text(encoding="utf-8"), compose_file.stat().st_mode & 0o777, events

    def test_compose_update_writes_new_file_and_preserves_mode(self):
        content, mode, events = self.run_apply({})
        self.assertIn("image: alpine", content)
        self.assertEqual(mode, 0o640)
        self.assertEqual(events, ["write", "docker"])

    def test_compose_start_creates_file_before_starting_service(self):
        content, mode, events = self.run_apply({}, action_name="start", existing=False)
        self.assertIn("image: alpine", content)
        self.assertEqual(mode, 0o644)
        self.assertEqual(events, ["write", "docker"])

    def test_failed_compose_update_restores_previous_file(self):
        content, mode, events = self.run_apply({}, docker_status=1)
        self.assertEqual(content, "services: {}\n")
        self.assertEqual(mode, 0o640)
        self.assertEqual(events, ["write", "docker", "write"])


if __name__ == "__main__":
    unittest.main()
