# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — DATABASE MIGRATION ENGINE
#
#  Applies incremental, idempotent schema migrations tracked by
#  PRAGMA user_version (SQLite's built-in schema version integer).
#
#  Design rules:
#    - Each migration step is idempotent (safe to re-run).
#    - Column additions check PRAGMA table_info before ALTER TABLE.
#    - SQLite forbids UNIQUE/NOT NULL without DEFAULT on ALTER TABLE —
#      handled by creating UNIQUE indexes separately.
#    - All data backfills use UPDATE WHERE column IS NULL.
#    - The entire migration batch runs in one transaction; any failure
#      rolls back ALL changes for that batch.
#    - NEVER drops tables, NEVER drops columns, NEVER modifies password
#      hashes or existing credential data.
#
#  Adding a new migration:
#    1. Bump SCHEMA_VERSION by 1.
#    2. Write a _mNNN_description(conn) function.
#    3. Add an `if current_version < N:` block in run_migrations().
# ═══════════════════════════════════════════════════════════════════

import logging
import re
import sqlite3
import uuid as _uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ── Current target schema version ────────────────────────────────
SCHEMA_VERSION = 2


# ─── Public API ───────────────────────────────────────────────────

def run_migrations(conn: sqlite3.Connection) -> bool:
    """Apply all pending migrations. Idempotent.

    Reads PRAGMA user_version to determine which migrations have
    already been applied. Sets user_version after each successful
    batch. A failure rolls back the entire batch and returns False.

    Args:
        conn: An open SQLite connection (WAL mode recommended).

    Returns:
        True if the schema is at SCHEMA_VERSION, False on error.
    """
    current = _get_schema_version(conn)
    log.info(
        f"[MIGRATION] Schema version: {current} (target: {SCHEMA_VERSION})"
    )

    if current >= SCHEMA_VERSION:
        log.debug("[MIGRATION] Schema is up-to-date. No migrations needed.")
        return True

    try:
        if current < 1:
            log.info("[MIGRATION] Running M001 — Multi-Hospital Foundation")
            _m001_multi_hospital_foundation(conn)
            _set_schema_version(conn, 1)
            conn.commit()
            log.info("[MIGRATION] M001 complete — schema version set to 1")
            current = 1

        if current < 2:
            log.info("[MIGRATION] Running M002 — RBAC Permissions + Legacy Admin Migration")
            _m002_rbac_permissions(conn)
            _set_schema_version(conn, 2)
            conn.commit()
            log.info("[MIGRATION] M002 complete — schema version set to 2")

        return True

    except Exception as exc:
        log.error(f"[MIGRATION] FAILED — rolling back: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def generate_user_id_base(hospital_code: str, first_name: str,
                          employee_id: str) -> str:
    """Generate the base human-readable user ID (no collision handling).

    Formula:
        HOSPITAL_CODE[:2].upper()  +  FIRST_NAME[:2].upper()  +  EMP_ID[:2].upper()

    Examples:
        generate_user_id_base("APOLLO", "Karthik", "47291") → "APKA47"
        generate_user_id_base("Apollo Hospital", "karthik", "47291") → "APKA47"
        generate_user_id_base("AIIMS", "Priya", "12345") → "AIPR12"

    Only alphabetic characters are accepted for hospital_code and first_name;
    alphanumeric characters for employee_id. All others are stripped.

    Args:
        hospital_code: Hospital name or short code (first 2 alpha chars used).
        first_name:    User's given name (first 2 alpha chars used).
        employee_id:   Staff badge / HR ID (first 2 alphanumeric chars used).

    Returns:
        A 6-character base ID string, possibly shorter if inputs are short.
    """
    hosp  = re.sub(r"[^A-Z]", "",       (hospital_code or "").upper())[:2]
    fname = re.sub(r"[^A-Z]", "",       (first_name   or "").upper())[:2]
    emp   = re.sub(r"[^A-Z0-9]", "",    (employee_id  or "").upper())[:2]
    return hosp + fname + emp


def make_unique_user_id(conn: sqlite3.Connection, base_id: str) -> str:
    """Return a guaranteed-unique user_id, appending dash suffixes on collision.

    Collision sequence:
        APKA47  →  APKA47-01  →  APKA47-02  →  ...  →  APKA47-99

    The base is always preserved; only a dash-numeric suffix is appended.
    This differs from modifying the employee-ID portion, which would produce
    confusing IDs like APKA472 that look like a different employee.

    Args:
        conn:    Open SQLite connection to query existing user_id values.
        base_id: The base user_id (e.g. "APKA47").

    Returns:
        A user_id string not currently present in the users table.

    Raises:
        ValueError: If 99 suffixes are exhausted without finding a free slot.
    """
    if not _user_id_exists(conn, base_id):
        return base_id
    for i in range(1, 100):
        candidate = f"{base_id}-{i:02d}"
        if not _user_id_exists(conn, candidate):
            return candidate
    raise ValueError(
        f"Cannot generate a unique user_id for base '{base_id}' — "
        "all 99 dash-suffixed variants are taken."
    )


