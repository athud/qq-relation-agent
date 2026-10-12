from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id      TEXT,
    group_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    nickname    TEXT,
    card        TEXT,
    content     TEXT,
    ts          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_group_ts   ON messages(group_id, ts);
CREATE INDEX IF NOT EXISTS idx_msg_group_user ON messages(group_id, user_id);

CREATE TABLE IF NOT EXISTS members (
    group_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    nickname    TEXT,
    card        TEXT,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS state (
    group_id    INTEGER NOT NULL,
    key         TEXT NOT NULL,
    last_ts     INTEGER NOT NULL,
    payload     TEXT,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (group_id, key)
);

CREATE TABLE IF NOT EXISTS known_groups (
    group_id    INTEGER PRIMARY KEY,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
);
"""


class Storage:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------------- messages ----------------

    def add_message(self, *, msg_id, group_id, user_id, nickname, card, content, ts):
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(msg_id, group_id, user_id, nickname, card, content, ts)"
                " VALUES (?,?,?,?,?,?,?)",
                (msg_id, group_id, user_id, nickname, card, content, ts),
            )
            self._conn.execute(
                "INSERT INTO known_groups(group_id, first_seen, last_seen) VALUES (?,?,?)"
                " ON CONFLICT(group_id) DO UPDATE SET last_seen=excluded.last_seen",
                (group_id, ts, ts),
            )
            self._conn.commit()

    def messages_between(self, group_id, start_ts, end_ts, limit=None):
        with self._lock:
            if limit:
                cur = self._conn.execute(
                    "SELECT * FROM ("
                    "  SELECT * FROM messages"
                    "  WHERE group_id=? AND ts>? AND ts<=?"
                    "  ORDER BY ts DESC LIMIT ?"
                    ") ORDER BY ts ASC",
                    (group_id, start_ts, end_ts, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM messages WHERE group_id=? AND ts>? AND ts<=?"
                    " ORDER BY ts ASC",
                    (group_id, start_ts, end_ts),
                )
            return [dict(r) for r in cur.fetchall()]

    def count_messages(self, group_id, start_ts, end_ts):
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE group_id=? AND ts>? AND ts<=?",
                (group_id, start_ts, end_ts),
            )
            return cur.fetchone()[0]

    def known_groups(self):
        with self._lock:
            cur = self._conn.execute("SELECT group_id FROM known_groups")
            return [r[0] for r in cur.fetchall()]

    # ---------------- members ----------------

    def upsert_members(self, group_id, members):
        now = int(time.time())
        with self._lock:
            self._conn.executemany(
                "INSERT INTO members(group_id, user_id, nickname, card, updated_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(group_id, user_id) DO UPDATE SET"
                " nickname=excluded.nickname, card=excluded.card, updated_at=excluded.updated_at",
                [(group_id, m["user_id"], m.get("nickname", "") or "",
                  m.get("card", "") or "", now) for m in members],
            )
            self._conn.commit()

    def get_members(self, group_id):
        with self._lock:
            cur = self._conn.execute(
                "SELECT user_id, nickname, card FROM members WHERE group_id=?",
                (group_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    # ---------------- state ----------------

    def get_state(self, group_id, key):
        with self._lock:
            cur = self._conn.execute(
                "SELECT last_ts, payload, updated_at FROM state WHERE group_id=? AND key=?",
                (group_id, key),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "last_ts": row["last_ts"],
                "payload": json.loads(row["payload"]) if row["payload"] else None,
                "updated_at": row["updated_at"],
            }

    def set_state(self, group_id, key, last_ts, payload=None):
        with self._lock:
            self._conn.execute(
                "INSERT INTO state(group_id, key, last_ts, payload, updated_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(group_id, key) DO UPDATE SET"
                " last_ts=excluded.last_ts,"
                " payload=excluded.payload,"
                " updated_at=excluded.updated_at",
                (group_id, key, last_ts,
                 json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                 int(time.time())),
            )
            self._conn.commit()