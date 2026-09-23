# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — CENTRALIZED AUTHORIZATION SERVICE (Phase 3)
#
#  Single point for ALL permission and hospital-scope checks.
#  No authorization logic should be scattered in UI or handler code.
#
#  Design rules:
#    - Hospital scope is ALWAYS derived from the authenticated
#      session / user DB record. NEVER from client input.
#    - Target-aware: authorize() checks actor → permission →
#      actor scope → target resource → target hospital.
#    - Deny by default. Missing permission = denied.
#    - APPLICATION scope (app_admin) can operate across hospitals.
#    - HOSPITAL scope can ONLY operate within own hospital.
#    - Audit all authorization failures.
#    - NEVER log passwords, tokens, private keys, or secrets.
#
#  Permission granularity:
#    USER_MANAGE is a single permission now, designed to later
#    split into USER_CREATE, USER_VIEW, USER_UPDATE, USER_ENABLE,
#    USER_DISABLE, ROLE_ASSIGN without schema changes.
# ═══════════════════════════════════════════════════════════════════

import logging
from dataclasses import dataclass, field
from typing import Optional

from shared_networking.database import AetherDatabase

log = logging.getLogger(__name__)


# ─── Permission Constants ─────────────────────────────────────────
# These MUST match the names seeded in migration M002.

PERM_HOSPITAL_MANAGE = "HOSPITAL_MANAGE"
PERM_USER_MANAGE = "USER_MANAGE"
PERM_ROLE_MANAGE = "ROLE_MANAGE"
PERM_PASSWORD_RESET_ADMIN = "PASSWORD_RESET_ADMIN"
PERM_PASSWORD_RESET_RECOVERY = "PASSWORD_RESET_RECOVERY"
PERM_DEVICE_MANAGE = "DEVICE_MANAGE"
PERM_SURGERY_MANAGE = "SURGERY_MANAGE"
PERM_AUDIT_ACCESS = "AUDIT_ACCESS"
PERM_APP_CONFIG = "APP_CONFIG"

ALL_PERMISSIONS = [
    PERM_HOSPITAL_MANAGE,
    PERM_USER_MANAGE,
    PERM_ROLE_MANAGE,
    PERM_PASSWORD_RESET_ADMIN,
    PERM_PASSWORD_RESET_RECOVERY,
    PERM_DEVICE_MANAGE,
    PERM_SURGERY_MANAGE,
    PERM_AUDIT_ACCESS,
    PERM_APP_CONFIG,
]

# ─── Scope Constants ──────────────────────────────────────────────

SCOPE_APPLICATION = "APPLICATION"
SCOPE_HOSPITAL = "HOSPITAL"
SCOPE_DEVICE = "DEVICE"


@dataclass
class AuthResult:
    """Result of an authorization check.

    Attributes:
        allowed:            True if the operation is permitted.
        actor_username:     The authenticated actor's username.
        actor_role:         The actor's DB-authoritative role.
        actor_hospital_id:  The actor's hospital (None = APPLICATION scope).
        denial_reason:      Human-readable reason for denial (empty if allowed).
    """
    allowed: bool
    actor_username: str = ""
    actor_role: str = ""
    actor_hospital_id: Optional[str] = None
    denial_reason: str = ""


