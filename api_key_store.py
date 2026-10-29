"""Persistent client API-key management.

Only SHA-256 hashes are stored. Newly generated key material is returned once
to the caller and is never recoverable from the database.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path


_DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "api_keys.sqlite3"
_STORE: "ApiKeyStore | None" = None
_STORE_LOCK = threading.Lock()


def _legacy_keys_from_env() -> list[str]:
    """Read old env-based keys for a one-time import into the key store."""
    candidates = [
        part.strip()
        for part in os.environ.get("API_KEYS", "").split(",")
        if part.strip()
    ]
    single = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if single:
        candidates.append(single)

    keys: list[str] = []
    seen: set[str] = set()
    for key in candidates:
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


class ApiKeyStore:
    """SQLite-backed API key metadata and hash store."""

    def __init__(self, path: Path, legacy_keys: list[str] | None = None):
        self.path = Path(path)
        self._legacy_keys = legacy_keys or []
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @staticmethod
    def _key_hash(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @classmethod
    def _prefix(cls, key: str) -> str:
        # Never expose an entire short legacy key in the UI.
        if len(key) >= 12:
            return key[:8]
        return f"legacy-{cls._key_hash(key)[:8]}"

    @classmethod
    def _insert_key(
        cls,
        conn: sqlite3.Connection,
        name: str,
        key: str,
        created_at: int,
    ) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO api_keys
               (id, name, key_hash, key_prefix, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                secrets.token_hex(8),
                name,
                cls._key_hash(key),
                cls._prefix(key),
                created_at,
            ),
        )

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    key_prefix TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )"""
            )
            # Fork feature: per-key DeepSeek account binding.
            cols = [row[1] for row in conn.execute("PRAGMA table_info(api_keys)").fetchall()]
            if "account_id" not in cols:
                conn.execute("ALTER TABLE api_keys ADD COLUMN account_id TEXT")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS api_key_store_meta (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )"""
            )
            conn.execute("BEGIN IMMEDIATE")
            imported = conn.execute(
                "SELECT value FROM api_key_store_meta WHERE name = ?",
                ("legacy_env_imported",),
            ).fetchone()
            if imported is None:
                now = int(time.time())
                for index, key in enumerate(self._legacy_keys, start=1):
                    self._insert_key(conn, f"Imported key {index}", key, now)
                conn.execute(
                    "INSERT INTO api_key_store_meta (name, value) VALUES (?, ?)",
                    ("legacy_env_imported", "1"),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        if os.name != "nt":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    @staticmethod
    def _public_record(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "prefix": row["key_prefix"],
            "created_at": row["created_at"],
            "account_id": row["account_id"] if "account_id" in row.keys() else None,
        }

    def list_keys(self) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name, key_prefix, created_at, account_id FROM api_keys "
                "ORDER BY created_at DESC, id DESC"
            ).fetchall()
            return [self._public_record(row) for row in rows]
        finally:
            conn.close()

    def has_keys(self) -> bool:
        conn = self._connect()
        try:
            return conn.execute("SELECT 1 FROM api_keys LIMIT 1").fetchone() is not None
        finally:
            conn.close()

    def verify(self, candidate: str) -> bool:
        if not candidate:
            return False
        digest = self._key_hash(candidate)
        conn = self._connect()
        try:
            rows = conn.execute("SELECT key_hash FROM api_keys").fetchall()
            matched = False
            for row in rows:
                matched = secrets.compare_digest(digest, row["key_hash"]) or matched
            return matched
        finally:
            conn.close()

    def account_id_for_key(self, candidate: str) -> str | None:
        """Return the DeepSeek account id bound to a client API key, or None.

        None means the key is unbound (round-robin pool behaviour) or unknown.
        """
        if not candidate:
            return None
        digest = self._key_hash(candidate)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT account_id FROM api_keys WHERE key_hash = ?", (digest,)
            ).fetchone()
            if row is None:
                return None
            bound = row["account_id"]
            return bound.strip() if bound and bound.strip() else None
        finally:
            conn.close()

    def create_key(self, name: str, account_id: str | None = None) -> dict:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Key name is required")
        if len(normalized_name) > 64:
            raise ValueError("Key name must be 64 characters or fewer")
        normalized_account = (account_id or "").strip() or None
        if normalized_account is not None and len(normalized_account) > 64:
            raise ValueError("account_id must be 64 characters or fewer")

        key = f"sk-{secrets.token_urlsafe(32)}"
        created_at = int(time.time())
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO api_keys
                   (id, name, key_hash, key_prefix, created_at, account_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    secrets.token_hex(8),
                    normalized_name,
                    self._key_hash(key),
                    self._prefix(key),
                    created_at,
                    normalized_account,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, name, key_prefix, created_at, account_id FROM api_keys WHERE key_hash = ?",
                (self._key_hash(key),),
            ).fetchone()
            return {"key": key, "api_key": self._public_record(row)}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def set_key_account(self, key_id: str, account_id: str | None) -> bool:
        """Bind (or unbind, with None) a client API key to a DeepSeek account."""
        normalized = (account_id or "").strip() or None
        if normalized is not None and len(normalized) > 64:
            raise ValueError("account_id must be 64 characters or fewer")
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE api_keys SET account_id = ? WHERE id = ?", (normalized, key_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def revoke_key(self, key_id: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_api_key_store() -> ApiKeyStore:
    """Return the process-wide store, importing legacy env keys only once."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                configured_path = os.environ.get("API_KEY_STORE_PATH", "").strip()
                path = Path(configured_path) if configured_path else _DEFAULT_PATH
                if not path.is_absolute():
                    path = Path(__file__).resolve().parent / path
                _STORE = ApiKeyStore(path, _legacy_keys_from_env())
    return _STORE
