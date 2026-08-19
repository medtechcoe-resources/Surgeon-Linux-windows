# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — LAUNCH ALL
#  Convenience script that starts the broker, Data Generator,
#  Surgeon Console, Robot Console, and Observer Screen as
#  subprocesses.
#
#  Usage:  python launch_all.py
# ═══════════════════════════════════════════════════════════════════

import subprocess
import sys
import os
import time
import signal

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    print("=" * 60)
    print("  AETHER CONSOLE — SYSTEM LAUNCHER")
    print("=" * 60)

    processes = []
    
    # Pre-check: ensure security is provisioned before starting subprocesses
    try:
        from shared_networking.database import AetherDatabase
        from shared_networking.tls import TLSManager
        from shared_networking.provisioning import is_provisioned, provision
        from shared_networking.config import DATABASE_PATH, CERTS_DIR
        print("\n  [0/5] Checking Security Provisioning...")
        db = AetherDatabase.instance()
        db.open(DATABASE_PATH)
        tls_mgr = TLSManager(CERTS_DIR)
        if not is_provisioned(db, tls_mgr):
            print("        System not provisioned — running first-time setup...")
            ok = provision(db, tls_mgr, broker_host="127.0.0.1")
            if not ok:
                print("  [ERROR] Provisioning failed. Cannot start system.")
                return
        print("        Security provisioning OK.")
    except Exception as e:
        print(f"        Warning: Could not check provisioning: {e}")

    try:
        # 1. Start Broker
        print("\n  [1/5] Starting Pub-Sub Broker...")
        broker = subprocess.Popen(
            [sys.executable, os.path.join(PROJECT_ROOT, "broker.py")],
            cwd=PROJECT_ROOT,
        )
        processes.append(("Broker", broker))
        time.sleep(1)  # Wait for broker to start

        # 2. Start Data Generator
        print("  [2/5] Starting Data Generator Backend...")
        datagen = subprocess.Popen(
            [sys.executable, os.path.join(
                PROJECT_ROOT, "Data-Generator", "main.py")],
            cwd=os.path.join(PROJECT_ROOT, "Data-Generator"),
        )
        processes.append(("Data Generator", datagen))
        time.sleep(1)  # Wait for generator to connect to broker

        # 3. Start Surgeon Console
        print("  [3/5] Starting Surgeon Console...")
        surgeon = subprocess.Popen(
            [sys.executable, os.path.join(PROJECT_ROOT, "main.py")],
            cwd=PROJECT_ROOT,
        )
        processes.append(("Surgeon Console", surgeon))
        time.sleep(0.5)

        # 4. Start Robot Console
        print("  [4/5] Starting Robot Console...")
        robot = subprocess.Popen(
            [sys.executable, os.path.join(
                PROJECT_ROOT, "Robot-Console", "main.py")],
            cwd=os.path.join(PROJECT_ROOT, "Robot-Console"),
        )
        processes.append(("Robot Console", robot))
        time.sleep(0.5)

        # 5. Start Observer Screen
        print("  [5/5] Starting Observer Screen...")
        observer = subprocess.Popen(
            [sys.executable, os.path.join(
                PROJECT_ROOT, "Observer-Screen", "main.py")],
            cwd=os.path.join(PROJECT_ROOT, "Observer-Screen"),
        )
        processes.append(("Observer Screen", observer))

        print("\n  All systems launched. Press Ctrl+C to stop all.\n")

        # Wait for any process to exit
        while True:
            for name, proc in processes:
                ret = proc.poll()
                if ret is not None:
                    print(f"  [{name}] exited with code {ret}")
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n  Shutting down all processes...")
        for name, proc in reversed(processes):
            print(f"  Stopping {name}...")
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("  All processes stopped.")


if __name__ == "__main__":
    main()
