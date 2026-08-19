# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — LOCAL SQLITE SECURITY DATABASE
#  Thread-safe SQLite database that serves as the single source of
#  truth for users, roles, devices, sessions, topic ACLs, and audit
#  logs. No internet dependency. No cloud services.
#
#  Schema:
#    users       — username, bcrypt password hash, role
#    roles       — role name
#    devices     — device_id, type, cert_fingerprint, enabled
#    sessions    — token, user_id, device_id, expiry
#    topic_acls  — role → topic → (can_publish, can_subscribe)
#    audit_logs  — timestamped security event log
#
#  Design rules:
#    - Never store plaintext passwords
#    - Never log passwords, tokens, or private keys
#    - All DB operations use thread-local connections (WAL mode)
#    - Long operations must NOT run in the network send path
# ═══════════════════════════════════════════════════════════════════

import sqlite3
import threading
import os
import glob
import shutil
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import bcrypt

log = logging.getLogger(__name__)

# Minimum password length
MIN_PASSWORD_LENGTH = 8

# Session lifetime
SESSION_LIFETIME_HOURS = 8


class AetherDatabase:
    """Thread-safe SQLite database manager for Aether security data.

    Uses thread-local connections with WAL journal mode so the broker
    and UI threads can safely access the DB concurrently without
    blocking real-time communication paths.

    Usage (singleton pattern — do not instantiate directly):
        db = AetherDatabase.instance()
        db.open(path)
    """

    _instance: Optional["AetherDatabase"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "AetherDatabase":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = AetherDatabase()
        return cls._instance

    def __init__(self):
        self._path: Optional[str] = None
        self._local = threading.local()   # thread-local connection storage
        self._init_lock = threading.Lock()
        self._ready = False
        self._audit_failures: int = 0    # Count of failed audit log writes
        self._backup_failed: bool = False  # Set True if last backup attempt failed

    # ─── Lifecycle ────────────────────────────────────────────────

    def open(self, path: str) -> bool:
        """Open (or create) the SQLite database at the given path.

        On open:
          1. Runs PRAGMA integrity_check FIRST. If the database is
             corrupt, logs CRITICAL and returns False — the caller must
             restore from a known-good backup.
          2. If integrity passes and the database already exists,
             creates a verified timestamped backup before proceeding.
          3. Initialises/upgrades schema and seeds roles/ACLs.

        Safe to call multiple times — idempotent after first success.
        Returns True on success, False on any unrecoverable error.
        """
        with self._init_lock:
            if self._ready:
                return True
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                self._path = path
                db_existed = os.path.exists(path)

                conn = self._get_conn()

                # ── Step 1: Integrity check (runs before anything else) ──
                if db_existed:
                    ok, detail = self._integrity_check(conn)
                    if not ok:
                        log.critical(
                            f"[DB] INTEGRITY CHECK FAILED: {detail}. "
                            "The database may be corrupt. "
                            "Restore from a known-good backup before starting."
                        )
                        self._path = None
                        return False
                    log.info("[DB] Integrity check passed")

                # ── Step 2: Backup existing DB (after integrity check) ────
                if db_existed:
                    self.auto_backup()

                # ── Step 3: Schema init and seeding ───────────────────────
                self._init_schema(conn)
                self._seed_roles_and_acls(conn)
                self._ready = True
                log.info(f"[DB] Database opened: {path}")
                return True
            except Exception as e:
                log.error(f"[DB] Failed to open database: {e}")
                return False

    @property
    def is_ready(self) -> bool:
        return self._ready

    # ─── Internal: Connection ─────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Return a thread-local SQLite connection (WAL mode)."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")  # Safe with WAL
            self._local.conn = conn
        return self._local.conn

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a statement and commit."""
        conn = self._get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur

    def _query(self, sql: str, params: tuple = ()) -> list:
        """Execute a SELECT and return all rows as dicts."""
        conn = self._get_conn()
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """Execute a SELECT and return the first row or None."""
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ─── Schema Initialisation ────────────────────────────────────

    def _init_schema(self, conn: sqlite3.Connection):
        """Create all tables if they do not already exist."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS roles (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                name    TEXT    NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    NOT NULL UNIQUE,
                password_hash   TEXT    NOT NULL,
                role_id         INTEGER NOT NULL REFERENCES roles(id),
                is_enabled      INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT    NOT NULL,
                last_login_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS devices (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id           TEXT    NOT NULL UNIQUE,
                device_type         TEXT    NOT NULL,
                cert_fingerprint    TEXT    NOT NULL UNIQUE,
                is_enabled          INTEGER NOT NULL DEFAULT 1,
                registered_at       TEXT    NOT NULL,
                last_seen_at        TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                token       TEXT    NOT NULL UNIQUE,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                device_id   INTEGER REFERENCES devices(id),
                created_at  TEXT    NOT NULL,
                expires_at  TEXT    NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS topic_acls (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                role            TEXT    NOT NULL,
                topic           TEXT    NOT NULL,
                can_publish     INTEGER NOT NULL DEFAULT 0,
                can_subscribe   INTEGER NOT NULL DEFAULT 0,
                UNIQUE(role, topic)
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                event_type  TEXT    NOT NULL,
                username    TEXT,
                device_id   TEXT,
                details     TEXT,
                ip_address  TEXT
            );
        """)
        conn.commit()
        log.debug("[DB] Schema initialised")

    # ─── Integrity Check ──────────────────────────────────────────

    def _integrity_check(self, conn: sqlite3.Connection) -> Tuple[bool, str]:
        """Run SQLite PRAGMA integrity_check.

        Returns (True, 'ok') if the database is intact, or
        (False, detail) describing the first integrity error.

        MUST be called before any application data is read or written.
        Do NOT proceed with a corrupt database.
        """
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            # SQLite returns a single row 'ok' when the DB is clean.
            if len(rows) == 1 and rows[0][0] == "ok":
                return True, "ok"
            # Multiple rows = list of errors
            details = "; ".join(row[0] for row in rows)
            return False, details
        except Exception as e:
            return False, str(e)

    # ─── Backup / Auto-Backup ─────────────────────────────────────

    def _get_backup_dir(self) -> str:
        """Return the backups directory, creating it if necessary."""
        db_dir = os.path.dirname(self._path)
        backup_dir = os.path.join(db_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        return backup_dir

    def auto_backup(self) -> bool:
        """Create a verified timestamped backup of the database.

        Steps:
          1. Create a timestamped copy using the SQLite backup API.
          2. Open the copy and run PRAGMA integrity_check on it.
          3. If verification fails, delete the bad copy, set
             _backup_failed=True, and log an ERROR.
          4. Rotate old backups to keep at most DB_BACKUP_KEEP_COUNT.

        Returns True if the backup was created and verified.
        """
        if not self._path or not os.path.exists(self._path):
            log.error("[DB] auto_backup: database not open or file missing")
            return False

        from shared_networking.config import DB_BACKUP_KEEP_COUNT

        backup_dir = self._get_backup_dir()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = os.path.join(backup_dir, f"aether_{ts}.db")

        try:
            src = sqlite3.connect(self._path)
            dst = sqlite3.connect(backup_path)
            src.backup(dst)
            dst.close()
            src.close()
        except Exception as e:
            self._backup_failed = True
            log.error(f"[DB] auto_backup: backup creation failed: {e}")
            return False

        # Verify the backup is readable and intact
        try:
            verify_conn = sqlite3.connect(backup_path)
            rows = verify_conn.execute("PRAGMA integrity_check").fetchall()
            verify_conn.close()
            if not (len(rows) == 1 and rows[0][0] == "ok"):
                raise ValueError(f"integrity_check failed on backup: {rows}")
        except Exception as e:
            self._backup_failed = True
            log.error(f"[DB] auto_backup: backup verification failed: {e} — "
                      f"deleting bad backup {backup_path}")
            try:
                os.remove(backup_path)
            except OSError:
                pass
            return False

        self._backup_failed = False
        log.info(f"[DB] Backup created and verified: {backup_path}")

        # Rotate: keep only the last DB_BACKUP_KEEP_COUNT backups
        self._rotate_backups(backup_dir, DB_BACKUP_KEEP_COUNT)
        return True

    def _rotate_backups(self, backup_dir: str, keep: int):
        """Delete oldest backups, keeping only the most recent `keep` files."""
        try:
            pattern = os.path.join(backup_dir, "aether_*.db")
            existing = sorted(glob.glob(pattern))
            excess = existing[:max(0, len(existing) - keep)]
            for old in excess:
                try:
                    os.remove(old)
                    log.info(f"[DB] Rotated old backup: {old}")
                except OSError as e:
                    log.warning(f"[DB] Could not remove old backup {old}: {e}")
        except Exception as e:
            log.warning(f"[DB] Backup rotation error: {e}")

    def checkpoint(self):
        """Run a WAL checkpoint as normal maintenance.

        Call on clean broker shutdown or after periodic backup.
        This is ordinary WAL housekeeping — NOT a corruption-recovery
        mechanism. Do not call on a database that has failed integrity check.
        """
        try:
            conn = self._get_conn()
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            log.debug("[DB] WAL checkpoint completed")
        except Exception as e:
            log.warning(f"[DB] WAL checkpoint failed: {e}")

    @property
    def backup_failed(self) -> bool:
        """True if the last backup attempt failed."""
        return self._backup_failed

    def _seed_roles_and_acls(self, conn: sqlite3.Connection):
        """Insert built-in roles and topic ACLs if not already present.

        ACLs are mapped to the exact existing topic names from
        shared_networking/message_types.py. No topic names are invented.
        """
        # Seed roles
        built_in_roles = ["admin", "user", "data_generator"]
        for role in built_in_roles:
            conn.execute(
                "INSERT OR IGNORE INTO roles (name) VALUES (?)", (role,))

        # ── Topic ACL Table ───────────────────────────────────────
        # Format: (role, topic, can_publish, can_subscribe)
        acls = [
            # ── admin: full access ────────────────────────────────
            ("admin", "patient_vitals",     1, 1),
            ("admin", "robot_telemetry",    1, 1),
            ("admin", "alerts",             1, 1),
            ("admin", "system_status",      1, 1),
            ("admin", "connection_status",  1, 1),
            ("admin", "video_broadcast",    1, 1),
            ("admin", "video_frame",        1, 1),
            ("admin", "video_detection",    1, 1),
            ("admin", "system_logs",        1, 1),
            ("admin", "robot_commands",     1, 1),
            ("admin", "system_control",     1, 1),
            ("admin", "robot_status",       1, 1),

            # ── user (surgeon): subscribe to data, publish control ─
            ("user", "patient_vitals",      0, 1),
            ("user", "robot_telemetry",     0, 1),
            ("user", "robot_status",        0, 1),
            ("user", "alerts",              0, 1),
            ("user", "connection_status",   0, 1),
            ("user", "system_status",       0, 1),
            ("user", "video_broadcast",     0, 1),
            ("user", "video_frame",         0, 1),
            ("user", "video_detection",     0, 1),
            ("user", "system_logs",         0, 1),
            ("user", "system_control",      1, 0),

            # ── data_generator: publish data streams only ──────────
            ("data_generator", "patient_vitals",    1, 0),
            ("data_generator", "robot_telemetry",   1, 0),
            ("data_generator", "alerts",            1, 0),
            ("data_generator", "connection_status", 1, 0),

            # ── robot_console: publish video/status, subscribe data ─
            ("robot_console", "connection_status",  1, 0),
            ("robot_console", "video_broadcast",    1, 0),
            ("robot_console", "robot_telemetry",    0, 1),
            ("robot_console", "patient_vitals",     0, 1),
            ("robot_console", "alerts",             0, 1),
            ("robot_console", "robot_commands",     0, 1),
            ("robot_console", "system_control",     0, 1),

            # ── observer: subscribe only ───────────────────────────
            ("observer", "robot_telemetry",     0, 1),
            ("observer", "robot_status",        0, 1),
            ("observer", "patient_vitals",      0, 1),
            ("observer", "alerts",              0, 1),
            ("observer", "video_frame",         0, 1),
            ("observer", "video_detection",     0, 1),
            ("observer", "system_logs",         0, 1),
            ("observer", "connection_status",   0, 1),
            ("observer", "system_status",       0, 1),
        ]
        for role, topic, can_pub, can_sub in acls:
            conn.execute(
                "INSERT OR IGNORE INTO topic_acls "
                "(role, topic, can_publish, can_subscribe) VALUES (?,?,?,?)",
                (role, topic, can_pub, can_sub),
            )
        conn.commit()
        log.debug("[DB] Roles and ACLs seeded")

    # ─── User Management ──────────────────────────────────────────

    def is_first_run(self) -> bool:
        """Return True if no users exist in the database yet."""
        row = self._query_one("SELECT COUNT(*) as cnt FROM users")
        return (row["cnt"] == 0) if row else True

    def create_user(self, username: str, password: str,
                    role: str) -> Tuple[bool, str]:
        """Create a new user. Only for admin use — not public registration.

        Returns (True, "") on success or (False, error_message) on failure.
        """
        if not username or not username.strip():
            return False, "Username cannot be empty."
        if len(password) < MIN_PASSWORD_LENGTH:
            return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."

        username = username.strip().lower()
        row = self._query_one("SELECT id FROM roles WHERE name = ?", (role,))
        if not row:
            return False, f"Unknown role: {role}"
        role_id = row["id"]

        existing = self._query_one(
            "SELECT id FROM users WHERE username = ?", (username,))
        if existing:
            return False, f"Username already exists: {username}"

        password_hash = self._hash_password(password)
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._exec(
                "INSERT INTO users (username, password_hash, role_id, "
                "created_at) VALUES (?,?,?,?)",
                (username, password_hash, role_id, now),
            )
            self.audit(
                "USER_CREATED", username=username,
                details=f"role={role}")
            log.info(f"[DB] User created: {username} (role={role})")
            return True, ""
        except Exception as e:
            log.error(f"[DB] create_user failed: {e}")
            return False, str(e)

    def verify_user(self, username: str, password: str) -> Tuple[bool, str]:
        """Verify username + password. Returns (True, role) or (False, '').

        IMPORTANT: The returned role is authoritative — never trust
        the client's claimed role.
        """
        if not username or not password:
            return False, ""

        username = username.strip().lower()
        row = self._query_one(
            "SELECT u.id, u.password_hash, u.is_enabled, r.name as role "
            "FROM users u JOIN roles r ON u.role_id = r.id "
            "WHERE u.username = ?",
            (username,),
        )
        if not row:
            log.warning(f"[DB] Login failed — unknown user: {username}")
            self.audit("LOGIN_FAILED", username=username,
                       details="unknown user")
            return False, ""

        if not row["is_enabled"]:
            log.warning(f"[DB] Login failed — disabled account: {username}")
            self.audit("LOGIN_FAILED", username=username,
                       details="account disabled")
            return False, ""

        try:
            if bcrypt.checkpw(password.encode("utf-8"),
                              row["password_hash"].encode("utf-8")):
                now = datetime.now(timezone.utc).isoformat()
                self._exec(
                    "UPDATE users SET last_login_at = ? WHERE username = ?",
                    (now, username),
                )
                log.info(f"[DB] Login success: {username} (role={row['role']})")
                self.audit("LOGIN_SUCCESS", username=username,
                           details=f"role={row['role']}")
                return True, row["role"]
            else:
                log.warning(f"[DB] Login failed — wrong password: {username}")
                self.audit("LOGIN_FAILED", username=username,
                           details="wrong password")
                return False, ""
        except Exception as e:
            log.error(f"[DB] Password verification error: {e}")
            return False, ""

    def change_password(self, username: str, current_password: str,
                        new_password: str) -> Tuple[bool, str]:
        """Change a user's own password after verifying the current one."""
        success, _ = self.verify_user(username, current_password)
        if not success:
            return False, "Current password is incorrect."
        if len(new_password) < MIN_PASSWORD_LENGTH:
            return False, (f"New password must be at least "
                           f"{MIN_PASSWORD_LENGTH} characters.")
        new_hash = self._hash_password(new_password)
        self._exec(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (new_hash, username),
        )
        self.audit("PASSWORD_CHANGED", username=username)
        log.info(f"[DB] Password changed: {username}")
        return True, "Password changed successfully."

    def get_user_role(self, username: str) -> Optional[str]:
        """Return the authoritative role for a username, or None."""
        row = self._query_one(
            "SELECT r.name as role FROM users u "
            "JOIN roles r ON u.role_id = r.id "
            "WHERE u.username = ? AND u.is_enabled = 1",
            (username.strip().lower(),),
        )
        return row["role"] if row else None

    # ─── Session Management ───────────────────────────────────────

    def create_session(self, username: str, role: str,
                       device_id: Optional[str] = None) -> str:
        """Create a server-side session with a secure random token.

        Returns the session token (never the user's credentials).
        """
        import secrets
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=SESSION_LIFETIME_HOURS)

        user_row = self._query_one(
            "SELECT id FROM users WHERE username = ?",
            (username.strip().lower(),),
        )
        user_id = user_row["id"] if user_row else None

        dev_row_id = None
        if device_id:
            dev_row = self._query_one(
                "SELECT id FROM devices WHERE device_id = ?", (device_id,))
            dev_row_id = dev_row["id"] if dev_row else None

        self._exec(
            "INSERT INTO sessions (token, user_id, device_id, "
            "created_at, expires_at) VALUES (?,?,?,?,?)",
            (token, user_id, dev_row_id, now.isoformat(), expires.isoformat()),
        )
        log.info(f"[DB] Session created for {username}, "
                 f"expires {expires.strftime('%H:%M UTC')}")
        return token

    def validate_session(self, token: str,
                         device_id: Optional[str] = None) -> Tuple[bool, str, str]:
        """Validate a session token.

        Returns (True, username, authoritative_role) or (False, '', '').
        The role is ALWAYS read from the database — never from the client.

        If device_id is provided, the session must have been created on
        that device (session/device binding). Sessions created without a
        device_id (nullable FK) are accepted from any device — this covers
        the case where the session was created before device binding was
        enforced. Service devices never use session tokens, so this check
        only affects human operator sessions.
        """
        if not token:
            return False, "", ""
        # expires_at is stored as UTC ISO 8601 string; lexicographic
        # comparison is correct for this format (YYYY-MM-DDTHH:MM:SS+00:00).
        now = datetime.now(timezone.utc).isoformat()

        if device_id:
            # Enforce device binding: session's device_id FK must match.
            # Sessions with NULL device_id are still accepted (legacy compat).
            row = self._query_one(
                "SELECT s.is_active, s.expires_at, u.username, r.name as role "
                "FROM sessions s "
                "JOIN users u ON s.user_id = u.id "
                "JOIN roles r ON u.role_id = r.id "
                "LEFT JOIN devices d ON s.device_id = d.id "
                "WHERE s.token = ? AND s.is_active = 1 AND s.expires_at > ? "
                "AND (d.device_id = ? OR s.device_id IS NULL)",
                (token, now, device_id),
            )
        else:
            row = self._query_one(
                "SELECT s.is_active, s.expires_at, u.username, r.name as role "
                "FROM sessions s "
                "JOIN users u ON s.user_id = u.id "
                "JOIN roles r ON u.role_id = r.id "
                "WHERE s.token = ? AND s.is_active = 1 AND s.expires_at > ?",
                (token, now),
            )
        if not row:
            return False, "", ""
        return True, row["username"], row["role"]

    def invalidate_session(self, token: str):
        """Mark a session as inactive (logout / security failure)."""
        if token:
            self._exec(
                "UPDATE sessions SET is_active = 0 WHERE token = ?",
                (token,),
            )
            log.info("[DB] Session invalidated")

    def cleanup_expired_sessions(self):
        """Remove expired sessions from the database. Call periodically."""
        now = datetime.now(timezone.utc).isoformat()
        self._exec(
            "DELETE FROM sessions WHERE expires_at < ? OR is_active = 0",
            (now,),
        )

    # ─── Device Management ────────────────────────────────────────

    def register_device(self, device_id: str, device_type: str,
                        cert_fingerprint: str) -> bool:
        """Register a device with its certificate fingerprint.

        Returns True if newly registered, False if already exists.
        """
        existing = self._query_one(
            "SELECT id FROM devices WHERE device_id = ?", (device_id,))
        if existing:
            return False
        now = datetime.now(timezone.utc).isoformat()
        self._exec(
            "INSERT INTO devices (device_id, device_type, cert_fingerprint, "
            "registered_at) VALUES (?,?,?,?)",
            (device_id, device_type, cert_fingerprint, now),
        )
        self.audit("DEVICE_REGISTERED", device_id=device_id,
                   details=f"type={device_type}")
        log.info(f"[DB] Device registered: {device_id} ({device_type})")
        return True

    def verify_device(self, cert_fingerprint: str) -> Tuple[bool, str, str]:
        """Verify a device by certificate fingerprint.

        Returns (True, device_id, device_type) or (False, '', '').
        """
        row = self._query_one(
            "SELECT device_id, device_type, is_enabled "
            "FROM devices WHERE cert_fingerprint = ?",
            (cert_fingerprint,),
        )
        if not row:
            return False, "", ""
        if not row["is_enabled"]:
            self.audit("DEVICE_AUTH_FAILED",
                       device_id=row["device_id"],
                       details="device disabled/revoked")
            return False, "", ""
        now = datetime.now(timezone.utc).isoformat()
        self._exec(
            "UPDATE devices SET last_seen_at = ? WHERE cert_fingerprint = ?",
            (now, cert_fingerprint),
        )
        return True, row["device_id"], row["device_type"]

    def revoke_device(self, device_id: str):
        """Disable/revoke a registered device."""
        self._exec(
            "UPDATE devices SET is_enabled = 0 WHERE device_id = ?",
            (device_id,),
        )
        self.audit("DEVICE_REVOKED", device_id=device_id)
        log.info(f"[DB] Device revoked: {device_id}")

    # ─── Topic ACL ────────────────────────────────────────────────

    def check_acl(self, role: str, topic: str,
                  action: str) -> bool:
        """Check if a role is permitted to perform action on a topic.

        action must be 'publish' or 'subscribe'.
        Returns True if permitted, False otherwise.
        Deny by default.
        """
        if action == "publish":
            col = "can_publish"
        elif action == "subscribe":
            col = "can_subscribe"
        else:
            return False

        row = self._query_one(
            f"SELECT {col} as allowed FROM topic_acls "
            "WHERE role = ? AND topic = ?",
            (role, topic),
        )
        if not row:
            return False
        return bool(row["allowed"])

    # ─── Audit Logging ────────────────────────────────────────────

    def audit(self, event_type: str, username: Optional[str] = None,
              device_id: Optional[str] = None,
              details: Optional[str] = None,
              ip_address: Optional[str] = None):
        """Append an immutable security audit log entry.

        NEVER log passwords, tokens, private keys, or sensitive secrets.
        Audit failures are counted and logged but do not crash the application.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._exec(
                "INSERT INTO audit_logs (timestamp, event_type, username, "
                "device_id, details, ip_address) VALUES (?,?,?,?,?,?)",
                (now, event_type, username, device_id, details, ip_address),
            )
        except Exception as e:
            # Count audit failures so the operator can detect degraded audit state.
            self._audit_failures += 1
            log.error(
                f"[DB] Audit log write failed (total failures: "
                f"{self._audit_failures}): {e}"
            )

    @property
    def audit_health(self) -> tuple:
        """Return audit subsystem health as (status, failure_count).

        status is 'OK' when no write failures have occurred since startup,
        or 'DEGRADED' if one or more audit writes failed.
        Exposing this in the UI or status API is recommended but not required.
        """
        if self._audit_failures == 0:
            return ("OK", 0)
        return ("DEGRADED", self._audit_failures)

    def get_recent_audit_logs(self, limit: int = 100) -> list:
        """Return the most recent audit log entries."""
        return self._query(
            "SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )

    # ─── Backup / Recovery ────────────────────────────────────────

    def backup(self, backup_path: str) -> bool:
        """Create a local backup of the database file.

        The backup is a plain SQLite copy. It contains hashed passwords
        only (never plaintext passwords or private keys).
        Backup files must be in .gitignore.

        Returns True on success.
        """
        if not self._path or not os.path.exists(self._path):
            log.error("[DB] Cannot backup — database not open")
            return False
        try:
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            # Use SQLite's built-in backup API for consistency
            src_conn = sqlite3.connect(self._path)
            dst_conn = sqlite3.connect(backup_path)
            src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()
            log.info(f"[DB] Backup created: {backup_path}")
            return True
        except Exception as e:
            log.error(f"[DB] Backup failed: {e}")
            return False

    # ─── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _hash_password(password: str) -> str:
        """Hash a password with bcrypt (cost factor 12)."""
        salt = bcrypt.gensalt(rounds=12)
        hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
        return hashed.decode("utf-8")