# ─── Internal helpers ─────────────────────────────────────────────

def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Read PRAGMA user_version (SQLite built-in, always 0 on new DBs)."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return row[0] if row else 0


def _set_schema_version(conn: sqlite3.Connection, version: int):
    """Write PRAGMA user_version — must be a bare integer, not a parameter."""
    conn.execute(f"PRAGMA user_version = {version}")


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if `column` exists in `table`."""
    cols = [
        row[1]
        for row in conn.execute(f"PRAGMA table_info([{table}])").fetchall()
    ]
    return column in cols


def _add_column_if_missing(conn: sqlite3.Connection, table: str,
                           column: str, definition: str):
    """ALTER TABLE to add `column` only when it is absent.

    SQLite forbids adding columns with UNIQUE or a NOT NULL constraint
    without a DEFAULT. Handle these by adding the column nullable and
    creating a UNIQUE INDEX separately in _create_indexes().
    """
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE [{table}] ADD COLUMN {column} {definition}")
        log.debug(f"[MIGRATION] Added column {table}.{column}")
    else:
        log.debug(f"[MIGRATION] Column {table}.{column} already present — skip")


def _user_id_exists(conn: sqlite3.Connection, user_id: str) -> bool:
    """Return True if the given user_id is already taken in users."""
    row = conn.execute(
        "SELECT id FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row is not None


# ─── M001 — Multi-Hospital Foundation ────────────────────────────

def _m001_multi_hospital_foundation(conn: sqlite3.Connection):
    """Apply M001: multi-hospital columns, roles with scope, indexes, backfill.

    Steps (each individually idempotent):
        1.  Add scope column to roles
        2.  Update scope for pre-existing roles
        3.  Seed new roles (app_admin, hospital_admin, technician, surgeon,
            doctor, observer) with correct scope
        4.  Add new columns to users  (uuid, user_id, hospital_id, names,
            employee_id, department_id, lockout fields, password metadata)
        5.  Add new columns to sessions  (hospital_id, last_activity_at)
        6.  Backfill existing users: generate uuid and user_id
        7.  Create indexes
    """

    # ── 1. scope column on roles ───────────────────────────────────
    _add_column_if_missing(
        conn, "roles", "scope",
        "TEXT NOT NULL DEFAULT 'HOSPITAL'"
    )

    # ── 2. Assign scope to pre-existing roles ──────────────────────
    _PRE_EXISTING_SCOPES = {
        "admin":          "APPLICATION",
        "user":           "HOSPITAL",
        "data_generator": "DEVICE",
    }
    for role_name, scope in _PRE_EXISTING_SCOPES.items():
        conn.execute(
            "UPDATE roles SET scope = ? WHERE name = ?", (scope, role_name)
        )

    # ── 3. Seed new roles ─────────────────────────────────────────
    _NEW_ROLES = [
        ("app_admin",      "APPLICATION"),
        ("hospital_admin", "HOSPITAL"),
        ("technician",     "HOSPITAL"),
        ("surgeon",        "HOSPITAL"),
        ("doctor",         "HOSPITAL"),
        ("observer",       "HOSPITAL"),
    ]
    for name, scope in _NEW_ROLES:
        conn.execute(
            "INSERT OR IGNORE INTO roles (name, scope) VALUES (?, ?)",
            (name, scope),
        )

    # ── 4. Extend users table ──────────────────────────────────────
    # Note: SQLite does not allow UNIQUE or NOT NULL without DEFAULT on
    # ALTER TABLE ADD COLUMN. UNIQUE constraints are enforced via indexes
    # created in step 7 instead.
    _add_column_if_missing(conn, "users", "uuid",        "TEXT")
    _add_column_if_missing(conn, "users", "user_id",     "TEXT")
    _add_column_if_missing(conn, "users", "hospital_id",
                           "TEXT REFERENCES hospitals(id)")
    _add_column_if_missing(conn, "users", "first_name",  "TEXT")
    _add_column_if_missing(conn, "users", "last_name",   "TEXT")
    _add_column_if_missing(conn, "users", "employee_id", "TEXT")
    _add_column_if_missing(conn, "users", "department_id",
                           "INTEGER REFERENCES departments(id)")
    _add_column_if_missing(conn, "users", "failed_login_attempts",
                           "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "users", "locked_until", "TEXT")
    _add_column_if_missing(conn, "users", "force_password_change",
                           "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "users", "password_changed_at", "TEXT")

    # ── 5. Extend sessions table ───────────────────────────────────
    _add_column_if_missing(conn, "sessions", "hospital_id",
                           "TEXT REFERENCES hospitals(id)")
    _add_column_if_missing(conn, "sessions", "last_activity_at", "TEXT")

    # ── 6. Backfill existing users ─────────────────────────────────
    _backfill_existing_users(conn)

    # ── 7. Create indexes ─────────────────────────────────────────
    _create_indexes(conn)


def _backfill_existing_users(conn: sqlite3.Connection):
    """Assign uuid and user_id to pre-migration users that have neither.

    hospital_id remains NULL for pre-existing users — they were
    provisioned before the multi-hospital model existed and are treated
    as application-scope accounts.

    user_id for pre-migration users follows the pattern:
        SYS + username[:2].upper() + "01"
    e.g. admin → SYSAD01
    """
    rows = conn.execute(
        "SELECT id, username FROM users WHERE uuid IS NULL"
    ).fetchall()

    for user_pk, username in rows:
        new_uuid = str(_uuid.uuid4())
        base_id  = _derive_backfill_user_id(username)
        try:
            unique_id = make_unique_user_id(conn, base_id)
        except ValueError:
            # Extreme edge case — use uuid prefix as fallback
            unique_id = "SYS" + str(new_uuid)[:5].upper()

        conn.execute(
            "UPDATE users SET uuid = ?, user_id = ? WHERE id = ?",
            (new_uuid, unique_id, user_pk),
        )
        log.info(
            f"[MIGRATION] Backfilled user '{username}': "
            f"uuid={new_uuid[:8]}... user_id={unique_id} "
            f"hospital_id=NULL (application scope)"
        )


def _derive_backfill_user_id(username: str) -> str:
    """Derive a SYS-prefixed backfill ID for pre-migration users.

    Pre-migration users have no hospital or name data, so the standard
    HOSP+FNAME+EMP formula cannot be applied.  These users receive a
    SYS-prefixed identifier:
        SYS + first 2 alpha chars of username + "01"
    e.g. "admin" → "SYSAD01"
    """
    clean = re.sub(r"[^A-Z0-9]", "", username.upper())[:2]
    return f"SYS{clean}01"


def _create_indexes(conn: sqlite3.Connection):
    """Create all M001 indexes. All are CREATE INDEX IF NOT EXISTS (idempotent).

    The UNIQUE indexes on users.uuid and users.user_id enforce uniqueness
    that ALTER TABLE ADD COLUMN cannot declare directly.
    """
    statements = [
        # Unique identity indexes (enforces uniqueness via index, not column def)
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_uuid    ON users(uuid)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)",
        # Lookup indexes
        "CREATE        INDEX IF NOT EXISTS idx_users_hospital   ON users(hospital_id)",
        "CREATE        INDEX IF NOT EXISTS idx_sessions_hospital ON sessions(hospital_id)",
        "CREATE        INDEX IF NOT EXISTS idx_dept_hospital     ON departments(hospital_id)",
    ]
    for stmt in statements:
        conn.execute(stmt)
        log.debug(f"[MIGRATION] Index: {stmt.split('ON')[1].strip()}")


# ─── M002 — RBAC Permissions + Legacy Admin Migration ────────────

# Permission names — canonical source for seeding.
# These MUST match the constants in shared_networking/authorization.py.
_M002_PERMISSIONS = [
    ("HOSPITAL_MANAGE",          "Create, edit, and deactivate hospitals"),
    ("USER_MANAGE",              "Create, edit, and disable users within scope"),
    ("ROLE_MANAGE",              "Assign and change user roles within scope"),
    ("PASSWORD_RESET_ADMIN",     "Administrative password reset (APP_ADMIN only)"),
    ("PASSWORD_RESET_RECOVERY",  "Initiate recovery-code-based password reset (Phase 6)"),
    ("DEVICE_MANAGE",            "Register and revoke devices"),
    ("SURGERY_MANAGE",           "Create and edit surgical procedures"),
    ("AUDIT_ACCESS",             "View audit logs"),
    ("APP_CONFIG",               "Modify application configuration"),
]

# Role → permissions mapping.
# Only roles that HAVE a given permission are listed.
_M002_ROLE_PERMISSIONS = {
    "app_admin": [
        "HOSPITAL_MANAGE",
        "USER_MANAGE",
        "ROLE_MANAGE",
        "PASSWORD_RESET_ADMIN",
        "DEVICE_MANAGE",
        "SURGERY_MANAGE",
        "AUDIT_ACCESS",
        "APP_CONFIG",
    ],
    "hospital_admin": [
        "USER_MANAGE",
        "ROLE_MANAGE",
        "PASSWORD_RESET_RECOVERY",
        "DEVICE_MANAGE",
        "SURGERY_MANAGE",
        "AUDIT_ACCESS",
    ],
    "technician": [
        "DEVICE_MANAGE",
    ],
    "surgeon": [
        "SURGERY_MANAGE",
    ],
    "doctor": [
        "SURGERY_MANAGE",
    ],
    # observer: no permissions
}


def _m002_rbac_permissions(conn: sqlite3.Connection):
    """Apply M002: permissions tables, role-permission seeding, legacy admin migration.

    Steps (each individually idempotent):
        1.  Create permissions table
        2.  Create role_permissions junction table
        3.  Seed permission rows
        4.  Seed role-permission mappings per the RBAC matrix
        5.  Migrate legacy 'admin' users to 'app_admin' role
        6.  Create indexes on role_permissions

    Security rules:
        - Legacy admin migration preserves UUID, credentials, audit history.
        - Legacy 'user' role is NOT auto-mapped to 'surgeon'.
        - No audit events are fabricated for historical periods.
        - The legacy 'admin' role row is kept (not deleted) for referential safety.
    """

    # ── 1. permissions table ───────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permissions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            description TEXT
        )
    """)

    # ── 2. role_permissions junction table ─────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS role_permissions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            role_id       INTEGER NOT NULL REFERENCES roles(id),
            permission_id INTEGER NOT NULL REFERENCES permissions(id),
            UNIQUE(role_id, permission_id)
        )
    """)

    # ── 3. Seed permissions ────────────────────────────────────────
    for perm_name, perm_desc in _M002_PERMISSIONS:
        conn.execute(
            "INSERT OR IGNORE INTO permissions (name, description) VALUES (?, ?)",
            (perm_name, perm_desc),
        )
    log.debug("[MIGRATION] M002: permissions seeded")

    # ── 4. Seed role-permission mappings ───────────────────────────
    for role_name, perm_names in _M002_ROLE_PERMISSIONS.items():
        role_row = conn.execute(
            "SELECT id FROM roles WHERE name = ?", (role_name,)
        ).fetchone()
        if not role_row:
            log.warning(
                f"[MIGRATION] M002: role '{role_name}' not found — "
                "skipping permission mapping"
            )
            continue
        role_id = role_row[0]

        for perm_name in perm_names:
            perm_row = conn.execute(
                "SELECT id FROM permissions WHERE name = ?", (perm_name,)
            ).fetchone()
            if not perm_row:
                log.warning(
                    f"[MIGRATION] M002: permission '{perm_name}' not found — "
                    "skipping"
                )
                continue
            perm_id = perm_row[0]
            conn.execute(
                "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) "
                "VALUES (?, ?)",
                (role_id, perm_id),
            )
    log.debug("[MIGRATION] M002: role-permission mappings seeded")

    # ── 5. Migrate legacy 'admin' → 'app_admin' ───────────────────
    _migrate_legacy_admin(conn)

    # ── 6. Indexes ─────────────────────────────────────────────────
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_role_permissions_role "
        "ON role_permissions(role_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_role_permissions_perm "
        "ON role_permissions(permission_id)"
    )
    log.debug("[MIGRATION] M002: indexes created")


def _migrate_legacy_admin(conn: sqlite3.Connection):
    """Migrate users with the legacy 'admin' role to 'app_admin'.

    Preserves:
        - UUID (immutable canonical identity)
        - username
        - password_hash (credentials unchanged)
        - created_at, last_login_at
        - All audit_logs referencing this username

    The legacy 'admin' role row is NOT deleted from the roles table
    (avoids orphaned FK references in audit_logs.details or topic_acls).

    Legacy 'user' role is NOT auto-mapped to 'surgeon' — these are
    semantically different and require explicit verification.
    """
    # Find the admin and app_admin role IDs
    admin_row = conn.execute(
        "SELECT id FROM roles WHERE name = 'admin'"
    ).fetchone()
    app_admin_row = conn.execute(
        "SELECT id FROM roles WHERE name = 'app_admin'"
    ).fetchone()

    if not admin_row or not app_admin_row:
        log.debug(
            "[MIGRATION] M002: admin or app_admin role not found — "
            "skipping legacy migration"
        )
        return

    admin_role_id = admin_row[0]
    app_admin_role_id = app_admin_row[0]

    # Find all users currently assigned to the legacy 'admin' role
    legacy_users = conn.execute(
        "SELECT id, username FROM users WHERE role_id = ?",
        (admin_role_id,),
    ).fetchall()

    if not legacy_users:
        log.debug("[MIGRATION] M002: no legacy admin users to migrate")
        return

    for user_pk, username in legacy_users:
        conn.execute(
            "UPDATE users SET role_id = ? WHERE id = ?",
            (app_admin_role_id, user_pk),
        )
        log.info(
            f"[MIGRATION] M002: migrated user '{username}' "
            f"from role 'admin' to 'app_admin' (preserving identity)"
        )
