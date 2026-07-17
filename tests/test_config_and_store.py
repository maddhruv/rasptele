import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rasptele.config import ConfigurationError, load_config
from rasptele.store import Store


class ConfigTests(unittest.TestCase):
    def test_missing_secret_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / "config.yaml"
            path.write_text("{}")
            with self.assertRaises(ConfigurationError):
                load_config(path)

    def test_loads_allowed_restart_names(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_USER_ID": "42"}, clear=True
        ):
            path = Path(directory) / "config.yaml"
            path.write_text("containers:\n  restart_allowed: [jellyfin]\n")
            config = load_config(path)
            self.assertEqual(config.allowed_user_id, 42)
            self.assertEqual(config.restart_allowed, frozenset({"jellyfin"}))

    def test_guard_config_does_not_receive_telegram_secret(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / "config.yaml"
            path.write_text("containers:\n  restart_allowed: [pihole]\n")
            config = load_config(path, require_telegram=False)
            self.assertEqual(config.allowed_user_id, 0)
            self.assertEqual(config.restart_allowed, frozenset({"pihole"}))

    def test_invalid_numeric_threshold_is_a_configuration_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_USER_ID": "42"}, clear=True
        ):
            path = Path(directory) / "config.yaml"
            path.write_text("alerts:\n  disk_percent: invalid\n")
            with self.assertRaisesRegex(ConfigurationError, "alerts.disk_percent must be a number"):
                load_config(path)

    def test_invalid_section_interval_and_guard_url_are_rejected(self):
        environment = {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_USER_ID": "42"}
        invalid_documents = [
            "[]\n",
            "alerts: []\n",
            "containers: false\n",
            "monitor_interval_seconds: 1.5\n",
            "monitor_interval_seconds: true\n",
            "docker_guard_url: http://\n",
        ]
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, environment, clear=True
        ):
            path = Path(directory) / "config.yaml"
            for document in invalid_documents:
                with self.subTest(document=document):
                    path.write_text(document)
                    with self.assertRaises(ConfigurationError):
                        load_config(path)


class StoreTests(unittest.TestCase):
    def test_confirmation_is_bound_and_single_use(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "state.db"))
            token = store.create_confirmation(7, "restart", "pihole")
            self.assertFalse(store.consume_confirmation(token, 8, "restart", "pihole"))
            self.assertTrue(store.consume_confirmation(token, 7, "restart", "pihole"))
            self.assertFalse(store.consume_confirmation(token, 7, "restart", "pihole"))
            store.close()

    def test_incident_has_open_and_recovery_transitions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "state.db"))
            self.assertEqual(store.raise_or_remind("disk", "Disk full", 3600), "opened")
            self.assertIsNone(store.raise_or_remind("disk", "Disk full", 3600))
            self.assertTrue(store.recover("disk"))
            store.close()

    def test_notification_remains_until_acknowledged(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "state.db"))
            store.enqueue_notification("disk", "Disk full")
            row = store.pending_notifications()[0]
            self.assertEqual(row["message"], "Disk full")
            store.acknowledge_notification(row["id"])
            self.assertEqual(store.pending_notifications(), [])
            store.close()

    def test_confirmation_target_is_server_side_and_single_use(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "state.db"))
            token = store.create_confirmation(7, "restart", "a-very-long-container-name")
            self.assertIsNone(store.consume_confirmation_target(token, 8, "restart"))
            self.assertEqual(
                store.consume_confirmation_target(token, 7, "restart"), "a-very-long-container-name"
            )
            self.assertIsNone(store.consume_confirmation_target(token, 7, "restart"))
            store.close()

    def test_incident_and_notification_are_one_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "state.db"))
            with self.assertRaises(KeyError):
                store.reconcile_incident("disk", True, "Disk full", 3600, {})
            self.assertEqual(store.active_incident_keys(""), set())
            self.assertEqual(store.pending_notifications(), [])
            self.assertEqual(store.recent_audit(), [])
            store.close()
