# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — ADMIN LAUNCHER / ROLE ROUTER (Phase 4)
#
#  After a successful login, route the authenticated user to the
#  correct application window based on their DB-authoritative role:
#
#    app_admin       → AppAdminWindow     (application admin panel)
#    hospital_admin  → HospitalAdminWindow (hospital admin panel)
#    surgical roles  → AetherConsole      (existing surgical console)
#    unknown/other   → fail closed        (session invalidated, exit)
#
#  Design rules:
#    - Role is ALWAYS the DB-authoritative value from the login result.
#      The caller must not re-derive or override it.
#    - Unknown privileged roles fail closed.  They do NOT fall through
#      to the surgical console.
#    - Session is invalidated on any fail-closed path.
# ═══════════════════════════════════════════════════════════════════

import logging
import sys

from PyQt6.QtWidgets import QMessageBox

from shared_networking.authentication import AuthManager

log = logging.getLogger(__name__)

# Roles that are allowed to enter the surgical console
_SURGICAL_ROLES = frozenset({
    "user", "surgeon", "doctor", "observer",
    "technician", "robot_console", "data_generator",
})


def route_after_login(app, login_result, auth_manager: AuthManager):
    """Inspect the DB-authoritative role and open the correct window.

    Args:
        app:           The running QApplication instance.
        login_result:  The LoginDialog result object (has .username,
                       .role, .session_id attributes).
        auth_manager:  The AuthManager for session invalidation.

    Returns:
        The QMainWindow that was shown (maximised), or None if the
        application should exit (fail-closed path).

    The caller must call sys.exit(app.exec()) after this function
    returns a non-None window.
    """
    role = login_result.role
    username = login_result.username
    session_id = login_result.session_id

    log.info(f"[LAUNCHER] Routing: username={username}, role={role}")

    if role == "app_admin":
        from screens.app_admin import AppAdminWindow
        window = AppAdminWindow(
            username=username,
            session_id=session_id,
            auth_manager=auth_manager,
        )
        window.showMaximized()
        return window

    elif role == "hospital_admin":
        from screens.hospital_admin import HospitalAdminWindow
        window = HospitalAdminWindow(
            username=username,
            session_id=session_id,
            auth_manager=auth_manager,
        )
        window.showMaximized()
        return window

    elif role in _SURGICAL_ROLES:
        from main import AetherConsole
        window = AetherConsole(
            username=username,
            role=role,
            session_id=session_id,
            auth_manager=auth_manager,
        )
        window.showMaximized()
        return window

    else:
        # Unknown or unhandled privileged role — FAIL CLOSED (R12)
        log.error(
            f"[LAUNCHER] Unknown role '{role}' for user '{username}'. "
            "Session invalidated. Refusing to launch any console."
        )
        auth_manager.remove_session(session_id)
        QMessageBox.critical(
            None,
            "Access Denied",
            f"Role '{role}' does not have a valid console entry point.\n\n"
            "Your session has been invalidated.\n"
            "Contact your system administrator.",
        )
        return None
