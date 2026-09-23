"""
Phase 2 — Multi-Hospital Data Architecture: Migration Tests
===========================================================

Covers:
  - Migration idempotency (fresh DB and pre-migration DB)
  - New tables: hospitals, departments
  - New columns: users (uuid, user_id, hospital_id, etc.) and sessions
  - Role scope field: APPLICATION / HOSPITAL / DEVICE
  - Old roles preserved; new roles seeded
  - User ID generation formula:  HOSP[:2] + FNAME[:2] + EMP[:2]
  - Collision sequence:  APKA47 → APKA47-01 → APKA47-02 (NOT APKA472)
  - Hospital management API
  - Hospital-scoped user isolation
  - CRIT-SEC-01 fix: validate_session rejects tokens for disabled users
  - HIGH-SEC-03 fix: change_password invalidates all active sessions
  - Pre-migration user backfill (uuid + user_id assigned, hospital_id = NULL)
"""

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared_networking.database import AetherDatabase
from shared_networking.migration import generate_user_id_base, make_unique_user_id


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db():
    """Fresh, fully-migrated isolated database for each test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = AetherDatabase()
    db._ready = False
    db._path = None
    assert db.open(db_path) is True

    yield db, db_path

    try:
        os.remove(db_path)
    except Exception:
        pass


@pytest.fixture
def pre_migration_db():
    """Simulate a pre-Phase-2 database (old schema, one admin user).

    Creates the database using the original schema manually, then opens
    it via the current AetherDatabase.open() which applies M001.
    Validates that existing data survives migration intact.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # Manually build the OLD schema (as it existed before M001)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE roles (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        INSERT INTO roles (name) VALUES ('admin');
        INSERT INTO roles (name) VALUES ('user');
        INSERT INTO roles (name) VALUES ('data_generator');

        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            role_id       INTEGER NOT NULL REFERENCES roles(id),
            is_enabled    INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT    NOT NULL,
            last_login_at TEXT
        );
        INSERT INTO users (username, password_hash, role_id, created_at)
        VALUES (
            'admin',
            '$2b$12$placeholder_hash_for_testing_xxxx',
            1,
            '2026-08-13T12:15:37+00:00'
        );

        CREATE TABLE devices (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id        TEXT    NOT NULL UNIQUE,
            device_type      TEXT    NOT NULL,
            cert_fingerprint TEXT    NOT NULL UNIQUE,
            is_enabled       INTEGER NOT NULL DEFAULT 1,
            registered_at    TEXT    NOT NULL,
            last_seen_at     TEXT
        );

        CREATE TABLE sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            token      TEXT    NOT NULL UNIQUE,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            device_id  INTEGER REFERENCES devices(id),
            created_at TEXT    NOT NULL,
            expires_at TEXT    NOT NULL,
            is_active  INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE topic_acls (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            role          TEXT    NOT NULL,
            topic         TEXT    NOT NULL,
            can_publish   INTEGER NOT NULL DEFAULT 0,
            can_subscribe INTEGER NOT NULL DEFAULT 0,
            UNIQUE(role, topic)
        );

        CREATE TABLE audit_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT    NOT NULL,
            event_type TEXT    NOT NULL,
            username   TEXT,
            device_id  TEXT,
            details    TEXT,
            ip_address TEXT
        );
    """)
    conn.commit()
    conn.close()

    # Open with the current AetherDatabase — triggers M001 migration
    db = AetherDatabase()
    db._ready = False
    db._path = None
    assert db.open(db_path) is True

    yield db, db_path

    try:
        os.remove(db_path)
    except Exception:
        pass


# ─── Migration Idempotency ────────────────────────────────────────────────────

class TestMigrationIdempotency:
    def test_fresh_db_opens_successfully(self, temp_db):
        """A fresh database opens and reports ready."""
        db, _ = temp_db
        assert db.is_ready is True

    def test_migration_is_idempotent_on_fresh_db(self, temp_db):
        """Opening the same fresh DB a second time must succeed without errors."""
        db, db_path = temp_db
        db2 = AetherDatabase()
        db2._ready = False
        db2._path = None
        assert db2.open(db_path) is True

    def test_pre_migration_db_opens_and_migrates(self, pre_migration_db):
        """An old-schema DB (pre-Phase-2) migrates and becomes ready."""
        db, _ = pre_migration_db
        assert db.is_ready is True

    def test_pre_migration_db_idempotent_second_open(self, pre_migration_db):
        """Opening an already-migrated DB a second time must succeed."""
        db, db_path = pre_migration_db
        db2 = AetherDatabase()
        db2._ready = False
        db2._path = None
        assert db2.open(db_path) is True

    def test_no_users_deleted_after_migration(self, pre_migration_db):
        """Pre-existing user rows must survive migration intact."""
        db, db_path = pre_migration_db
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        assert count >= 1, "Pre-existing users were deleted during migration"


# ─── New Schema Elements ──────────────────────────────────────────────────────

class TestNewSchema:
    def test_hospitals_table_exists(self, temp_db):
        _, db_path = temp_db
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hospitals'"
        ).fetchone()
        conn.close()
        assert row is not None, "'hospitals' table not created"

    def test_departments_table_exists(self, temp_db):
        _, db_path = temp_db
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='departments'"
        ).fetchone()
        conn.close()
        assert row is not None, "'departments' table not created"

    def test_new_columns_on_users(self, temp_db):
        _, db_path = temp_db
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        conn.close()
        required = {
            "uuid", "user_id", "hospital_id",
            "first_name", "last_name", "employee_id", "department_id",
            "failed_login_attempts", "locked_until",
            "force_password_change", "password_changed_at",
        }
        missing = required - cols
        assert not missing, f"Missing users columns: {missing}"

    def test_new_columns_on_sessions(self, temp_db):
        _, db_path = temp_db
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        conn.close()
        assert "hospital_id" in cols, "sessions.hospital_id missing"
        assert "last_activity_at" in cols, "sessions.last_activity_at missing"

    def test_scope_column_on_roles(self, temp_db):
        _, db_path = temp_db
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(roles)").fetchall()}
        conn.close()
        assert "scope" in cols, "roles.scope missing"

    def test_unique_indexes_created(self, temp_db):
        _, db_path = temp_db
        conn = sqlite3.connect(db_path)
        indexes = {
            r[1]
            for r in conn.execute(
                "SELECT * FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        conn.close()
        assert "idx_users_uuid" in indexes
        assert "idx_users_user_id" in indexes


# ─── Roles and Scopes ─────────────────────────────────────────────────────────

class TestRoles:
    def test_old_roles_preserved(self, temp_db):
        """The original admin / user / data_generator roles must survive."""
        db, _ = temp_db
        for name in ("admin", "user", "data_generator"):
            row = db._query_one("SELECT id FROM roles WHERE name = ?", (name,))
            assert row is not None, f"Original role '{name}' was deleted"

    def test_new_roles_seeded(self, temp_db):
        """All six Phase-2 roles must be present."""
        db, _ = temp_db
        for name in ("app_admin", "hospital_admin", "technician",
                     "surgeon", "doctor", "observer"):
            row = db._query_one("SELECT id FROM roles WHERE name = ?", (name,))
            assert row is not None, f"New role '{name}' not seeded"

    def test_application_scope_roles(self, temp_db):
        """app_admin and admin must have APPLICATION scope."""
        db, _ = temp_db
        for name in ("app_admin", "admin"):
            row = db._query_one("SELECT scope FROM roles WHERE name = ?", (name,))
            assert row is not None
            assert row["scope"] == "APPLICATION", (
                f"Role '{name}' has scope={row['scope']!r}, expected APPLICATION"
            )

    def test_hospital_scope_roles(self, temp_db):
        """All hospital-level roles must have HOSPITAL scope."""
        db, _ = temp_db
        for name in ("hospital_admin", "technician", "surgeon",
                     "doctor", "observer", "user"):
            row = db._query_one("SELECT scope FROM roles WHERE name = ?", (name,))
            assert row is not None
            assert row["scope"] == "HOSPITAL", (
                f"Role '{name}' has scope={row['scope']!r}, expected HOSPITAL"
            )

    def test_data_generator_scope_is_device(self, temp_db):
        db, _ = temp_db
        row = db._query_one(
            "SELECT scope FROM roles WHERE name = 'data_generator'"
        )
        assert row is not None
        assert row["scope"] == "DEVICE"


# ─── User ID Generation ───────────────────────────────────────────────────────

class TestUserIdGeneration:
    def test_exact_formula_example(self):
        """Apollo + Karthik + 47291 must produce exactly APKA47."""
        result = generate_user_id_base("APOLLO", "Karthik", "47291")
        assert result == "APKA47", f"Got {result!r}, expected 'APKA47'"

    def test_hospital_full_name_uses_first_two_alpha(self):
        """Full hospital name should strip non-alpha and yield first 2 chars."""
        result = generate_user_id_base("Apollo Hospital", "Karthik", "47291")
        assert result == "APKA47"

    def test_case_insensitive(self):
        """Lower-case inputs must produce the same result as upper-case."""
        r1 = generate_user_id_base("apollo", "karthik", "47291")
        r2 = generate_user_id_base("APOLLO", "KARTHIK", "47291")
        assert r1 == r2 == "APKA47"

    def test_collision_uses_dash_suffix_not_longer_emp_id(self, temp_db):
        """When APKA47 is taken, the collision must be APKA47-01 not APKA472."""
        db, _ = temp_db
        _, hosp_id = db.create_hospital("Apollo Hospitals", "APOLLO")

        ok, uid1 = db.create_full_user(
            "user_one", "StrongPass123!", "surgeon",
            hospital_id=hosp_id,
            first_name="Karthik", last_name="Sharma", employee_id="47291",
        )
        assert ok, uid1
        assert uid1 == "APKA47"

        ok, uid2 = db.create_full_user(
            "user_two", "StrongPass456!", "surgeon",
            hospital_id=hosp_id,
            first_name="Karthi", last_name="Rajan", employee_id="47X11",
        )
        assert ok, uid2
        assert uid2 == "APKA47-01", (
            f"Expected APKA47-01 (dash-suffix collision), got {uid2!r}. "
            "The algorithm must NOT extend the employee-ID portion."
        )

    def test_collision_second_suffix(self, temp_db):
        """Third user with the same base formula must get APKA47-02."""
        db, _ = temp_db
        _, hosp_id = db.create_hospital("Apollo Hospitals", "APOLLO")

        for uname in ("u1", "u2", "u3"):
            ok, _ = db.create_full_user(
                uname, "StrongPass123!", "surgeon",
                hospital_id=hosp_id,
                first_name="Karthik", employee_id="47291",
            )
            assert ok

        expected = {"u1": "APKA47", "u2": "APKA47-01", "u3": "APKA47-02"}
        for uname, exp_uid in expected.items():
            row = db._query_one(
                "SELECT user_id FROM users WHERE username = ?", (uname,)
            )
            assert row["user_id"] == exp_uid, (
                f"User '{uname}': expected {exp_uid!r}, got {row['user_id']!r}"
            )


# ─── Hospital Management ──────────────────────────────────────────────────────

class TestHospitalManagement:
    def test_create_hospital_succeeds(self, temp_db):
        db, _ = temp_db
        ok, hosp_id = db.create_hospital("Apollo Hospitals Chennai", "APOLLO")
        assert ok
        assert len(hosp_id) == 36  # UUID format

    def test_get_hospital_returns_correct_record(self, temp_db):
        db, _ = temp_db
        _, hosp_id = db.create_hospital("AIIMS Delhi", "AIIMS")
        h = db.get_hospital(hosp_id)
        assert h is not None
        assert h["name"] == "AIIMS Delhi"
        assert h["code"] == "AIIMS"

    def test_duplicate_hospital_code_rejected(self, temp_db):
        db, _ = temp_db
        ok, _ = db.create_hospital("Apollo Chennai", "APOLLO")
        assert ok
        ok, err = db.create_hospital("Apollo Mumbai", "APOLLO")
        assert not ok
        assert "already exists" in err.lower()

    def test_list_hospitals_returns_all_active(self, temp_db):
        db, _ = temp_db
        db.create_hospital("Apollo Hospitals", "APOLLO")
        db.create_hospital("AIIMS Delhi", "AIIMS")
        hospitals = db.list_hospitals()
        codes = {h["code"] for h in hospitals}
        assert "APOLLO" in codes
        assert "AIIMS" in codes


# ─── Hospital-Scoped Users ────────────────────────────────────────────────────

class TestHospitalScopedUsers:
    def test_create_full_user_assigns_correct_user_id(self, temp_db):
        db, _ = temp_db
        _, hosp_id = db.create_hospital("Apollo Hospitals", "APOLLO")
        ok, user_id = db.create_full_user(
            "dr_karthik", "SecurePass123!", "surgeon",
            hospital_id=hosp_id,
            first_name="Karthik", last_name="Sharma", employee_id="47291",
        )
        assert ok, user_id
        assert user_id == "APKA47"

    def test_app_admin_has_null_hospital_id(self, temp_db):
        """app_admin created without hospital_id must have hospital_id = NULL.

        This is the corrected architecture: app_admin scope = APPLICATION,
        hospital_id = NULL.  There must be NO fake 'SYSTEM' hospital.
        """
        db, _ = temp_db
        ok, _ = db.create_full_user(
            "global_admin", "GlobalAdmin123!", "app_admin",
            hospital_id=None,
            first_name="System", employee_id="00001",
        )
        assert ok
        h_id = db.get_user_hospital("global_admin")
        assert h_id is None, (
            f"app_admin should have hospital_id=NULL, got {h_id!r}. "
            "app_admin is application-scoped — no sentinel hospital."
        )

    def test_hospital_user_hospital_id_stored_correctly(self, temp_db):
        db, _ = temp_db
        _, hosp_id = db.create_hospital("Apollo Hospitals", "APOLLO")
        db.create_full_user(
            "dr_karthik", "SecurePass123!", "surgeon",
            hospital_id=hosp_id, first_name="Karthik", employee_id="47291",
        )
        stored = db.get_user_hospital("dr_karthik")
        assert stored == hosp_id

    def test_get_users_for_hospital_isolates_correctly(self, temp_db):
        """Users from Hospital A must never appear in Hospital B's list."""
        db, _ = temp_db
        _, hosp_a = db.create_hospital("Apollo Hospitals", "APOLLO")
        _, hosp_b = db.create_hospital("AIIMS Delhi", "AIIMS")

        db.create_full_user(
            "alice", "Pass1234!", "surgeon",
            hospital_id=hosp_a, first_name="Alice", employee_id="11111",
        )
        db.create_full_user(
            "bob", "Pass1234!", "surgeon",
            hospital_id=hosp_b, first_name="Bob", employee_id="22222",
        )

        a_users = {u["username"] for u in db.get_users_for_hospital(hosp_a)}
        b_users = {u["username"] for u in db.get_users_for_hospital(hosp_b)}

        assert "alice" in a_users
        assert "bob" not in a_users
        assert "bob" in b_users
        assert "alice" not in b_users

    def test_get_user_profile_includes_hospital_and_scope(self, temp_db):
        db, _ = temp_db
        _, hosp_id = db.create_hospital("Apollo Hospitals", "APOLLO")
        db.create_full_user(
            "dr_k", "Pass1234!", "surgeon",
            hospital_id=hosp_id, first_name="Karthik", employee_id="99999",
        )
        profile = db.get_user_profile("dr_k")
        assert profile is not None
        assert profile["hospital_code"] == "APOLLO"
        assert profile["role"] == "surgeon"
        assert profile["scope"] == "HOSPITAL"


