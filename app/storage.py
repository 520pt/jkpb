from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_MESSAGE_TEMPLATE = "{name} {date}（{time_range})是你的{shift_label}"
LEGACY_DAILY_DUTY_TEMPLATE = (
    "今日在岗人员\n"
    "监控班：早班：{early}，中班：{middle}，晚班：{night}\n"
    "驾驶员：大车：{big_drivers} 小车：{small_drivers}\n"
    "备勤人员：{standby}"
)
DEFAULT_DAILY_DUTY_TEMPLATE = (
    "今日在岗人员\n"
    "监控班：早班：{early}，中班：{middle}，晚班：{night}\n"
    "驾驶员：大车：{big_drivers} 小车：{small_drivers}\n"
    "备勤人员：{standby}\n"
    "今日下午休息：{afternoon_rest}\n"
    "正在休息：{resting}\n"
    "今日下午到岗：{afternoon_return}"
)
LEGACY_REST_MESSAGE_TEMPLATE = "{name} {date} 今天休息"
LEGACY_TOMORROW_REST_MESSAGE_TEMPLATE = "{name} {date} 明天休息"
DEFAULT_REST_MESSAGE_TEMPLATE = "{name} {rest_status}"


class DutyRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS roster_months (
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    grid_json TEXT NOT NULL,
                    source_image_path TEXT NOT NULL DEFAULT '',
                    confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (year, month)
                );

                CREATE TABLE IF NOT EXISTS roster_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    grid_json TEXT NOT NULL,
                    source_image_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS monitored_people (
                    name TEXT PRIMARY KEY,
                    wecom_userid TEXT NOT NULL,
                    mention_text TEXT NOT NULL DEFAULT '',
                    mention_mobile TEXT NOT NULL DEFAULT '',
                    daily_time TEXT NOT NULL DEFAULT '07:50',
                    before_shift_minutes INTEGER NOT NULL DEFAULT 10,
                    rest_reminder_enabled INTEGER NOT NULL DEFAULT 0,
                    rest_reminder_time TEXT NOT NULL DEFAULT '08:30',
                    rest_message_template TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS notification_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    webhook_url TEXT NOT NULL DEFAULT '',
                    message_template TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS personnel_names (
                    name TEXT PRIMARY KEY,
                    mention_mobile TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS custom_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    mention_mobile TEXT NOT NULL DEFAULT '',
                    shift_code TEXT NOT NULL,
                    reminder_time TEXT NOT NULL,
                    message TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS daily_duty_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    reminder_time TEXT NOT NULL DEFAULT '07:50',
                    big_driver_names_json TEXT NOT NULL DEFAULT '[]',
                    small_driver_names_json TEXT NOT NULL DEFAULT '[]',
                    message_template TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sent_reminders (
                    reminder_key TEXT PRIMARY KEY,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS send_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    target TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(monitored_people)").fetchall()}
            if "mention_mobile" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN mention_mobile TEXT NOT NULL DEFAULT ''")
            if "rest_reminder_enabled" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN rest_reminder_enabled INTEGER NOT NULL DEFAULT 0")
            if "rest_reminder_time" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN rest_reminder_time TEXT NOT NULL DEFAULT '08:30'")
            if "rest_message_template" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN rest_message_template TEXT NOT NULL DEFAULT ''")
            config_columns = {row["name"] for row in conn.execute("PRAGMA table_info(notification_config)").fetchall()}
            if "message_template" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN message_template TEXT NOT NULL DEFAULT ''")
            personnel_columns = {row["name"] for row in conn.execute("PRAGMA table_info(personnel_names)").fetchall()}
            if "mention_mobile" not in personnel_columns:
                conn.execute("ALTER TABLE personnel_names ADD COLUMN mention_mobile TEXT NOT NULL DEFAULT ''")

    def table_names(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {row["name"] for row in rows}

    def save_roster_month(
        self,
        year: int,
        month: int,
        grid: list[dict[str, Any]],
        source_image_path: str,
    ) -> None:
        with self._connect() as conn:
            grid_json = json.dumps(grid, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO roster_months (year, month, grid_json, source_image_path)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(year, month) DO UPDATE SET
                    grid_json = excluded.grid_json,
                    source_image_path = excluded.source_image_path,
                    confirmed_at = CURRENT_TIMESTAMP
                """,
                (year, month, grid_json, source_image_path),
            )
            conn.execute(
                """
                INSERT INTO roster_versions (year, month, grid_json, source_image_path)
                VALUES (?, ?, ?, ?)
                """,
                (year, month, grid_json, source_image_path),
            )
        self.upsert_personnel_names([str(row.get("name", "")).strip() for row in grid])

    def get_roster_month(self, year: int, month: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM roster_months WHERE year = ? AND month = ?",
                (year, month),
            ).fetchone()
        if row is None:
            return None
        return {
            "year": row["year"],
            "month": row["month"],
            "grid": json.loads(row["grid_json"]),
            "source_image_path": row["source_image_path"],
            "confirmed_at": row["confirmed_at"],
        }

    def list_roster_months(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM roster_months ORDER BY year, month").fetchall()
        return [
            {
                "year": row["year"],
                "month": row["month"],
                "grid": json.loads(row["grid_json"]),
                "source_image_path": row["source_image_path"],
                "confirmed_at": row["confirmed_at"],
            }
            for row in rows
        ]

    def list_roster_versions(self, year: int, month: int, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, year, month, grid_json, source_image_path, created_at
                FROM roster_versions
                WHERE year = ? AND month = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (year, month, max(1, min(int(limit), 100))),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "year": row["year"],
                "month": row["month"],
                "grid": json.loads(row["grid_json"]),
                "source_image_path": row["source_image_path"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_roster_version(self, version_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, year, month, grid_json, source_image_path, created_at
                FROM roster_versions
                WHERE id = ?
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "year": row["year"],
            "month": row["month"],
            "grid": json.loads(row["grid_json"]),
            "source_image_path": row["source_image_path"],
            "created_at": row["created_at"],
        }

    def count_roster_months(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM roster_months").fetchone()
        return int(row["count"])

    def count_monitored_people(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM monitored_people").fetchone()
        return int(row["count"])

    def upsert_personnel_names(self, names: list[str]) -> None:
        clean_names = sorted({name.strip() for name in names if name and name.strip()})
        with self._connect() as conn:
            for name in clean_names:
                conn.execute(
                    """
                    INSERT INTO personnel_names (name) VALUES (?)
                    ON CONFLICT(name) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                    """,
                    (name,),
                )

    def upsert_personnel_contacts(self, contacts: list[dict[str, str]]) -> None:
        clean_contacts: dict[str, str] = {}
        for contact in contacts:
            name = str(contact.get("name") or "").strip()
            if not name:
                continue
            mobile = str(contact.get("mention_mobile") or "").strip()
            if name not in clean_contacts or mobile:
                clean_contacts[name] = mobile
        with self._connect() as conn:
            for name, mobile in sorted(clean_contacts.items()):
                conn.execute(
                    """
                    INSERT INTO personnel_names (name, mention_mobile) VALUES (?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        mention_mobile = CASE
                            WHEN excluded.mention_mobile != '' THEN excluded.mention_mobile
                            ELSE personnel_names.mention_mobile
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (name, mobile),
                )

    def save_personnel_names(self, names: list[str]) -> None:
        clean_names = sorted({name.strip() for name in names if name and name.strip()})
        with self._connect() as conn:
            if clean_names:
                placeholders = ",".join("?" for _ in clean_names)
                conn.execute(f"DELETE FROM personnel_names WHERE name NOT IN ({placeholders})", clean_names)
            else:
                conn.execute("DELETE FROM personnel_names")
            for name in clean_names:
                conn.execute(
                    """
                    INSERT INTO personnel_names (name) VALUES (?)
                    ON CONFLICT(name) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                    """,
                    (name,),
                )

    def list_personnel_names(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM personnel_names ORDER BY name").fetchall()
        return [row["name"] for row in rows]

    def list_personnel(self) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name, mention_mobile FROM personnel_names ORDER BY name").fetchall()
        return [{"name": row["name"], "mention_mobile": row["mention_mobile"]} for row in rows]

    def save_monitored_person(
        self,
        *,
        name: str,
        original_name: str = "",
        wecom_userid: str = "",
        mention_text: str = "",
        mention_mobile: str = "",
        daily_time: str = "07:50",
        before_shift_minutes: int = 10,
        rest_reminder_enabled: bool = False,
        rest_reminder_time: str = "08:30",
        rest_message_template: str = DEFAULT_REST_MESSAGE_TEMPLATE,
        enabled: bool = True,
    ) -> None:
        clean_name = name.strip()
        clean_original_name = original_name.strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO monitored_people
                    (
                        name, wecom_userid, mention_text, mention_mobile, daily_time, before_shift_minutes,
                        rest_reminder_enabled, rest_reminder_time, rest_message_template, enabled
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    wecom_userid = excluded.wecom_userid,
                    mention_text = excluded.mention_text,
                    mention_mobile = excluded.mention_mobile,
                    daily_time = excluded.daily_time,
                    before_shift_minutes = excluded.before_shift_minutes,
                    rest_reminder_enabled = excluded.rest_reminder_enabled,
                    rest_reminder_time = excluded.rest_reminder_time,
                    rest_message_template = excluded.rest_message_template,
                    enabled = excluded.enabled
                """,
                (
                    clean_name,
                    wecom_userid,
                    mention_text,
                    mention_mobile,
                    daily_time,
                    before_shift_minutes,
                    int(rest_reminder_enabled),
                    rest_reminder_time or "08:30",
                    _normalize_rest_message_template(rest_message_template),
                    int(enabled),
                ),
            )
            if clean_original_name and clean_original_name != clean_name:
                conn.execute("DELETE FROM monitored_people WHERE name = ?", (clean_original_name,))
        self.upsert_personnel_contacts([{"name": clean_name, "mention_mobile": mention_mobile}])

    def delete_monitored_person(self, name: str) -> bool:
        clean_name = name.strip()
        if not clean_name:
            return False
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM monitored_people WHERE name = ?", (clean_name,))
        return cursor.rowcount > 0

    def list_monitored_people(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM monitored_people"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [
            {
                "name": row["name"],
                "wecom_userid": row["wecom_userid"],
                "mention_text": row["mention_text"],
                "mention_mobile": row["mention_mobile"],
                "daily_time": row["daily_time"],
                "before_shift_minutes": row["before_shift_minutes"],
                "rest_reminder_enabled": bool(row["rest_reminder_enabled"]),
                "rest_reminder_time": row["rest_reminder_time"],
                "rest_message_template": _normalize_rest_message_template(row["rest_message_template"]),
                "enabled": bool(row["enabled"]),
            }
            for row in rows
        ]

    def save_custom_reminder(
        self,
        *,
        name: str,
        shift_code: str,
        reminder_time: str,
        message: str,
        mention_mobile: str = "",
        enabled: bool = True,
        id: int | None = None,
    ) -> int:
        clean_name = name.strip()
        clean_mobile = mention_mobile.strip()
        with self._connect() as conn:
            if id is not None:
                cursor = conn.execute(
                    """
                    UPDATE custom_reminders
                    SET name = ?,
                        mention_mobile = ?,
                        shift_code = ?,
                        reminder_time = ?,
                        message = ?,
                        enabled = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (clean_name, clean_mobile, shift_code, reminder_time, message, int(enabled), int(id)),
                )
                if cursor.rowcount > 0:
                    reminder_id = int(id)
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO custom_reminders
                            (name, mention_mobile, shift_code, reminder_time, message, enabled)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (clean_name, clean_mobile, shift_code, reminder_time, message, int(enabled)),
                    )
                    reminder_id = int(cursor.lastrowid)
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO custom_reminders
                        (name, mention_mobile, shift_code, reminder_time, message, enabled)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (clean_name, clean_mobile, shift_code, reminder_time, message, int(enabled)),
                )
                reminder_id = int(cursor.lastrowid)
        self.upsert_personnel_contacts([{"name": clean_name, "mention_mobile": clean_mobile}])
        return reminder_id

    def delete_custom_reminder(self, reminder_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM custom_reminders WHERE id = ?", (int(reminder_id),))
        return cursor.rowcount > 0

    def list_custom_reminders(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM custom_reminders"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name, shift_code, reminder_time, id"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "mention_mobile": row["mention_mobile"],
                "shift_code": row["shift_code"],
                "reminder_time": row["reminder_time"],
                "message": row["message"],
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def save_notification_config(self, *, webhook_url: str, message_template: str = DEFAULT_MESSAGE_TEMPLATE) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notification_config (id, webhook_url, message_template)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    webhook_url = excluded.webhook_url,
                    message_template = excluded.message_template,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (webhook_url, message_template or DEFAULT_MESSAGE_TEMPLATE),
            )

    def get_notification_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT webhook_url, message_template FROM notification_config WHERE id = 1").fetchone()
        if row is None:
            return {"webhook_url": "", "message_template": DEFAULT_MESSAGE_TEMPLATE}
        return {
            "webhook_url": row["webhook_url"],
            "message_template": row["message_template"] or DEFAULT_MESSAGE_TEMPLATE,
        }

    def save_daily_duty_config(
        self,
        *,
        enabled: bool = True,
        reminder_time: str = "07:50",
        big_driver_names: list[str] | None = None,
        small_driver_names: list[str] | None = None,
        message_template: str = DEFAULT_DAILY_DUTY_TEMPLATE,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_duty_config
                    (id, enabled, reminder_time, big_driver_names_json, small_driver_names_json, message_template)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    reminder_time = excluded.reminder_time,
                    big_driver_names_json = excluded.big_driver_names_json,
                    small_driver_names_json = excluded.small_driver_names_json,
                    message_template = excluded.message_template,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(enabled),
                    reminder_time or "07:50",
                    json.dumps(big_driver_names or [], ensure_ascii=False),
                    json.dumps(small_driver_names or [], ensure_ascii=False),
                    _normalize_daily_duty_template(message_template),
                ),
            )

    def get_daily_duty_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM daily_duty_config WHERE id = 1").fetchone()
        if row is None:
            return {
                "enabled": True,
                "reminder_time": "07:50",
                "big_driver_names": [],
                "small_driver_names": [],
                "message_template": DEFAULT_DAILY_DUTY_TEMPLATE,
            }
        return {
            "enabled": bool(row["enabled"]),
            "reminder_time": row["reminder_time"],
            "big_driver_names": json.loads(row["big_driver_names_json"] or "[]"),
            "small_driver_names": json.loads(row["small_driver_names_json"] or "[]"),
            "message_template": _normalize_daily_duty_template(row["message_template"]),
        }

    def mark_sent_once(self, reminder_key: str) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("INSERT INTO sent_reminders (reminder_key) VALUES (?)", (reminder_key,))
            return True
        except sqlite3.IntegrityError:
            return False

    def save_send_record(
        self,
        *,
        kind: str,
        target: str,
        status: str,
        scheduled_at: str = "",
        content: str = "",
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO send_records (kind, target, scheduled_at, status, content, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (kind, target, scheduled_at, status, content, error),
            )

    def list_send_records(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, target, scheduled_at, status, content, error, created_at
                FROM send_records
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "target": row["target"],
                "scheduled_at": row["scheduled_at"],
                "status": row["status"],
                "content": row["content"],
                "error": row["error"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_send_record(self, record_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, kind, target, scheduled_at, status, content, error, created_at
                FROM send_records
                WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "kind": row["kind"],
            "target": row["target"],
            "scheduled_at": row["scheduled_at"],
            "status": row["status"],
            "content": row["content"],
            "error": row["error"],
            "created_at": row["created_at"],
        }

    def list_send_records_since(self, start_text: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, target, scheduled_at, status, content, error, created_at
                FROM send_records
                WHERE created_at >= ?
                ORDER BY id DESC
                """,
                (start_text,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "target": row["target"],
                "scheduled_at": row["scheduled_at"],
                "status": row["status"],
                "content": row["content"],
                "error": row["error"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def _normalize_rest_message_template(value: str | None) -> str:
    template = (value or "").strip()
    if not template or template in {LEGACY_REST_MESSAGE_TEMPLATE, LEGACY_TOMORROW_REST_MESSAGE_TEMPLATE}:
        return DEFAULT_REST_MESSAGE_TEMPLATE
    return template


def _normalize_daily_duty_template(value: str | None) -> str:
    template = (value or "").strip()
    legacy_with_resting = LEGACY_DAILY_DUTY_TEMPLATE + "\n今日休息人员：{resting}"
    legacy_with_rest_statuses = (
        LEGACY_DAILY_DUTY_TEMPLATE + "\n"
        "今日下午休息：{afternoon_rest}\n"
        "正在休息到：{resting_until}\n"
        "今日下午到岗：{afternoon_return}"
    )
    if not template or template in {LEGACY_DAILY_DUTY_TEMPLATE, legacy_with_resting, legacy_with_rest_statuses}:
        return DEFAULT_DAILY_DUTY_TEMPLATE
    return template
