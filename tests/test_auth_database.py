"""Tests for authentication, SQLite database operations, session lifecycle, and ACL reconciliation."""
import sys
import os
import tempfile
import sqlite3
from datetime import datetime, timedelta, timezone
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared_networking.database import AetherDatabase
from shared_networking.authentication import AuthManager


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = AetherDatabase()
    # Reset singleton internals for isolated test instance
    db._ready = False
    db._path = None
    assert db.open(db_path) is True

    yield db, db_path

    try:
        os.remove(db_path)
    except Exception:
        pass


class TestDatabaseAndAuth:
    def test_user_creation_and_password_verification(self, temp_db):
        db, _ = temp_db
        # Password too short
        ok, err = db.create_user("doctor", "short", "user")
        assert not ok
        assert "at least" in err

        # Valid user creation
        ok, err = db.create_user("doctor", "surgicalPassword123!", "user")
        assert ok
        assert err == ""

        # Verify correct credentials
        valid, role = db.verify_user("doctor", "surgicalPassword123!")
        assert valid is True
        assert role == "user"

        # Verify wrong password
        valid, role = db.verify_user("doctor", "wrongPass")
        assert valid is False
        assert role == ""

        # Verify non-existent user
        valid, role = db.verify_user("ghost", "surgicalPassword123!")
        assert valid is False

    def test_session_lifecycle_and_device_binding(self, temp_db):
        db, _ = temp_db
        db.create_user("surgeon_user", "ValidPassword123", "user")

        # Register device
        db.register_device("surgeon_console", "surgeon", "fp_1234567890abcdef")

        # Create session bound to device
        token = db.create_session("surgeon_user", "user", device_id="surgeon_console")
        assert len(token) > 20

        # Validate with matching device
        valid, username, role, *_ = db.validate_session(token, device_id="surgeon_console")
        assert valid is True
        assert username == "surgeon_user"
        assert role == "user"

        # Validate with wrong device when device-bound
        valid, *_ = db.validate_session(token, device_id="observer_screen")
        assert valid is False

        # Invalidate session (logout)
        db.invalidate_session(token)
        valid, *_ = db.validate_session(token, device_id="surgeon_console")
        assert valid is False

    def test_acl_reconciliation_on_conflict(self, temp_db):
        db, db_path = temp_db
        # Check initial ACL for user on system_control
        assert db.check_acl("user", "system_control", "publish") is True

        # Simulate ACL update and ensure re-seeding reconciles without DB reset
        conn = sqlite3.connect(db_path)
        db._seed_roles_and_acls(conn)
        conn.close()

        assert db.check_acl("user", "patient_vitals", "subscribe") is True
        assert db.check_acl("user", "patient_vitals", "publish") is False
        assert db.check_acl("data_generator", "patient_vitals", "publish") is True

    def test_device_verification_and_revocation(self, temp_db):
        db, _ = temp_db
        db.register_device("robot_console", "robot", "fp_robot_987654321")

        valid, dev_id, dev_type = db.verify_device("fp_robot_987654321")
        assert valid is True
        assert dev_id == "robot_console"
        assert dev_type == "robot"

        # Revoke device
        db.revoke_device("robot_console")
        valid, _, _ = db.verify_device("fp_robot_987654321")
        assert valid is False
