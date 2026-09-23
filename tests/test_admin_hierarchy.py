"""
Phase 4 — Administrative Hierarchy Tests
=========================================

Covers:
  - AppAdminService: hospital management, hospital admin creation,
    global user/audit access, app config (secrets excluded)
  - HospitalAdminService: scoped user management, department management,
    scoped audit access
  - Role escalation denial (hospital_admin → app_admin, hospital_admin → hospital_admin)
  - Self-modification / self-lockout prevention
  - Hospital isolation matrix (Hospital A admin vs Hospital B, Technician A)
  - Audit event coverage (presence, absence of secrets)
  - Provisioning compatibility (new installs + M002 backward compat)

Test philosophy:
  - All tests run against isolated in-memory or temp-file databases.
  - No singleton reuse between tests.
  - Service-layer tests do NOT test PyQt6 UI — they test the backend only.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared_networking.database import AetherDatabase
from shared_networking.authentication import AuthManager
from shared_networking.authorization import AuthorizationService
from shared_networking.admin_service import (
    AppAdminService,
    HospitalAdminService,
    HOSPITAL_ADMIN_ALLOWED_ROLES,
)


# ─── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def temp_db():
    """Fresh, fully-migrated, isolated database for each test."""
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
def populated(temp_db):
    """DB with two hospitals, one app_admin, two hospital admins, one technician."""
    db, db_path = temp_db

    # App admin (APPLICATION scope, hospital_id=NULL)
    ok, _ = db.create_full_user(
        "app_admin_user", "AppAdmin123!", "app_admin",
        hospital_id=None, first_name="System", employee_id="00001",
    )
    assert ok

    # Hospital A
    ok, hosp_a = db.create_hospital("Apollo Hospitals", "APOLLO")
    assert ok

    # Hospital B
    ok, hosp_b = db.create_hospital("AIIMS Delhi", "AIIMS")
    assert ok

    # Hospital Admin A → Hospital A
    ok, _ = db.create_full_user(
        "hadmin_a", "HAdmin123!", "hospital_admin",
        hospital_id=hosp_a, first_name="Alice", employee_id="10001",
    )
    assert ok

    # Hospital Admin B → Hospital B
    ok, _ = db.create_full_user(
        "hadmin_b", "HAdmin123!", "hospital_admin",
        hospital_id=hosp_b, first_name="Bob", employee_id="20001",
    )
    assert ok

    # Technician A → Hospital A
    ok, _ = db.create_full_user(
        "tech_a", "Tech1234!", "technician",
        hospital_id=hosp_a, first_name="Carlos", employee_id="10002",
    )
    assert ok

    # Surgeon A → Hospital A
    ok, _ = db.create_full_user(
        "surgeon_a", "Surgeon12!", "surgeon",
        hospital_id=hosp_a, first_name="Diana", employee_id="10003",
    )
    assert ok

    authz = AuthorizationService(db)
    app_svc = AppAdminService(db, authz)
    hosp_svc = HospitalAdminService(db, authz)

    def tok(username):
        return db.create_session(username, db.get_user_role(username))

    return {
        "db": db,
        "authz": authz,
        "app_svc": app_svc,
        "hosp_svc": hosp_svc,
        "hosp_a": hosp_a,
        "hosp_b": hosp_b,
        "tok": tok,
    }


# ─── TestAppAdminService ──────────────────────────────────────────

class TestAppAdminService:
    def test_create_hospital_succeeds(self, temp_db):
        db, _ = temp_db
        ok, _ = db.create_full_user(
            "admin1", "Admin1234!", "app_admin",
            hospital_id=None, first_name="System", employee_id="00001",
        )
        assert ok
        token = db.create_session("admin1", "app_admin")
        svc = AppAdminService(db)

        ok, hosp_id = svc.create_hospital(token, "City Hospital", "CITY")
        assert ok, hosp_id
        assert len(hosp_id) == 36

    def test_create_hospital_audited(self, temp_db):
        db, _ = temp_db
        db.create_full_user("admin1", "Admin1234!", "app_admin",
                            hospital_id=None, first_name="S", employee_id="1")
        token = db.create_session("admin1", "app_admin")
        svc = AppAdminService(db)
        svc.create_hospital(token, "Audit Hospital", "AUDITHOSP")

        logs = db.get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_HOSPITAL_CREATED" in events

    def test_list_hospitals_returns_all(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        ok, hospitals = p["app_svc"].list_hospitals(tok)
        assert ok
        codes = {h["code"] for h in hospitals}
        assert "APOLLO" in codes
        assert "AIIMS" in codes

    def test_set_hospital_active_deactivate(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        ok, err = p["app_svc"].set_hospital_active(tok, p["hosp_a"], False)
        assert ok, err
        hosp = p["db"].get_hospital(p["hosp_a"])
        assert hosp["is_active"] == 0

    def test_set_hospital_active_reactivate(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        p["app_svc"].set_hospital_active(tok, p["hosp_a"], False)
        ok, err = p["app_svc"].set_hospital_active(tok, p["hosp_a"], True)
        assert ok, err
        hosp = p["db"].get_hospital(p["hosp_a"])
        assert hosp["is_active"] == 1

    def test_deactivation_audited(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        p["app_svc"].set_hospital_active(tok, p["hosp_a"], False)
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_HOSPITAL_DEACTIVATED" in events

    def test_create_hospital_admin_succeeds(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        ok, uid = p["app_svc"].create_hospital_admin(
            tok, p["hosp_a"], "new_hadmin", "HAdmin999!", "Eve",
        )
        assert ok, uid
        profile = p["db"].get_user_profile("new_hadmin")
        assert profile is not None
        assert profile["role"] == "hospital_admin"
        assert profile["hospital_id"] == p["hosp_a"]

    def test_create_hospital_admin_validates_hospital_server_side(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        # Supply a completely invalid hospital_id
        ok, err = p["app_svc"].create_hospital_admin(
            tok, "00000000-0000-0000-0000-000000000000",
            "ghost_admin", "GhostPass1!", "Ghost",
        )
        assert not ok
        assert "not found" in err.lower()

    def test_create_hospital_admin_audited(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        p["app_svc"].create_hospital_admin(
            tok, p["hosp_a"], "audited_hadmin", "AuditPass1!", "Fred",
        )
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_HOSPITAL_ADMIN_CREATED" in events

    def test_list_all_users_includes_both_hospitals(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        ok, users = p["app_svc"].list_all_users(tok)
        assert ok
        unames = {u["username"] for u in users}
        assert "hadmin_a" in unames
        assert "hadmin_b" in unames
        assert "tech_a" in unames

    def test_get_audit_logs_succeeds(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        ok, logs = p["app_svc"].get_audit_logs(tok, limit=20)
        assert ok
        assert isinstance(logs, list)

    def test_get_app_config_excludes_secrets(self, populated):
        """Configuration must not expose passwords, keys, or secrets."""
        p = populated
        tok = p["tok"]("app_admin_user")
        ok, cfg = p["app_svc"].get_app_config(tok)
        assert ok
        cfg_str = str(cfg).lower()
        # These must never appear in config output
        for secret_key in ("password", "hash", "private_key", "cert",
                           "secret", "token", "recovery"):
            assert secret_key not in cfg_str, (
                f"Config output contains sensitive key '{secret_key}': {cfg}"
            )
        # These safe keys must be present
        assert "version" in cfg
        assert "broker_host" in cfg
        assert "broker_port" in cfg
        assert "database_file" in cfg

    def test_hospital_admin_cannot_use_app_admin_service(self, populated):
        """hospital_admin must be denied by AppAdminService (lacks HOSPITAL_MANAGE)."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, _ = p["app_svc"].create_hospital(tok, "Rogue Hospital", "ROGUE")
        assert not ok

    def test_hospital_admin_cannot_list_all_hospitals(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, _ = p["app_svc"].list_hospitals(tok)
        assert not ok


# ─── TestHospitalAdminService ─────────────────────────────────────

class TestHospitalAdminService:
    def test_get_my_hospital_correct(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, data = p["hosp_svc"].get_my_hospital(tok)
        assert ok
        assert data["code"] == "APOLLO"

    def test_list_my_users_excludes_other_hospital(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, users = p["hosp_svc"].list_my_users(tok)
        assert ok
        unames = {u["username"] for u in users}
        assert "tech_a" in unames
        assert "surgeon_a" in unames
        assert "hadmin_b" not in unames   # Hospital B user must not appear

    def test_create_technician_succeeds(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, uid = p["hosp_svc"].create_user(
            tok, "new_tech", "Tech1234!", "technician", "George")
        assert ok, uid
        profile = p["db"].get_user_profile("new_tech")
        assert profile["role"] == "technician"
        assert profile["hospital_id"] == p["hosp_a"]

    def test_create_surgeon_succeeds(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, _ = p["hosp_svc"].create_user(
            tok, "new_surg", "Surg1234!", "surgeon", "Hannah")
        assert ok

    def test_create_doctor_succeeds(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, _ = p["hosp_svc"].create_user(
            tok, "new_doc", "Doc12345!", "doctor", "Ivan")
        assert ok

    def test_create_observer_succeeds(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, _ = p["hosp_svc"].create_user(
            tok, "new_obs", "Obs12345!", "observer", "Julia")
        assert ok

    # ─── Role escalation denials (R9, R16) ───────────────────────

    def test_create_user_role_escalation_hospital_admin_denied(self, populated):
        """Hospital Admin must NOT be able to create another hospital_admin."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, err = p["hosp_svc"].create_user(
            tok, "rogue_hadmin", "Rogue123!", "hospital_admin", "Rogue")
        assert not ok
        assert "hospital_admin" in err.lower() or "allowed" in err.lower()

    def test_create_user_role_escalation_app_admin_denied(self, populated):
        """Hospital Admin must NOT be able to create an app_admin."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, err = p["hosp_svc"].create_user(
            tok, "rogue_app", "Rogue123!", "app_admin", "Rogue")
        assert not ok
        assert "app_admin" in err.lower() or "allowed" in err.lower()

    def test_escalation_denial_audited(self, populated):
        """Role escalation attempt must be written to the audit log."""
        p = populated
        tok = p["tok"]("hadmin_a")
        p["hosp_svc"].create_user(
            tok, "esc_attempt", "Esc12345!", "hospital_admin", "EscUser")
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_ROLE_ESCALATION_DENIED" in events

    # ─── set_user_enabled target verification (R7) ────────────────

    def test_set_user_enabled_wrong_hospital_denied(self, populated):
        """Hospital Admin A must not be able to disable a Hospital B user."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, err = p["hosp_svc"].set_user_enabled(tok, "hadmin_b", False)
        assert not ok
        assert "hospital" in err.lower() or "denied" in err.lower()

    def test_set_user_enabled_own_hospital_allowed(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, err = p["hosp_svc"].set_user_enabled(tok, "tech_a", False)
        assert ok, err

    # ─── Self-modification guard ──────────────────────────────────

    def test_hospital_admin_cannot_disable_own_account(self, populated):
        """Hospital Admin must not be able to disable their own account."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, err = p["hosp_svc"].set_user_enabled(tok, "hadmin_a", False)
        assert not ok
        assert "own account" in err.lower() or "self" in err.lower()

    # ─── set_user_role guards (R8, R9) ───────────────────────────

    def test_hospital_admin_cannot_change_own_role(self, populated):
        """Phase 3 self-role-change guard — also enforced in Phase 4 service."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, err = p["hosp_svc"].set_user_role(tok, "hadmin_a", "technician")
        assert not ok

    def test_assign_app_admin_role_denied(self, populated):
        """Hospital Admin must not be able to escalate a user to app_admin."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, err = p["hosp_svc"].set_user_role(tok, "tech_a", "app_admin")
        assert not ok
        assert "app_admin" in err.lower() or "allowed" in err.lower()

    def test_assign_hospital_admin_role_denied(self, populated):
        """Hospital Admin must not be able to assign hospital_admin to a user."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, err = p["hosp_svc"].set_user_role(tok, "tech_a", "hospital_admin")
        assert not ok
        assert "hospital_admin" in err.lower() or "allowed" in err.lower()

    def test_set_user_role_wrong_hospital_denied(self, populated):
        """Hospital Admin A must not be able to change a Hospital B user's role."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, err = p["hosp_svc"].set_user_role(tok, "hadmin_b", "technician")
        assert not ok

    # ─── Audit log scope ─────────────────────────────────────────

    def test_hospital_audit_log_scoped_to_own_hospital(self, populated):
        """Hospital audit log must not contain events from other hospitals."""
        p = populated
        # hadmin_b performs an action so it appears in audit logs
        tok_b = p["tok"]("hadmin_b")
        p["hosp_svc"].create_user(
            tok_b, "hosp_b_user", "User1234!", "technician", "TestUser")

        # hadmin_a's audit view should not include hadmin_b events
        tok_a = p["tok"]("hadmin_a")
        ok, logs_a = p["hosp_svc"].get_hospital_audit_logs(tok_a, limit=200)
        assert ok
        usernames_in_log = {l.get("username") for l in logs_a if l.get("username")}
        assert "hadmin_b" not in usernames_in_log, (
            "Hospital A audit log must not include Hospital B admin events"
        )


# ─── TestIsolationMatrix ──────────────────────────────────────────

class TestIsolationMatrix:
    """Spec scenario: Hospital A and Hospital B must be fully isolated."""

    def test_hospital_a_admin_cannot_manage_hospital_b_user(self, populated):
        p = populated
        tok_a = p["tok"]("hadmin_a")
        ok, _ = p["hosp_svc"].set_user_enabled(tok_a, "hadmin_b", False)
        assert not ok

    def test_hospital_b_admin_cannot_manage_hospital_a_user(self, populated):
        p = populated
        tok_b = p["tok"]("hadmin_b")
        ok, _ = p["hosp_svc"].set_user_enabled(tok_b, "tech_a", False)
        assert not ok

    def test_hospital_a_admin_cannot_change_hospital_b_user_role(self, populated):
        p = populated
        tok_a = p["tok"]("hadmin_a")
        ok, _ = p["hosp_svc"].set_user_role(tok_a, "hadmin_b", "technician")
        assert not ok

    def test_hospital_b_admin_cannot_change_hospital_a_user_role(self, populated):
        p = populated
        tok_b = p["tok"]("hadmin_b")
        ok, _ = p["hosp_svc"].set_user_role(tok_b, "tech_a", "surgeon")
        assert not ok

    def test_app_admin_can_manage_hospital_a(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        ok, hosp_id = p["app_svc"].create_hospital(tok, "Springfield General", "SGH")
        assert ok, hosp_id

    def test_app_admin_can_see_both_hospitals(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        ok, hospitals = p["app_svc"].list_hospitals(tok)
        assert ok
        codes = {h["code"] for h in hospitals}
        assert "APOLLO" in codes
        assert "AIIMS" in codes

    def test_technician_a_has_no_user_manage_permission(self, populated):
        """Technician has DEVICE_MANAGE only — user management must be denied."""
        p = populated
        tok = p["tok"]("tech_a")
        ok, _ = p["hosp_svc"].list_my_users(tok)
        assert not ok

    def test_technician_a_has_no_hospital_manage_permission(self, populated):
        p = populated
        tok = p["tok"]("tech_a")
        ok, _ = p["app_svc"].list_hospitals(tok)
        assert not ok

    def test_technician_a_cannot_create_hospital_b_user(self, populated):
        p = populated
        tok = p["tok"]("tech_a")
        ok, _ = p["hosp_svc"].create_user(
            tok, "rogue_user", "Rogue123!", "technician", "Rogue")
        assert not ok


# ─── TestSelfLockoutPrevention ────────────────────────────────────

class TestSelfLockoutPrevention:
    def test_app_admin_cannot_disable_own_account_via_hosp_svc(self, populated):
        """Self-modification guard in HospitalAdminService."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, _ = p["hosp_svc"].set_user_enabled(tok, "hadmin_a", False)
        assert not ok

    def test_app_admin_cannot_change_own_role_via_hosp_svc(self, populated):
        """Phase 3 self-role guard preserved in HospitalAdminService."""
        p = populated
        tok = p["tok"]("hadmin_a")
        ok, _ = p["hosp_svc"].set_user_role(tok, "hadmin_a", "technician")
        assert not ok


# ─── TestAuditCoverage ────────────────────────────────────────────

class TestAuditCoverage:
    def test_hospital_created_event_logged(self, populated):
        p = populated
        logs = p["db"].get_recent_audit_logs(200)
        events = [l["event_type"] for l in logs]
        assert "HOSPITAL_CREATED" in events or "ADMIN_HOSPITAL_CREATED" in events

    def test_hospital_deactivated_event_logged(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        p["app_svc"].set_hospital_active(tok, p["hosp_a"], False)
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_HOSPITAL_DEACTIVATED" in events

    def test_hospital_admin_created_event_logged(self, populated):
        p = populated
        tok = p["tok"]("app_admin_user")
        p["app_svc"].create_hospital_admin(
            tok, p["hosp_b"], "audit_hadmin", "Audit123!", "AuditUser")
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_HOSPITAL_ADMIN_CREATED" in events

    def test_user_created_event_logged(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        p["hosp_svc"].create_user(
            tok, "audit_user", "Audit123!", "technician", "AuditU")
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_USER_CREATED" in events

    def test_user_disabled_event_logged(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        p["hosp_svc"].set_user_enabled(tok, "tech_a", False)
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_USER_DISABLED" in events

    def test_user_enabled_event_logged(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        p["hosp_svc"].set_user_enabled(tok, "tech_a", False)
        p["hosp_svc"].set_user_enabled(tok, "tech_a", True)
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_USER_ENABLED" in events

    def test_role_changed_event_logged(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        p["hosp_svc"].set_user_role(tok, "tech_a", "surgeon")
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_USER_ROLE_CHANGED" in events

    def test_department_created_event_logged(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        p["hosp_svc"].create_department(tok, "Cardiology", "CARD")
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_DEPARTMENT_CREATED" in events

    def test_escalation_denial_event_logged(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        p["hosp_svc"].create_user(
            tok, "esc_u", "Esc12345!", "hospital_admin", "EscU")
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        assert "ADMIN_ROLE_ESCALATION_DENIED" in events

    def test_audit_records_contain_no_passwords(self, populated):
        """Audit log details must NEVER contain passwords or sensitive secrets."""
        logs = populated["db"].get_recent_audit_logs(500)
        for entry in logs:
            details = (entry.get("details") or "").lower()
            for secret_word in ("password", "hash", "private_key",
                                "token", "secret", "recovery_code"):
                assert secret_word not in details, (
                    f"Audit entry contains '{secret_word}' in details: "
                    f"event={entry.get('event_type')}, details={entry.get('details')!r}"
                )

    def test_access_denial_event_logged_for_cross_hospital(self, populated):
        p = populated
        tok = p["tok"]("hadmin_a")
        p["hosp_svc"].set_user_enabled(tok, "hadmin_b", False)
        logs = p["db"].get_recent_audit_logs(50)
        events = [l["event_type"] for l in logs]
        # Either the specific cross-hospital event or a generic denial
        assert any("CROSS_HOSPITAL" in e or "DENIED" in e for e in events)


# ─── TestProvisioningCompatibility ───────────────────────────────

class TestProvisioningCompatibility:
    def test_new_install_creates_app_admin_role(self, temp_db):
        """New provisioning creates user with app_admin role, not legacy admin."""
        db, _ = temp_db
        from shared_networking.provisioning import _create_admin_account

        ok, err = _create_admin_account(
            db, admin_username="sysadmin", admin_password="SysAdmin1!")
        assert ok, err

        profile = db.get_user_profile("sysadmin")
        assert profile is not None
        assert profile["role"] == "app_admin", (
            f"Expected role=app_admin, got {profile['role']!r}"
        )
        assert profile["hospital_id"] is None, (
            "New app_admin must have hospital_id=NULL (no sentinel hospital)"
        )

    def test_m002_migration_upgrades_legacy_admin(self, temp_db):
        """M002 must still migrate pre-existing 'admin' users to 'app_admin'.

        Strategy:
          1. Open the DB normally (roles seeded, schema at version 2).
          2. Insert a legacy user with role_id = roles.id WHERE name='admin'.
          3. Reset schema_version to 1 so run_migrations() sees M002 as unrun.
          4. Open a NEW AetherDatabase instance pointing at the same file —
             this triggers M002 which migrates the legacy user.
          5. Assert the user's role is now 'app_admin'.

        This accurately simulates a production DB that ran M001 (schema_version=1)
        but not yet M002, with a legacy 'admin' user already present.
        """
        import bcrypt as _bcrypt
        import uuid as _uuid
        from datetime import datetime, timezone

        db, db_path = temp_db

        # Step 2: Look up the correctly-seeded 'admin' role_id
        admin_row = db._query_one("SELECT id FROM roles WHERE name='admin'")
        assert admin_row is not None, "'admin' role must exist (kept for FK safety)"
        admin_role_id = admin_row["id"]

        # Insert legacy admin user using the correct seeded role_id
        pw_hash = _bcrypt.hashpw(b"OldPass1!", _bcrypt.gensalt()).decode()
        now = datetime.now(timezone.utc).isoformat()
        db._exec(
            "INSERT INTO users "
            "(username, password_hash, role_id, uuid, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("legacy_admin", pw_hash, admin_role_id, str(_uuid.uuid4()), now),
        )

        # Verify the role resolves to 'admin' before migration
        pre = db.get_user_profile("legacy_admin")
        assert pre is not None
        assert pre["role"] == "admin", (
            f"User should have 'admin' role before M002, got {pre['role']!r}"
        )

        # Step 3: Reset user_version to 1 so M002 re-runs on next open
        db._exec("PRAGMA user_version = 1")

        # Step 4: Open a NEW AetherDatabase instance — triggers M002
        db2 = AetherDatabase()
        db2._ready = False
        db2._path = None
        assert db2.open(db_path) is True

        # Step 5: M002 must have promoted the legacy user to app_admin
        post = db2.get_user_profile("legacy_admin")
        assert post is not None, "legacy_admin must survive M002 migration"
        assert post["role"] == "app_admin", (
            f"M002 must migrate 'admin' → 'app_admin', got {post['role']!r}"
        )

    def test_existing_app_admin_survives_second_db_open(self, temp_db):
        """Opening an already-migrated DB a second time must not corrupt users."""
        db, db_path = temp_db
        from shared_networking.provisioning import _create_admin_account
        _create_admin_account(db, admin_username="persist_admin",
                              admin_password="Persist12!")

        db2 = AetherDatabase()
        db2._ready = False
        db2._path = None
        assert db2.open(db_path) is True

        profile = db2.get_user_profile("persist_admin")
        assert profile is not None
        assert profile["role"] == "app_admin"


# ─── TestHospitalAdminAllowedRoles ───────────────────────────────

class TestHospitalAdminAllowedRoles:
    """Verify the HOSPITAL_ADMIN_ALLOWED_ROLES constant is correct."""

    def test_allowed_roles_does_not_include_privileged_roles(self):
        assert "app_admin" not in HOSPITAL_ADMIN_ALLOWED_ROLES
        assert "hospital_admin" not in HOSPITAL_ADMIN_ALLOWED_ROLES

    def test_allowed_roles_includes_expected_roles(self):
        assert "technician" in HOSPITAL_ADMIN_ALLOWED_ROLES
        assert "surgeon" in HOSPITAL_ADMIN_ALLOWED_ROLES
        assert "doctor" in HOSPITAL_ADMIN_ALLOWED_ROLES
        assert "observer" in HOSPITAL_ADMIN_ALLOWED_ROLES
