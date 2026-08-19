# ═══════════════════════════════════════════════════════════════════
#  OBSERVER SCREEN — STANDALONE MONITORING APPLICATION
# ═══════════════════════════════════════════════════════════════════

import sys
import os

# Add parent directory of Observer-Screen to path to import shared modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow
from styles import generate_stylesheet

from shared_networking.database import AetherDatabase
from shared_networking.authentication import AuthManager
from shared_networking.login_dialog import LoginDialog
from shared_networking.config import DATABASE_PATH


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Clinical Observer Screen")

    # Apply stylesheet
    app.setStyleSheet(generate_stylesheet())

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
        title="Observer Screen — Login",
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
