import json
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from rasptele import __version__
from rasptele.config import AlertConfig, Config, PiholeConfig
from rasptele.main import main

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = ("compose.yaml", "compose.coolify.yaml", "compose.portainer.yaml")


def make_config(*, configured: bool) -> Config:
    return Config(
        token="123:token",
        allowed_user_id=42,
        database_path=":memory:",
        monitor_interval_seconds=30,
        reminder_interval_minutes=30,
        audit_retention_days=90,
        docker_guard_url="http://guard:9000",
        restart_allowed=frozenset(),
        alerts=AlertConfig(),
        pihole=PiholeConfig("http://pihole.test", "application-password")
        if configured
        else None,
    )


class ApplicationWiringTests(unittest.TestCase):
    def run_main(self, config: Config):
        store = MagicMock()
        monitor = MagicMock()
        pihole = MagicMock()
        with (
            patch.object(sys, "argv", ["rasptele", "--config", "/tmp/config.yaml"]),
            patch("rasptele.main.load_config", return_value=config) as load_config,
            patch("rasptele.main.Store", return_value=store),
            patch("rasptele.main.Monitor", return_value=monitor) as monitor_type,
            patch("rasptele.main.PiholeClient", return_value=pihole) as pihole_type,
            patch("rasptele.main.run_bot", new_callable=AsyncMock) as run_bot,
        ):
            main()
        load_config.assert_called_once_with(
            "/tmp/config.yaml", require_integration_secrets=True
        )
        store.close.assert_called_once_with()
        return store, monitor, pihole, monitor_type, pihole_type, run_bot

    def test_configured_main_shares_one_pihole_client(self) -> None:
        config = make_config(configured=True)

        store, monitor, pihole, monitor_type, pihole_type, run_bot = self.run_main(config)

        pihole_type.assert_called_once_with(config.pihole)
        monitor_type.assert_called_once_with(config, store, pihole=pihole)
        run_bot.assert_awaited_once_with(config, store, monitor, pihole)

    def test_unconfigured_main_creates_no_pihole_client(self) -> None:
        config = make_config(configured=False)

        store, monitor, _, monitor_type, pihole_type, run_bot = self.run_main(config)

        pihole_type.assert_not_called()
        monitor_type.assert_called_once_with(config, store, pihole=None)
        run_bot.assert_awaited_once_with(config, store, monitor, None)


class DeploymentManifestTests(unittest.TestCase):
    def manifests(self):
        for filename in COMPOSE_FILES:
            with self.subTest(filename=filename):
                yield filename, yaml.safe_load((ROOT / filename).read_text())

    def test_only_main_service_receives_pihole_password(self) -> None:
        for _, manifest in self.manifests():
            services = manifest["services"]
            self.assertIn("PIHOLE_PASSWORD", services["rasptele"]["environment"])
            self.assertEqual(
                services["rasptele"]["environment"]["PIHOLE_PASSWORD"],
                "${PIHOLE_PASSWORD:?required}",
            )
            self.assertNotIn(
                "PIHOLE_PASSWORD", services["docker-guard"].get("environment", {})
            )
            self.assertNotIn(
                "PIHOLE_PASSWORD", services["rasptele-watchdog"].get("environment", {})
            )

    def test_service_secret_boundaries_are_least_privileged(self) -> None:
        secret_names = {"TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID", "PIHOLE_PASSWORD"}
        for _, manifest in self.manifests():
            services = manifest["services"]
            main_secrets = secret_names & set(services["rasptele"]["environment"])
            guard_secrets = secret_names & set(
                services["docker-guard"].get("environment", {})
            )
            watchdog_secrets = secret_names & set(
                services["rasptele-watchdog"]["environment"]
            )
            self.assertEqual(main_secrets, secret_names)
            self.assertEqual(guard_secrets, set())
            self.assertEqual(
                watchdog_secrets,
                {"TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID"},
            )

    def test_release_versions_match(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        package = json.loads((ROOT / "package.json").read_text())
        self.assertEqual(project["version"], __version__)
        self.assertEqual(package["version"], __version__)
        for _, manifest in self.manifests():
            for service in manifest["services"].values():
                self.assertEqual(
                    service["image"], f"ghcr.io/maddhruv/rasptele:{__version__}"
                )


if __name__ == "__main__":
    unittest.main()