class AuthorizationService:
    """Centralized server-side authorization.

    All permission checks go through this service. Hospital isolation
    is enforced here — callers do NOT need to implement scope checks.

    Usage:
        authz = AuthorizationService()
        result = authz.authorize(session_token, PERM_USER_MANAGE,
                                 target_hospital_id=some_hospital)
        if not result.allowed:
            log.warning(f"Denied: {result.denial_reason}")
    """

    def __init__(self, db: AetherDatabase = None):
        self._db = db or AetherDatabase.instance()

    # ─── Primary Authorization Entry Point ────────────────────────

    def authorize(
        self,
        session_token: str,
        permission: str,
        target_hospital_id: Optional[str] = None,
        target_username: Optional[str] = None,
    ) -> AuthResult:
        """Target-aware authorization check.

        Flow:
          1. Validate session → derive actor identity, role, hospital_id
          2. Check role has the requested permission
          3. If actor scope == HOSPITAL:
             - If target_hospital_id provided: actor.hospital == target
             - If target_username provided: target user belongs to actor's hospital
          4. If actor scope == APPLICATION: no hospital restriction
          5. Self-modification guard: cannot change own role
          6. Audit denial on failure

        Args:
            session_token:      The actor's session token.
            permission:         Permission constant (e.g. PERM_USER_MANAGE).
            target_hospital_id: Hospital being operated on (optional).
            target_username:    User being operated on (optional).

        Returns:
            AuthResult with allowed=True/False and context.

        Critical rules:
            - Hospital_id is ALWAYS derived from the session, never from client.
            - Client-supplied role or hospital_id is NEVER trusted.
            - Deny by default on any check failure.
        """
        # ── Step 1: Validate session ──────────────────────────────
        valid, actor_username, actor_role, actor_hospital_id = \
            self._db.validate_session(session_token)

        if not valid:
            self._audit_denial(
                "AUTHZ_DENIED", username="",
                details="invalid or expired session")
            return AuthResult(
                allowed=False,
                denial_reason="Invalid or expired session")

        # ── Step 2: Check role has permission ─────────────────────
        if not self._db.role_has_permission(actor_role, permission):
            self._audit_denial(
                "AUTHZ_DENIED", username=actor_username,
                details=(
                    f"role={actor_role} lacks permission={permission}"
                ))
            return AuthResult(
                allowed=False,
                actor_username=actor_username,
                actor_role=actor_role,
                actor_hospital_id=actor_hospital_id,
                denial_reason=(
                    f"Role '{actor_role}' does not have "
                    f"permission '{permission}'"
                ))

        # ── Step 3: Hospital scope enforcement ────────────────────
        actor_scope = self._db.get_role_scope(actor_role)

        if actor_scope == SCOPE_HOSPITAL:
            # Hospital-scoped actor — enforce isolation

            # 3a. Check against target hospital
            if target_hospital_id is not None:
                if actor_hospital_id != target_hospital_id:
                    self._audit_denial(
                        "AUTHZ_CROSS_HOSPITAL",
                        username=actor_username,
                        details=(
                            f"role={actor_role} "
                            f"actor_hospital={actor_hospital_id} "
                            f"target_hospital={target_hospital_id}"
                        ))
                    return AuthResult(
                        allowed=False,
                        actor_username=actor_username,
                        actor_role=actor_role,
                        actor_hospital_id=actor_hospital_id,
                        denial_reason=(
                            "Cross-hospital access denied: actor belongs to "
                            f"hospital {actor_hospital_id}, target is "
                            f"{target_hospital_id}"
                        ))

            # 3b. Check against target user's hospital
            if target_username is not None:
                target_user = self._db.get_user_profile(target_username)
                if target_user is None:
                    return AuthResult(
                        allowed=False,
                        actor_username=actor_username,
                        actor_role=actor_role,
                        actor_hospital_id=actor_hospital_id,
                        denial_reason=f"Target user not found: {target_username}")

                target_user_hospital = target_user.get("hospital_id")
                if target_user_hospital != actor_hospital_id:
                    self._audit_denial(
                        "AUTHZ_CROSS_HOSPITAL",
                        username=actor_username,
                        details=(
                            f"role={actor_role} "
                            f"actor_hospital={actor_hospital_id} "
                            f"target_user={target_username} "
                            f"target_hospital={target_user_hospital}"
                        ))
                    return AuthResult(
                        allowed=False,
                        actor_username=actor_username,
                        actor_role=actor_role,
                        actor_hospital_id=actor_hospital_id,
                        denial_reason=(
                            "Cross-hospital access denied: target user "
                            f"'{target_username}' belongs to hospital "
                            f"{target_user_hospital}"
                        ))

        # APPLICATION scope — no hospital restriction (app_admin)

        # ── Step 4: Self-modification guard for role changes ──────
        if permission == PERM_ROLE_MANAGE and target_username is not None:
            if target_username.strip().lower() == actor_username:
                self._audit_denial(
                    "AUTHZ_DENIED", username=actor_username,
                    details="attempted to change own role")
                return AuthResult(
                    allowed=False,
                    actor_username=actor_username,
                    actor_role=actor_role,
                    actor_hospital_id=actor_hospital_id,
                    denial_reason="Cannot change your own role")

        # ── All checks passed ─────────────────────────────────────
        return AuthResult(
            allowed=True,
            actor_username=actor_username,
            actor_role=actor_role,
            actor_hospital_id=actor_hospital_id)

    # ─── Hospital Scope Check ─────────────────────────────────────

    def check_hospital_scope(
        self,
        session_token: str,
        target_hospital_id: str,
    ) -> bool:
        """Check if the actor may access the target hospital.

        Returns True if:
          - Actor has APPLICATION scope (app_admin) → always True
          - Actor has HOSPITAL scope and actor.hospital_id == target

        Returns False otherwise. Never trusts client-supplied hospital_id.
        """
        valid, _, actor_role, actor_hospital_id = \
            self._db.validate_session(session_token)
        if not valid:
            return False

        actor_scope = self._db.get_role_scope(actor_role)
        if actor_scope == SCOPE_APPLICATION:
            return True

        return actor_hospital_id == target_hospital_id

    # ─── Effective Hospital Derivation ────────────────────────────

    def get_effective_hospital_id(
        self, session_token: str
    ) -> Optional[str]:
        """Derive the actor's hospital from session → user → DB.

        Returns:
            hospital_id for HOSPITAL-scope actors
            None for APPLICATION-scope actors (app_admin)
            None if session is invalid

        NEVER accepts hospital_id from client input.
        """
        valid, _, _, hospital_id = self._db.validate_session(session_token)
        if not valid:
            return None
        return hospital_id

    # ─── Internal ─────────────────────────────────────────────────

    def _audit_denial(self, event_type: str, username: str = "",
                      details: str = ""):
        """Audit an authorization failure.

        Never logs passwords, hashes, recovery codes, or private keys.
        """
        self._db.audit(
            event_type,
            username=username or None,
            details=details,
        )
        log.warning(f"[AUTHZ] {event_type}: {details}")
