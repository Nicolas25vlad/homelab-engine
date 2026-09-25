import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import remote_secrets


class SecretKeyTests(unittest.TestCase):
    def test_parser_returns_names_only(self):
        keys = remote_secrets.present_keys("export DATABASE_PASSWORD=do-not-print\nAPI_TOKEN=also-private\n# ignored\n")
        self.assertEqual(keys, {"DATABASE_PASSWORD", "API_TOKEN"})
        self.assertNotIn("do-not-print", keys)


if __name__ == "__main__":
    unittest.main()
