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
COMPOSE_FILE = ROOT / "compose.yaml"


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
            patch.object(sys, "argv", ["rasptele"]),
            patch("rasptele.main.load_config", return_value=config) as load_config,
            patch("rasptele.main.Store", return_value=store),
            patch("rasptele.main.Monitor", return_value=monitor) as monitor_type,
            patch("rasptele.main.PiholeClient", return_value=pihole) as pihole_type,
            patch("rasptele.main.run_bot", new_callable=AsyncMock) as run_bot,
        ):
            main()
        load_config.assert_called_once_with(load_pihole=True)
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

    def test_watchdog_loads_telegram_configuration_without_pihole(self) -> None:
        config = make_config(configured=False)
        with (
            patch.object(sys, "argv", ["rasptele", "--watchdog"]),
            patch("rasptele.main.load_config", return_value=config) as load_config,
            patch("rasptele.main.run_watchdog", new_callable=AsyncMock) as run_watchdog,
        ):
            main()

        load_config.assert_called_once_with(load_pihole=False)
        run_watchdog.assert_awaited_once_with(config)


class DeploymentManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = yaml.safe_load(COMPOSE_FILE.read_text())
        self.services = self.manifest["services"]

    def test_only_one_deployment_manifest_exists(self) -> None:
        self.assertFalse((ROOT / "compose.coolify.yaml").exists())
        self.assertFalse((ROOT / "compose.portainer.yaml").exists())
        self.assertFalse((ROOT / "config.example.yaml").exists())

    def test_services_pull_one_exact_release_without_config_mounts(self) -> None:
        expected_image = f"ghcr.io/maddhruv/rasptele:{__version__}"
        for name, service in self.services.items():
            with self.subTest(service=name):
                self.assertEqual(service["image"], expected_image)
                self.assertEqual(service["pull_policy"], "always")
                self.assertNotIn("build", service)
                self.assertNotIn("--config", service.get("command", []))
                self.assertFalse(
                    any("/config" in volume for volume in service.get("volumes", []))
                )

    def test_service_environment_is_least_privileged(self) -> None:
        self.assertEqual(
            set(self.services["rasptele"]["environment"]),
            {
                "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_ALLOWED_USER_ID",
                "PIHOLE_URL",
                "PIHOLE_PASSWORD",
                "RASPTELE_RESTART_ALLOWED",
                "RASPTELE_MONITOR_INTERVAL_SECONDS",
                "RASPTELE_REMINDER_INTERVAL_MINUTES",
                "RASPTELE_AUDIT_RETENTION_DAYS",
                "RASPTELE_DISK_PERCENT",
                "RASPTELE_TEMPERATURE_CELSIUS",
            },
        )
        self.assertEqual(
            set(self.services["docker-guard"]["environment"]),
            {"RASPTELE_RESTART_ALLOWED"},
        )
        self.assertEqual(
            set(self.services["rasptele-watchdog"]["environment"]),
            {
                "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_ALLOWED_USER_ID",
                "RASPTELE_MONITOR_INTERVAL_SECONDS",
            },
        )

    def test_privileged_mounts_remain_isolated(self) -> None:
        guard_volumes = self.services["docker-guard"]["volumes"]
        bot_volumes = self.services["rasptele"]["volumes"]
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", guard_volumes)
        self.assertNotIn("/var/run/docker.sock:/var/run/docker.sock", bot_volumes)
        self.assertEqual(self.services["rasptele"]["pid"], "host")
        self.assertIn("rasptele-data:/data", bot_volumes)
        self.assertIn("/proc:/host/proc:ro", bot_volumes)
        self.assertIn("/sys:/host/sys:ro", bot_volumes)
        self.assertIn("/:/host:ro", bot_volumes)
        self.assertNotIn("volumes", self.services["rasptele-watchdog"])

    def test_release_versions_match(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        package = json.loads((ROOT / "package.json").read_text())
        self.assertEqual(project["version"], __version__)
        self.assertEqual(package["version"], __version__)
        for service in self.services.values():
            self.assertEqual(service["image"], f"ghcr.io/maddhruv/rasptele:{__version__}")

    def test_release_automation_targets_canonical_compose_and_latest(self) -> None:
        release_it = json.loads((ROOT / ".release-it.json").read_text())
        outputs = release_it["plugins"]["@release-it/bumper"]["out"]
        compose_outputs = [
            output["file"] for output in outputs if output["file"].endswith(".yaml")
        ]
        self.assertEqual(compose_outputs, ["compose.yaml"])
        self.assertIn("README.md", [output["file"] for output in outputs])
        self.assertIn(
            "docs/getting-started.md", [output["file"] for output in outputs]
        )
        self.assertIn("docs/deployment.md", [output["file"] for output in outputs])

        deployment_docs = (ROOT / "docs/deployment.md").read_text()
        self.assertIn("Releases from before the env-only migration", deployment_docs)
        self.assertNotRegex(deployment_docs, r"v\d+\.\d+\.\d+ and earlier")
        self.assertIn("git switch --detach <NEW_RELEASE_TAG>", deployment_docs)

        release_workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn("type=raw,value=latest", release_workflow)
        self.assertIn("group: release\n", release_workflow)
        self.assertNotIn("group: release-${{ github.ref }}", release_workflow)
        self.assertNotIn("compose.coolify.yaml", release_workflow)
        self.assertNotIn("compose.portainer.yaml", release_workflow)


if __name__ == "__main__":
    unittest.main()
