"""
Tests for Phase 4 UI Refactor: Unified User Management Tab & Server-Side RBAC Visibility.

Validates:
- Exactly 6 top-level tabs for app_admin (User Management visible)
- Exactly 6 top-level tabs for hospital_admin (User Management visible, no hospital selector)
- Exactly 5 top-level tabs for technician, surgeon, doctor, observer (User Management hidden)
- Idempotent NavBar.add_tab() insertion (no duplicate tabs)
- Existing tab index invariance (indexes 0-4 remain Pre-Op, Live Video, Live Control, Settings, Comm Center)
- Invalid/expired session fails closed (tab not added)
- Strict server-side RBAC and role escalation protection
"""
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QScrollArea
from PyQt6.QtCore import Qt

from main import AetherConsole
from widgets.nav_tabs import NavBar, _draw_users
from screens.user_management import (
    UserManagementScreen,
    AppAdminView,
    HospitalAdminView,
)
from screens.preop_planning import PreopPlanningScreen
from screens.live_video import LiveVideoScreen
from screens.live_control import LiveControlScreen
from screens.settings import SettingsScreen
from screens.comm_center import CommCenterScreen

from shared_networking.database import AetherDatabase
from shared_networking.authentication import AuthManager
from shared_networking.authorization import AuthorizationService, PERM_USER_MANAGE
from shared_networking.admin_service import (
    AppAdminService,
    HospitalAdminService,
    HOSPITAL_ADMIN_ALLOWED_ROLES,
)


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def auth_fixture():
    """Create a temporary database with two hospitals and test accounts for all roles."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = AetherDatabase()
    db._ready = False
    db._path = None
    assert db.open(path) is True

    # 1. Hospitals
    ok, hosp_a = db.create_hospital("Apollo Chennai", "APOLLO", "Chennai, India")
    assert ok
    ok, hosp_b = db.create_hospital("Fortis Delhi", "FORTIS", "Delhi, India")
    assert ok

    # 2. Users for all roles
    # app_admin (APPLICATION scope, hospital_id=None)
    db.create_full_user("app_admin_user", "AppAdmin123!", "app_admin",
                        first_name="Global", last_name="Admin",
                        employee_id="SYS01", hospital_id=None)

    # hospital_admin (HOSPITAL scope, hospital_a)
    db.create_full_user("hosp_admin_user", "HospAdmin123!", "hospital_admin",
                        first_name="Apollo", last_name="Admin",
                        employee_id="HA01", hospital_id=hosp_a)

    # technician (HOSPITAL scope, hospital_a)
    db.create_full_user("tech_user", "TechPass123!", "technician",
                        first_name="Tom", last_name="Tech",
                        employee_id="TC01", hospital_id=hosp_a)

    # surgeon (HOSPITAL scope, hospital_a)
    db.create_full_user("surgeon_user", "SurgeonPass123!", "surgeon",
                        first_name="Sally", last_name="Surgeon",
                        employee_id="SG01", hospital_id=hosp_a)

    # doctor (HOSPITAL scope, hospital_a)
    db.create_full_user("doctor_user", "DoctorPass123!", "doctor",
                        first_name="Dan", last_name="Doctor",
                        employee_id="DR01", hospital_id=hosp_a)

    # observer (HOSPITAL scope, hospital_a)
    db.create_full_user("observer_user", "ObserverPass123!", "observer",
                        first_name="Oscar", last_name="Observer",
                        employee_id="OB01", hospital_id=hosp_a)

    # 3. Active Sessions
    sessions = {
        "app_admin": db.create_session("app_admin_user", "app_admin"),
        "hospital_admin": db.create_session("hosp_admin_user", "hospital_admin"),
        "technician": db.create_session("tech_user", "technician"),
        "surgeon": db.create_session("surgeon_user", "surgeon"),
        "doctor": db.create_session("doctor_user", "doctor"),
        "observer": db.create_session("observer_user", "observer"),
    }

    auth_manager = AuthManager()
    auth_manager._db = db

    yield {
        "db": db,
        "auth": auth_manager,
        "sessions": sessions,
        "hosp_a": hosp_a,
        "hosp_b": hosp_b,
    }

    try:
        os.remove(path)
    except Exception:
        pass


class TestUserManagementTabVisibility:
    """Test role-by-role visibility of the User Management tab in AetherConsole."""

    def test_app_admin_has_exactly_6_tabs_with_user_management(self, auth_fixture):
        """app_admin must have exactly 6 tabs; 6th is User Management with AppAdminView."""
        session_id = auth_fixture["sessions"]["app_admin"]
        window = AetherConsole(
            username="app_admin_user",
            role="app_admin",
            session_id=session_id,
            auth_manager=auth_fixture["auth"],
        )

        assert len(window.nav.buttons) == 6, f"Expected 6 nav tabs, got {len(window.nav.buttons)}"
        labels = [btn._label for btn in window.nav.buttons]
        assert labels == ["Pre-Op", "Live Video", "Live Control", "Settings", "Comm Center", "User Management"]
        assert window.stack.count() == 6

        assert window.user_mgmt_screen is not None
        assert isinstance(window.user_mgmt_screen, UserManagementScreen)
        assert isinstance(window.user_mgmt_screen.view, AppAdminView)

        # Verify AppAdminView contains the 5 required sections
        tab_names = [window.user_mgmt_screen.view._tabs.tabText(i).strip()
                     for i in range(window.user_mgmt_screen.view._tabs.count())]
        assert tab_names == ["Hospitals", "Users", "Create Hospital Admin", "Audit Log", "Configuration"]
        window.close()

    def test_hospital_admin_has_exactly_6_tabs_with_user_management(self, auth_fixture):
        """hospital_admin must have exactly 6 tabs; 6th is User Management with HospitalAdminView."""
        session_id = auth_fixture["sessions"]["hospital_admin"]
        window = AetherConsole(
            username="hosp_admin_user",
            role="hospital_admin",
            session_id=session_id,
            auth_manager=auth_fixture["auth"],
        )

        assert len(window.nav.buttons) == 6, f"Expected 6 nav tabs, got {len(window.nav.buttons)}"
        labels = [btn._label for btn in window.nav.buttons]
        assert labels == ["Pre-Op", "Live Video", "Live Control", "Settings", "Comm Center", "User Management"]
        assert window.stack.count() == 6

        assert window.user_mgmt_screen is not None
        assert isinstance(window.user_mgmt_screen, UserManagementScreen)
        assert isinstance(window.user_mgmt_screen.view, HospitalAdminView)

        # Verify HospitalAdminView contains the 5 required sections
        tab_names = [window.user_mgmt_screen.view._tabs.tabText(i).strip()
                     for i in range(window.user_mgmt_screen.view._tabs.count())]
        assert tab_names == ["My Hospital", "Users", "Create User", "Departments", "Audit Log"]

        # STRICT INVARIANT: Verify HospitalAdminView has NO hospital selector
        assert not hasattr(window.user_mgmt_screen.view, "_hosp_combo")
        assert not hasattr(window.user_mgmt_screen.view, "_ca_hosp_combo")
        window.close()

    def test_technician_has_exactly_5_tabs_user_management_hidden(self, auth_fixture):
        """technician must have exactly 5 tabs; User Management must NOT exist."""
        session_id = auth_fixture["sessions"]["technician"]
        window = AetherConsole(
            username="tech_user",
            role="technician",
            session_id=session_id,
            auth_manager=auth_fixture["auth"],
        )

        assert len(window.nav.buttons) == 5, f"Expected 5 nav tabs, got {len(window.nav.buttons)}"
        labels = [btn._label for btn in window.nav.buttons]
        assert labels == ["Pre-Op", "Live Video", "Live Control", "Settings", "Comm Center"]
        assert "User Management" not in labels
        assert window.stack.count() == 5
        assert window.user_mgmt_screen is None
        window.close()

    def test_surgeon_has_exactly_5_tabs_user_management_hidden(self, auth_fixture):
        """surgeon must have exactly 5 tabs; User Management must NOT exist."""
        session_id = auth_fixture["sessions"]["surgeon"]
        window = AetherConsole(
            username="surgeon_user",
            role="surgeon",
            session_id=session_id,
            auth_manager=auth_fixture["auth"],
        )

        assert len(window.nav.buttons) == 5
        labels = [btn._label for btn in window.nav.buttons]
        assert labels == ["Pre-Op", "Live Video", "Live Control", "Settings", "Comm Center"]
        assert "User Management" not in labels
        assert window.stack.count() == 5
        assert window.user_mgmt_screen is None
        window.close()

    def test_doctor_has_exactly_5_tabs_user_management_hidden(self, auth_fixture):
        """doctor must have exactly 5 tabs; User Management must NOT exist."""
        session_id = auth_fixture["sessions"]["doctor"]
        window = AetherConsole(
            username="doctor_user",
            role="doctor",
            session_id=session_id,
            auth_manager=auth_fixture["auth"],
        )

        assert len(window.nav.buttons) == 5
        labels = [btn._label for btn in window.nav.buttons]
        assert labels == ["Pre-Op", "Live Video", "Live Control", "Settings", "Comm Center"]
        assert "User Management" not in labels
        assert window.stack.count() == 5
        assert window.user_mgmt_screen is None
        window.close()

    def test_observer_has_exactly_5_tabs_user_management_hidden(self, auth_fixture):
        """observer must have exactly 5 tabs; User Management must NOT exist."""
        session_id = auth_fixture["sessions"]["observer"]
        window = AetherConsole(
            username="observer_user",
            role="observer",
            session_id=session_id,
            auth_manager=auth_fixture["auth"],
        )

        assert len(window.nav.buttons) == 5
        labels = [btn._label for btn in window.nav.buttons]
        assert labels == ["Pre-Op", "Live Video", "Live Control", "Settings", "Comm Center"]
        assert "User Management" not in labels
        assert window.stack.count() == 5
        assert window.user_mgmt_screen is None
        window.close()


class TestNavigationIntegrityAndIdempotency:
    """Test NavBar.add_tab idempotency, index stability, and screen mappings."""

    def test_add_tab_idempotency(self):
        """Calling add_tab multiple times with the same name must return existing index and avoid duplicate buttons."""
        nav = NavBar()
        initial_count = len(nav.buttons)
        assert initial_count == 5

        # First add
        idx1 = nav.add_tab("User Management", _draw_users)
        assert idx1 == 5
        assert len(nav.buttons) == 6

        # Second add (duplicate attempt)
        idx2 = nav.add_tab("User Management", _draw_users)
        assert idx2 == 5
        assert len(nav.buttons) == 6  # Count remains 6, no duplicate added

    def test_existing_tab_indexes_and_screens_invariance(self, auth_fixture):
        """Indexes 0-4 must consistently map to the existing surgical screens on both 5-tab and 6-tab layouts."""
        # 1. 5-tab window (surgeon)
        win_surgeon = AetherConsole(
            username="surgeon_user",
            role="surgeon",
            session_id=auth_fixture["sessions"]["surgeon"],
            auth_manager=auth_fixture["auth"],
        )
        assert win_surgeon.stack.count() == 5
        assert isinstance(win_surgeon.stack.widget(0).widget(), PreopPlanningScreen)
        assert isinstance(win_surgeon.stack.widget(1).widget(), LiveVideoScreen)
        assert isinstance(win_surgeon.stack.widget(2).widget(), LiveControlScreen)
        assert isinstance(win_surgeon.stack.widget(3).widget(), SettingsScreen)
        assert isinstance(win_surgeon.stack.widget(4).widget(), CommCenterScreen)

        # 2. 6-tab window (app_admin)
        win_admin = AetherConsole(
            username="app_admin_user",
            role="app_admin",
            session_id=auth_fixture["sessions"]["app_admin"],
            auth_manager=auth_fixture["auth"],
        )
        assert win_admin.stack.count() == 6
        assert isinstance(win_admin.stack.widget(0).widget(), PreopPlanningScreen)
        assert isinstance(win_admin.stack.widget(1).widget(), LiveVideoScreen)
        assert isinstance(win_admin.stack.widget(2).widget(), LiveControlScreen)
        assert isinstance(win_admin.stack.widget(3).widget(), SettingsScreen)
        assert isinstance(win_admin.stack.widget(4).widget(), CommCenterScreen)
        assert isinstance(win_admin.stack.widget(5).widget(), UserManagementScreen)

        # 3. Test navigation clicking index 5 switches stack to index 5
        win_admin.nav.set_active(5)
        assert win_admin.stack.currentIndex() == 5

        # Switching back to index 1 switches stack to index 1
        win_admin.nav.set_active(1)
        assert win_admin.stack.currentIndex() == 1

        win_surgeon.close()
        win_admin.close()


class TestSecurityAndSessionBoundaries:
    """Verify server-side security checks, fail-closed behavior, and role boundaries."""

    def test_invalid_session_fails_closed(self, auth_fixture):
        """Invalid or forged session token must result in 5 tabs (User Management absent)."""
        window = AetherConsole(
            username="attacker",
            role="app_admin",  # UI claims to be app_admin, but session is forged
            session_id="forged_token_12345",
            auth_manager=auth_fixture["auth"],
        )
        assert len(window.nav.buttons) == 5
        assert window.user_mgmt_screen is None
        assert window.stack.count() == 5
        window.close()

    def test_expired_or_revoked_session_fails_closed(self, auth_fixture):
        """Revoked session token must result in 5 tabs."""
        db = auth_fixture["db"]
        token = db.create_session("app_admin_user", "app_admin")
        auth_fixture["auth"].remove_session(token)  # Revoke session

        window = AetherConsole(
            username="app_admin_user",
            role="app_admin",
            session_id=token,
            auth_manager=auth_fixture["auth"],
        )
        assert len(window.nav.buttons) == 5
        assert window.user_mgmt_screen is None
        window.close()

    def test_technician_cannot_invoke_admin_services(self, auth_fixture):
        """Technician session token must be denied server-side by AppAdminService and HospitalAdminService."""
        tech_tok = auth_fixture["sessions"]["technician"]
        db = auth_fixture["db"]

        app_svc = AppAdminService(db=db)
        hosp_svc = HospitalAdminService(db=db)

        # App admin operations
        ok, _ = app_svc.create_hospital(tech_tok, "Rogue Hosp", "ROGUE")
        assert not ok
        ok, _ = app_svc.list_all_users(tech_tok)
        assert not ok

        # Hospital admin operations
        ok, _ = hosp_svc.list_my_users(tech_tok)
        assert not ok
        ok, _ = hosp_svc.create_user(tech_tok, "rogue_tech", "Pass1234!", "surgeon", "Rogue")
        assert not ok

    def test_hospital_admin_cannot_escalate_roles(self):
        """HospitalAdmin allowed roles must be strictly limited to technician, surgeon, doctor, observer."""
        assert "app_admin" not in HOSPITAL_ADMIN_ALLOWED_ROLES
        assert "hospital_admin" not in HOSPITAL_ADMIN_ALLOWED_ROLES
        assert HOSPITAL_ADMIN_ALLOWED_ROLES == {"technician", "surgeon", "doctor", "observer"}
