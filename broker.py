# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — PUB-SUB BROKER LAUNCHER
#  Standalone script to run the central pub-sub broker.
#  Start this before launching any console applications.
#
#  Usage:  python broker.py
#          python broker.py --host 0.0.0.0 --port 5000
#          python broker.py --provision   (first-time setup)
# ═══════════════════════════════════════════════════════════════════

import sys
import os
import argparse
import logging

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared_networking.broker import PubSubBroker
from shared_networking.config import BROKER_HOST, BROKER_PORT, DATABASE_PATH, CERTS_DIR
from shared_networking.database import AetherDatabase
from shared_networking.tls import TLSManager
from shared_networking.provisioning import is_provisioned, provision


def main():
    parser = argparse.ArgumentParser(
        description="Aether Pub-Sub Broker (Secure)")
    parser.add_argument("--host", default=BROKER_HOST,
                        help=f"Bind address (default: {BROKER_HOST})")
    parser.add_argument("--port", type=int, default=BROKER_PORT,
                        help=f"Bind port (default: {BROKER_PORT})")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose logging")
    parser.add_argument("--provision", action="store_true",
                        help="Force re-run of first-time provisioning")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Security Provisioning Check ───────────────────────────────
    db = AetherDatabase.instance()
    db.open(DATABASE_PATH)
    tls_mgr = TLSManager(CERTS_DIR)

    if args.provision or not is_provisioned(db, tls_mgr):
        print("\n  [!] System is not provisioned. Running first-time setup...")
        ok = provision(db, tls_mgr, broker_host=args.host)
        if not ok:
            print("\n  ✖ Provisioning failed. Cannot start broker.")
            sys.exit(1)

    # ── Start Broker ──────────────────────────────────────────────
    broker = PubSubBroker(host=args.host, port=args.port)
    try:
        broker.start()
    except KeyboardInterrupt:
        print("\n  Broker shutting down...")
        broker.stop()
    except RuntimeError as e:
        print(f"\n  ✖ Broker failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
