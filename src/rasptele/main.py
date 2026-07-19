"""Application entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging

from .bot import run_bot
from .config import ConfigurationError, load_config
from .guard import main as guard_main
from .monitor import Monitor
from .pihole import PiholeClient
from .store import Store
from .watchdog import run_watchdog


def main() -> None:
    parser = argparse.ArgumentParser(description="Rasptele Telegram control plane")
    parser.add_argument("--config", default="/config/config.yaml")
    parser.add_argument("--guard", action="store_true", help="run the Docker guard sidecar")
    parser.add_argument("--watchdog", action="store_true", help="run the independent failure notifier")
    args, _ = parser.parse_known_args()
    if args.guard:
        guard_main()
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(args.config, require_integration_secrets=not args.watchdog)
    except ConfigurationError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc
    if args.watchdog:
        asyncio.run(run_watchdog(config))
        return
    store = Store(config.database_path)
    pihole = PiholeClient(config.pihole) if config.pihole is not None else None
    monitor = Monitor(config, store, pihole=pihole)
    try:
        asyncio.run(run_bot(config, store, monitor, pihole))
    finally:
        store.close()


if __name__ == "__main__":
    main()
