"""Small durable store for incidents, confirmations, and audit events."""

from __future__ import annotations

import secrets
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, database_path: str) -> None:
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS incidents (
              key TEXT PRIMARY KEY, active INTEGER NOT NULL, opened_at INTEGER NOT NULL,
              last_notified_at INTEGER NOT NULL, detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS confirmations (
              token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, action TEXT NOT NULL,
              target TEXT NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS audit_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at INTEGER NOT NULL,
              event_type TEXT NOT NULL, detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notification_outbox (
              id INTEGER PRIMARY KEY AUTOINCREMENT, incident_key TEXT NOT NULL,
              message TEXT NOT NULL, created_at INTEGER NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def audit(self, event_type: str, detail: str) -> None:
        self.connection.execute(
            "INSERT INTO audit_events(occurred_at, event_type, detail) VALUES (?, ?, ?)",
            (int(time.time()), event_type, detail),
        )
        self.connection.commit()

    def raise_or_remind(self, key: str, detail: str, reminder_seconds: int) -> str | None:
        """Return opened/reminder/recovered transition names; None means no message."""
        now = int(time.time())
        row = self.connection.execute("SELECT * FROM incidents WHERE key = ?", (key,)).fetchone()
        if row is None or not row["active"]:
            self.connection.execute(
                "INSERT OR REPLACE INTO incidents VALUES (?, 1, ?, ?, ?)", (key, now, now, detail)
            )
            self.connection.commit()
            self.audit("incident_opened", f"{key}: {detail}")
            return "opened"
        if now - row["last_notified_at"] >= reminder_seconds:
            self.connection.execute(
                "UPDATE incidents SET last_notified_at = ?, detail = ? WHERE key = ?", (now, detail, key)
            )
            self.connection.commit()
            self.audit("incident_reminded", f"{key}: {detail}")
            return "reminder"
        return None

    def recover(self, key: str) -> bool:
        row = self.connection.execute("SELECT active FROM incidents WHERE key = ?", (key,)).fetchone()
        if row is None or not row["active"]:
            return False
        self.connection.execute("UPDATE incidents SET active = 0 WHERE key = ?", (key,))
        self.connection.commit()
        self.audit("incident_recovered", key)
        return True

    def active_incident_keys(self, prefix: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT key FROM incidents WHERE active = 1 AND key LIKE ?", (f"{prefix}%",)
        ).fetchall()
        return {str(row["key"]) for row in rows}

    def reconcile_incident(
        self,
        key: str,
        active: bool,
        detail: str,
        reminder_seconds: int,
        messages: dict[str, str],
    ) -> str | None:
        """Persist an incident transition and its notification in one transaction."""
        now = int(time.time())
        row = self.connection.execute("SELECT * FROM incidents WHERE key = ?", (key,)).fetchone()
        transition: str | None = None
        if active and (row is None or not row["active"]):
            transition = "opened"
        elif active and now - row["last_notified_at"] >= reminder_seconds:
            transition = "reminder"
        elif not active and row is not None and row["active"]:
            transition = "recovered"
        if transition is None:
            return None

        with self.connection:
            if transition == "opened":
                self.connection.execute(
                    "INSERT OR REPLACE INTO incidents VALUES (?, 1, ?, ?, ?)",
                    (key, now, now, detail),
                )
            elif transition == "reminder":
                self.connection.execute(
                    "UPDATE incidents SET last_notified_at = ?, detail = ? WHERE key = ?",
                    (now, detail, key),
                )
            else:
                self.connection.execute(
                    "UPDATE incidents SET active = 0, detail = ? WHERE key = ?", (detail, key)
                )
            self.connection.execute(
                "INSERT INTO audit_events(occurred_at, event_type, detail) VALUES (?, ?, ?)",
                (now, f"incident_{transition}", f"{key}: {detail}"),
            )
            self.connection.execute(
                "INSERT INTO notification_outbox(incident_key, message, created_at) VALUES (?, ?, ?)",
                (key, messages[transition], now),
            )
        return transition

    def enqueue_notification(self, incident_key: str, message: str) -> None:
        self.connection.execute(
            "INSERT INTO notification_outbox(incident_key, message, created_at) VALUES (?, ?, ?)",
            (incident_key, message, int(time.time())),
        )
        self.connection.commit()

    def pending_notifications(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT id, incident_key, message FROM notification_outbox ORDER BY id LIMIT ?", (limit,)
        ).fetchall()

    def acknowledge_notification(self, notification_id: int) -> None:
        self.connection.execute("DELETE FROM notification_outbox WHERE id = ?", (notification_id,))
        self.connection.commit()

    def create_confirmation(self, user_id: int, action: str, target: str, lifetime_seconds: int = 60) -> str:
        token = secrets.token_urlsafe(18)
        self.connection.execute(
            "INSERT INTO confirmations VALUES (?, ?, ?, ?, ?, NULL)",
            (token, user_id, action, target, int(time.time()) + lifetime_seconds),
        )
        self.connection.commit()
        return token

    def consume_confirmation(self, token: str, user_id: int, action: str, target: str) -> bool:
        now = int(time.time())
        cursor = self.connection.execute(
            """UPDATE confirmations SET consumed_at = ? WHERE token = ? AND user_id = ?
               AND action = ? AND target = ? AND consumed_at IS NULL AND expires_at >= ?""",
            (now, token, user_id, action, target, now),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def consume_confirmation_target(self, token: str, user_id: int, action: str) -> str | None:
        now = int(time.time())
        row = self.connection.execute(
            """SELECT target FROM confirmations WHERE token = ? AND user_id = ? AND action = ?
               AND consumed_at IS NULL AND expires_at >= ?""",
            (token, user_id, action, now),
        ).fetchone()
        if row is None:
            return None
        cursor = self.connection.execute(
            "UPDATE confirmations SET consumed_at = ? WHERE token = ? AND consumed_at IS NULL",
            (now, token),
        )
        self.connection.commit()
        return str(row["target"]) if cursor.rowcount == 1 else None

    def recent_audit(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT occurred_at, event_type, detail FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def prune(self, retention_days: int) -> None:
        cutoff = int(time.time()) - retention_days * 86400
        self.connection.execute("DELETE FROM audit_events WHERE occurred_at < ?", (cutoff,))
        self.connection.execute("DELETE FROM incidents WHERE active = 0 AND opened_at < ?", (cutoff,))
        self.connection.execute("DELETE FROM confirmations WHERE expires_at < ?", (cutoff,))
        self.connection.commit()
