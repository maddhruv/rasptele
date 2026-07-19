import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rasptele.config import ConfigurationError, PiholeConfig, load_config
from rasptele.store import Store


class ConfigTests(unittest.TestCase):
    telegram_environment = {
        "TELEGRAM_BOT_TOKEN": "token",
        "TELEGRAM_ALLOWED_USER_ID": "42",
    }

    def test_missing_secret_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError):
                load_config()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "TELEGRAM_BOT_TOKEN"):
                load_config()

    def test_rejects_invalid_allowed_user_ids(self):
        for value in ("", "invalid", "0", "-1"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_USER_ID": value},
                clear=True,
            ):
                with self.assertRaisesRegex(ConfigurationError, "TELEGRAM_ALLOWED_USER_ID"):
                    load_config()

    def test_loads_defaults_without_optional_environment(self):
        with patch.dict(os.environ, self.telegram_environment, clear=True):
            config = load_config()

        self.assertEqual(config.allowed_user_id, 42)
        self.assertEqual(config.database_path, "/data/rasptele.sqlite3")
        self.assertEqual(config.monitor_interval_seconds, 60)
        self.assertEqual(config.reminder_interval_minutes, 30)
        self.assertEqual(config.audit_retention_days, 90)
        self.assertEqual(config.docker_guard_url, "http://docker-guard:8080")
        self.assertEqual(config.restart_allowed, frozenset())
        self.assertEqual(config.alerts.disk_percent, 90)
        self.assertEqual(config.alerts.temperature_celsius, 80)
        self.assertIsNone(config.pihole)

    def test_loads_all_operational_overrides(self):
        environment = {
            **self.telegram_environment,
            "RASPTELE_MONITOR_INTERVAL_SECONDS": "15",
            "RASPTELE_REMINDER_INTERVAL_MINUTES": "10",
            "RASPTELE_AUDIT_RETENTION_DAYS": "45",
            "RASPTELE_DISK_PERCENT": "85.5",
            "RASPTELE_TEMPERATURE_CELSIUS": "75.25",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = load_config()

        self.assertEqual(config.monitor_interval_seconds, 15)
        self.assertEqual(config.reminder_interval_minutes, 10)
        self.assertEqual(config.audit_retention_days, 45)
        self.assertEqual(config.alerts.disk_percent, 85.5)
        self.assertEqual(config.alerts.temperature_celsius, 75.25)

    def test_restart_allowlist_is_comma_separated_trimmed_and_deduplicated(self):
        environment = {
            **self.telegram_environment,
            "RASPTELE_RESTART_ALLOWED": " pihole, jellyfin,,pihole,  ",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = load_config()

        self.assertEqual(config.restart_allowed, frozenset({"pihole", "jellyfin"}))

    def test_guard_loads_only_restart_configuration(self):
        environment = {
            "RASPTELE_RESTART_ALLOWED": "pihole",
            "PIHOLE_URL": "invalid",
            "PIHOLE_PASSWORD": "unused-secret",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = load_config(require_telegram=False, load_pihole=False)

        self.assertEqual(config.allowed_user_id, 0)
        self.assertEqual(config.restart_allowed, frozenset({"pihole"}))
        self.assertIsNone(config.pihole)

    def test_watchdog_ignores_pihole_configuration(self):
        environment = {**self.telegram_environment, "PIHOLE_PASSWORD": "unused-secret"}
        with patch.dict(os.environ, environment, clear=True):
            config = load_config(load_pihole=False)

        self.assertEqual(config.allowed_user_id, 42)
        self.assertIsNone(config.pihole)

    def test_invalid_numeric_environment_is_rejected(self):
        invalid_values = {
            "RASPTELE_MONITOR_INTERVAL_SECONDS": ("0", "1.5", "invalid"),
            "RASPTELE_REMINDER_INTERVAL_MINUTES": ("0", "invalid"),
            "RASPTELE_AUDIT_RETENTION_DAYS": ("-1", "invalid"),
            "RASPTELE_DISK_PERCENT": ("0", "100.1", "nan", "inf", "invalid"),
            "RASPTELE_TEMPERATURE_CELSIUS": ("0", "nan", "inf", "invalid"),
        }
        for name, values in invalid_values.items():
            for value in values:
                with self.subTest(name=name, value=value), patch.dict(
                    os.environ, {**self.telegram_environment, name: value}, clear=True
                ):
                    with self.assertRaisesRegex(ConfigurationError, name):
                        load_config()

    def test_pihole_integration_is_optional(self):
        environment = {**self.telegram_environment, "PIHOLE_URL": "", "PIHOLE_PASSWORD": ""}
        with patch.dict(os.environ, environment, clear=True):
            self.assertIsNone(load_config().pihole)

    def test_loads_normalized_pihole_configuration(self):
        environment = {
            **self.telegram_environment,
            "PIHOLE_URL": "https://pi.hole/",
            "PIHOLE_PASSWORD": "application-password",
        }
        with patch.dict(os.environ, environment, clear=True):
            pihole = load_config().pihole

        self.assertIsNotNone(pihole)
        self.assertEqual(pihole.url, "https://pi.hole")
        self.assertEqual(pihole.password, "application-password")

    def test_pihole_url_and_password_must_be_set_together(self):
        partial_environments = (
            {"PIHOLE_URL": "http://192.168.1.2"},
            {"PIHOLE_PASSWORD": "application-password"},
        )
        for partial in partial_environments:
            with self.subTest(partial=partial), patch.dict(
                os.environ, {**self.telegram_environment, **partial}, clear=True
            ):
                with self.assertRaisesRegex(ConfigurationError, "PIHOLE_URL and PIHOLE_PASSWORD"):
                    load_config()

    def test_invalid_pihole_url_is_rejected(self):
        for url in ("pi.hole", "http://"):
            environment = {
                **self.telegram_environment,
                "PIHOLE_URL": url,
                "PIHOLE_PASSWORD": "application-password",
            }
            with self.subTest(url=url), patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(ConfigurationError, "PIHOLE_URL"):
                    load_config()

    def test_pihole_configuration_error_does_not_include_password(self):
        password = "do-not-leak-this-password"
        environment = {
            **self.telegram_environment,
            "PIHOLE_URL": "invalid",
            "PIHOLE_PASSWORD": password,
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ConfigurationError) as raised:
                load_config()
            self.assertNotIn(password, str(raised.exception))

    def test_pihole_password_is_hidden_from_config_representation(self):
        password = "must-not-appear-in-repr"
        config = PiholeConfig("http://pihole.test", password)

        self.assertNotIn(password, repr(config))


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
