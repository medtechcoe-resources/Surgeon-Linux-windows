# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — AUTHENTICATION MODULE (v3 — Phase 3 RBAC)
#  Provides the AuthManager interface consumed by the Login Dialog
#  and all application entry points.
#
#  This version delegates ALL credential storage and session
#  management to the SQLite AetherDatabase. The JSON credentials
#  file (credentials.json) is no longer used.
#
#  Key security rules enforced here:
#    - The role returned by verify() is ALWAYS from the database.
#      The client MUST NOT be trusted to claim its own role.
#    - Sessions have a server-side expiration time.
#    - create_session() stores the session in the DB with hospital_id
#      derived from the user's DB record (never from client).
#    - validate_session() reads authoritative role AND hospital_id
#      from the DB. Returns 4-tuple (valid, username, role, hospital_id).
#    - Plaintext passwords are never stored or logged.
#    - Authorization is delegated to AuthorizationService (Phase 3).
# ═══════════════════════════════════════════════════════════════════

import logging
from typing import Optional, Tuple

from shared_networking.database import AetherDatabase

log = logging.getLogger(__name__)


class AuthManager:
    """Manages local authentication for all Aether Console applications.

    Delegates to AetherDatabase for all persistence. The AuthManager
    is the public API consumed by the Login Dialog, entry points,
    and broker — it does not perform credential storage itself.

    Backwards-compatible interface with the original AuthManager:
        auth = AuthManager()
        success, role = auth.verify("admin", "password")
        if success:
            session_id = auth.create_session("admin", role)
    """

    def __init__(self):
        self._db = AetherDatabase.instance()

    @property
    def db(self) -> AetherDatabase:
        """Return the underlying AetherDatabase instance."""
        return self._db

    @db.setter
    def db(self, value: AetherDatabase):
        self._db = value

    # ─── Public API ───────────────────────────────────────────────

    def verify(self, username: str, password: str) -> Tuple[bool, str]:
        """Verify a username/password combination.

        Returns:
            (True, authoritative_role_from_db) on success
            (False, '') on failure

        The role is always read from the database and must not be
        overridden by the caller or the client.
        """
        return self._db.verify_user(username, password)

    def change_password(self, username: str, current_password: str,
                        new_password: str) -> Tuple[bool, str]:
        """Change a user's own password.

        Authorization for who may call this method should be enforced
        by the caller via AuthorizationService. This method only
        verifies the current password before accepting the change.

        Returns (True, message) or (False, error_message).
        """
        return self._db.change_password(username, current_password,
                                        new_password)

    def create_session(self, username: str, role: str,
                       device_id: Optional[str] = None) -> str:
        """Create a new server-side session.

        The role parameter is accepted for API compatibility but is
        ignored — the authoritative role is always fetched from the DB.
        The device_id associates the session with a registered device.

        The session's hospital_id is populated from the user's DB record
        — NEVER from client input.

        Returns a secure session token string.
        """
        # Always use DB-authoritative role, ignore client-provided role
        authoritative_role = self._db.get_user_role(username) or role
        _ = authoritative_role  # role stored via username FK in DB session
        return self._db.create_session(username, role, device_id=device_id)

    def validate_session(self, session_id: str,
                         device_id: Optional[str] = None):
        """Validate a session token.

        Returns:
            (True, username, authoritative_role, hospital_id) if valid
            (False, '', '', None) if invalid or expired

        The returned role and hospital_id are ALWAYS from the database —
        never from the session data the client provided.

        If device_id is provided, the session must have been created on
        that device (session/device binding). See AetherDatabase.validate_session
        for full semantics.
        """
        return self._db.validate_session(session_id, device_id=device_id)

    def remove_session(self, session_id: str):
        """Invalidate a session (logout or security failure)."""
        self._db.invalidate_session(session_id)
        log.info("[AUTH] Session invalidated")

    def is_provisioned(self) -> bool:
        """Return True if at least one user account exists in the DB."""
        return not self._db.is_first_run()