# ─── Pre-Migration User Backfill ──────────────────────────────────────────────

class TestPreMigrationBackfill:
    def test_existing_users_get_uuid(self, pre_migration_db):
        """Every pre-migration user must have a non-null UUID after M001."""
        db, db_path = pre_migration_db
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT username, uuid FROM users").fetchall()
        conn.close()
        for username, uuid_val in rows:
            assert uuid_val is not None, (
                f"User '{username}' has NULL uuid after migration"
            )
            assert len(uuid_val) == 36, (
                f"User '{username}' has malformed uuid: {uuid_val!r}"
            )

    def test_existing_users_get_user_id(self, pre_migration_db):
        """Every pre-migration user must have a non-null user_id after M001."""
        db, db_path = pre_migration_db
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT username, user_id FROM users").fetchall()
        conn.close()
        for username, user_id_val in rows:
            assert user_id_val is not None, (
                f"User '{username}' has NULL user_id after migration"
            )

    def test_admin_user_id_is_sysad01(self, pre_migration_db):
        """The legacy 'admin' user must be backfilled with user_id='SYSAD01'."""
        db, db_path = pre_migration_db
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT user_id FROM users WHERE username = 'admin'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "SYSAD01", (
            f"admin user_id expected 'SYSAD01', got {row[0]!r}"
        )

    def test_existing_users_have_null_hospital_id(self, pre_migration_db):
        """Pre-migration users must have hospital_id = NULL (application scope).

        The corrected architecture requires no fake 'SYSTEM' hospital.
        """
        db, db_path = pre_migration_db
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT username, hospital_id FROM users"
        ).fetchall()
        conn.close()
        for username, hospital_id in rows:
            assert hospital_id is None, (
                f"Pre-migration user '{username}' has hospital_id={hospital_id!r}. "
                "Expected NULL (application scope, no fake SYSTEM hospital)."
            )


