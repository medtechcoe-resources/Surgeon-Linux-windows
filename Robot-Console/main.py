# ═══════════════════════════════════════════════════════════════════
#  ROBOT CONSOLE — APPLICATION ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

import sys
import os

# Ensure project root is on sys.path for the shared networking package
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow

from shared_networking.database import AetherDatabase
from shared_networking.authentication import AuthManager
from shared_networking.login_dialog import LoginDialog
from shared_networking.config import DATABASE_PATH


def main():
    app = QApplication(sys.argv)

    # Set application metadata
    app.setApplicationName("Robot Console")
    app.setOrganizationName("MedRobot")
    app.setApplicationVersion("1.0")

    # ── Initialize Security Database ──────────────────────────────
    db = AetherDatabase.instance()
    if not db.open(DATABASE_PATH):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "Security Error",
            "Cannot open security database.\n"
            "Please run 'python broker.py --provision' first.",
        )
        sys.exit(1)

    # ── Initialize Authentication ─────────────────────────────────
    auth = AuthManager()
    if not auth.is_provisioned():
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "Not Provisioned",
            "No user accounts exist.\n"
            "Please run 'python broker.py --provision' first.",
        )
        sys.exit(1)

    # ── Show Login Dialog ─────────────────────────────────────────
    login = LoginDialog(
        title="Robot Console — Login",
        auth_manager=auth,
    )
    if login.exec() != LoginDialog.DialogCode.Accepted:
        sys.exit(0)

    # ── Launch Main Window ────────────────────────────────────────
    window = MainWindow(
        username=login.username,
        role=login.role,
        session_id=login.session_id,
    )
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
