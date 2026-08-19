# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — FIRST-TIME SECURITY PROVISIONING
#  Initialises the complete local security architecture on first run:
#
#    1. SQLite database (schema + roles + ACLs)
#    2. Aether Local CA
#    3. Broker certificate
#    4. Device certificates for each known client module
#    5. Initial administrator account (interactive CLI prompt)
#
#  Rules:
#    - Certificates are NOT regenerated on every startup.
#    - Cert generation only happens during explicit provisioning.
#    - No default credentials (admin/admin123) are created.
#    - Public registration is permanently disabled.
#    - Only authenticated admins can create additional users.
#
#  Usage:
#      python -m shared_networking.provisioning
#  or call is_provisioned() at startup and provision() if False.
# ═══════════════════════════════════════════════════════════════════

import os
import sys
import getpass
import logging

log = logging.getLogger(__name__)

# Known device identities (client_name → device_type)
# These map to the client_name strings used in ConnectionManager.
KNOWN_DEVICES = {
    "surgeon_console": "surgeon",
    "robot_console":   "robot",
    "observer_screen": "observer",
    "data_generator":  "data_generator",
}


def is_provisioned(db, tls_mgr) -> bool:
    """Return True if the system is already fully provisioned.

    Checks:
      - Database is open and has at least one user.
      - CA exists.
      - Broker cert exists.
    """
    if not db.is_ready:
        return False
    if db.is_first_run():
        return False
    if not tls_mgr.ca_exists():
        return False
    if not tls_mgr.broker_cert_exists():
        return False
    return True


def provision(db, tls_mgr, broker_host: str = "127.0.0.1",
              admin_username: str = None,
              admin_password: str = None) -> bool:
    """Run the complete first-time security provisioning sequence.

    If admin_username/admin_password are provided (e.g. for automated
    testing), they are used directly. Otherwise the user is prompted
    interactively via the CLI.

    Returns True if provisioning completed successfully.
    """
    print("\n" + "=" * 60)
    print("  AETHER CONSOLE -- FIRST-TIME SECURITY PROVISIONING")
    print("=" * 60)

    # Step 1: Database
    print("\n  [1/5] Database already initialised." if db.is_ready else
          "\n  [1/5] Opening database...")
    # (DB is opened by caller before provision() is invoked)
    if not db.is_ready:
        print("  [ERROR] Database is not open. Aborting.")
        return False
    print("  [OK] Database ready.")

    # Step 2: Aether Local CA
    print("\n  [2/5] Creating Aether Local CA...")
    if not tls_mgr.create_ca():
        print("  [ERROR] Failed to create CA. Aborting.")
        return False
    print("  [OK] Aether Local CA ready.")

    # Step 3: Broker Certificate
    print("\n  [3/5] Creating broker certificate...")
    if not tls_mgr.create_broker_cert(host=broker_host):
        print("  [ERROR] Failed to create broker cert. Aborting.")
        return False
    print("  [OK] Broker certificate ready.")

    # Step 4: Device Certificates
    print("\n  [4/5] Creating device certificates...")
    for device_id, device_type in KNOWN_DEVICES.items():
        ok, fingerprint = tls_mgr.create_device_cert(device_id, device_type)
        if not ok:
            print(f"  [ERROR] Failed to create cert for {device_id}")
            return False
        # Register device in the database
        db.register_device(device_id, device_type, fingerprint)
        print(f"  [OK] Device: {device_id} ({device_type})")
    print("  [OK] All device certificates ready.")

    # Step 5: Initial Administrator Account
    print("\n  [5/5] Creating initial administrator account...")
    if db.is_first_run():
        ok, error_msg = _create_admin_account(
            db, admin_username, admin_password)
        if not ok:
            print(f"  [ERROR] {error_msg}")
            return False
        print("  [OK] Administrator account created.")
    else:
        print("  [OK] Users already exist -- skipping admin creation.")

    print("\n" + "=" * 60)
    print("  PROVISIONING COMPLETE. System is ready.")
    print("=" * 60 + "\n")
    db.audit("PROVISIONING_COMPLETE",
             details="First-time security provisioning completed")
    return True


def _create_admin_account(db, admin_username: str = None,
                          admin_password: str = None) -> tuple:
    """Prompt for or accept the initial admin credentials.

    No default credentials are created. Public registration is
    permanently disabled — only this first admin account is created
    here; subsequent users must be created by an authenticated admin.

    Returns (True, '') on success or (False, error_message) on failure.
    """
    MIN_PASSWORD_LENGTH = 8

    if admin_username and admin_password:
        # Non-interactive mode (e.g., automated testing)
        username = admin_username.strip().lower()
        password = admin_password
    else:
        # Interactive CLI
        print("\n  No user accounts exist. Create the initial administrator.")
        print("  This account will be the only way to log in.\n")
        while True:
            username = input("  Admin username: ").strip().lower()
            if not username:
                print("  Username cannot be empty. Try again.")
                continue
            break

        while True:
            password = getpass.getpass("  Admin password: ")
            if len(password) < MIN_PASSWORD_LENGTH:
                print(f"  Password must be at least {MIN_PASSWORD_LENGTH} "
                      f"characters. Try again.")
                continue
            confirm = getpass.getpass("  Confirm password: ")
            if password != confirm:
                print("  Passwords do not match. Try again.")
                continue
            break

    ok, err = db.create_user(username, password, "admin")
    if not ok:
        return False, err

    log.info(f"[PROVISION] Initial admin account created: {username}")
    return True, ""