# ─── Security Bug Fixes ───────────────────────────────────────────────────────

class TestSecurityBugFixes:
    def test_crit_sec_01_disabled_user_session_rejected(self, temp_db):
        """CRIT-SEC-01 fix: validate_session must fail if u.is_enabled = 0.

        Previously, validate_session did not check u.is_enabled, so a
        disabled user's active session token was still accepted.
        """
        db, _ = temp_db
        db.create_user("target", "ValidPass123!", "user")
        token = db.create_session("target", "user")

        # Token valid before disabling
        valid, *_ = db.validate_session(token)
        assert valid is True

        # Disable the account
        db._exec("UPDATE users SET is_enabled = 0 WHERE username = 'target'")

        # Token must now be rejected
        valid, *_ = db.validate_session(token)
        assert valid is False, (
            "CRIT-SEC-01: validate_session accepted a token for a "
            "disabled user — is_enabled check is missing"
        )

    def test_crit_sec_01_device_bound_session_also_rejected(self, temp_db):
        """CRIT-SEC-01 fix applies to both query branches in validate_session."""
        db, _ = temp_db
        db.create_user("target2", "ValidPass123!", "user")
        db.register_device("surgeon_console", "surgeon", "fp_test_abc123")
        token = db.create_session("target2", "user", device_id="surgeon_console")

        valid, *_ = db.validate_session(token, device_id="surgeon_console")
        assert valid is True

        db._exec("UPDATE users SET is_enabled = 0 WHERE username = 'target2'")

        valid, *_ = db.validate_session(token, device_id="surgeon_console")
        assert valid is False, (
            "CRIT-SEC-01: device-bound branch also missing is_enabled check"
        )

    def test_high_sec_03_password_change_revokes_all_sessions(self, temp_db):
        """HIGH-SEC-03 fix: change_password must invalidate all active sessions.

        Previously, existing sessions survived a password change, allowing
        a compromised session to remain active after a credential rotation.
        """
        db, _ = temp_db
        db.create_user("target", "OldPassword123!", "user")
        token1 = db.create_session("target", "user")
        token2 = db.create_session("target", "user")

        assert db.validate_session(token1)[0] is True
        assert db.validate_session(token2)[0] is True

        ok, msg = db.change_password("target", "OldPassword123!", "NewPassword456!")
        assert ok, f"Password change failed: {msg}"

        assert db.validate_session(token1)[0] is False, (
            "HIGH-SEC-03: token1 still valid after password change"
        )
        assert db.validate_session(token2)[0] is False, (
            "HIGH-SEC-03: token2 still valid after password change"
        )

    def test_high_sec_03_new_session_works_after_password_change(self, temp_db):
        """A new session created after password change must be valid."""
        db, _ = temp_db
        db.create_user("target", "OldPassword123!", "user")
        old_token = db.create_session("target", "user")

        db.change_password("target", "OldPassword123!", "NewPassword456!")

        # Old session invalid
        assert db.validate_session(old_token)[0] is False

        # New session (would be created after re-login) must work
        new_token = db.create_session("target", "user")
        valid, username, role, *_ = db.validate_session(new_token)
        assert valid is True
        assert username == "target"
        assert role == "user"
