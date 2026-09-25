import sys
import shlex
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import homelab
import remote_inventory


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
        row = homelab.service_action(domain, service, self.actual)
        self.assertEqual((row["action"], row["result"]), ("none", "unchanged"))

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


if __name__ == "__main__":
    unittest.main()
