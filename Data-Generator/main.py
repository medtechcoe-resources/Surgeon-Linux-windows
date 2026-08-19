# ═══════════════════════════════════════════════════════════════════
#  DATA GENERATOR — MAIN ENTRY POINT
#  Headless backend console.  Starts a QCoreApplication (no window),
#  connects to the broker, and publishes simulated data.
#  A live ANSI terminal dashboard is rendered in the same console.
#
#  Usage:
#      python main.py
#
#  Keyboard controls (while running):
#      P — pause / resume all generators
#      Q — quit
# ═══════════════════════════════════════════════════════════════════

import sys
import os
import signal

# ── Ensure project root is on sys.path ───────────────────────────
_THIS_DIR    = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_THIS_DIR, _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Use QCoreApplication — no GUI needed
from PyQt6.QtCore import QCoreApplication, QTimer

from shared_networking.database import AetherDatabase
from shared_networking.config import DATABASE_PATH

from publisher import DataPublisher
from console_ui import ConsoleUI


def main():
    app = QCoreApplication(sys.argv)
    app.setApplicationName("Aether Data Generator")
    app.setOrganizationName("MedRobot")
    app.setApplicationVersion("4.2")

    # ── Initialize Security Database (read-only access) ───────────
    # Data Generator does not manage users — it only needs the DB
    # open so connection_manager can resolve its TLS context.
    db = AetherDatabase.instance()
    if not db.open(DATABASE_PATH):
        print("[ERROR] Cannot open security database.")
        print("        Run 'python broker.py --provision' first.")
        sys.exit(1)

    # ── Publisher (owns all generators) ───────────────────────────
    publisher = DataPublisher()

    # ── Terminal dashboard ─────────────────────────────────────────
    ui = ConsoleUI(publisher)

    # ── Wire keyboard commands ────────────────────────────────────
    def on_pause():
        if publisher.is_paused:
            publisher.resume_all()
            ui.notify_paused(False)
        else:
            publisher.pause_all()
            ui.notify_paused(True)

    def on_quit():
        ui.stop()
        publisher.stop()
        QTimer.singleShot(500, app.quit)

    ui.quit_requested.connect(on_quit)
    ui.pause_requested.connect(on_pause)

    # ── Handle SIGINT (Ctrl+C) cleanly ───────────────────────────
    def _sigint_handler(sig, frame):
        on_quit()

    signal.signal(signal.SIGINT, _sigint_handler)

    # ── Start everything ─────────────────────────────────────────
    ui.start()
    publisher.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
