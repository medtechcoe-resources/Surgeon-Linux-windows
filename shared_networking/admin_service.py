# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — PHASE 4 ADMINISTRATIVE SERVICE LAYER
#
#  Two service classes that provide all server-side administrative
#  operations for the admin hierarchy:
#
#    AppAdminService   — application-scope operations (app_admin role)
#    HospitalAdminService — hospital-scope operations (hospital_admin role)
#
#  Design rules (Phase 4):
#    - Every public method accepts a session_token and re-derives
#      actor identity, role, and hospital from the session via
#      AuthorizationService.  NEVER accept identity from callers.
#    - All operations go through AuthorizationService.authorize()
#      before touching the database.  Deny by default.
#    - Hospital scope is ALWAYS from the session — never from UI input.
#    - Hospital Admin may only create/manage users with roles in
#      HOSPITAL_ADMIN_ALLOWED_ROLES.  Assigning app_admin or
#      hospital_admin is permanently denied.
#    - Self-modification guard: an admin cannot disable or change
#      the role of their own account.
#    - PASSWORD_RESET_RECOVERY is intentionally omitted from Phase 4.
#      It will be implemented in Phase 6.
#    - NEVER log passwords, tokens, private keys, or secrets.
#    - Every successful AND denied operation writes an audit event.
# ═══════════════════════════════════════════════════════════════════

import logging
from typing import Optional, Tuple

from shared_networking.database import AetherDatabase
from shared_networking.authorization import (
    AuthorizationService,
    PERM_HOSPITAL_MANAGE,
    PERM_USER_MANAGE,
    PERM_ROLE_MANAGE,
    PERM_AUDIT_ACCESS,
    PERM_APP_CONFIG,
)
from shared_networking.config import (
    APP_VERSION, BROKER_HOST, BROKER_PORT, DATABASE_PATH,
)

log = logging.getLogger(__name__)

# ─── Role Constants ───────────────────────────────────────────────

# Roles that Hospital Admin may create or assign.
# hospital_admin and app_admin are deliberately excluded.
HOSPITAL_ADMIN_ALLOWED_ROLES = frozenset({
    "technician", "surgeon", "doctor", "observer",
})


# ─── App Admin Service ────────────────────────────────────────────

class AppAdminService:
    """Service layer for Application Admin operations.

    All operations require an active session with role 'app_admin'
    (APPLICATION scope). Hospital isolation is not applicable at this
    level — app_admin may operate across all hospitals.

    Usage:
        svc = AppAdminService()
        ok, result = svc.create_hospital(token, "Apollo", "APOLLO")
    """

    def __init__(self, db: AetherDatabase = None,
                 authz: AuthorizationService = None):
        self._db = db or AetherDatabase.instance()
        self._authz = authz or AuthorizationService(self._db)

    # ─── Hospital Management ──────────────────────────────────────

    def create_hospital(self, token: str, name: str, code: str,
                        address: Optional[str] = None) -> Tuple[bool, str]:
        """Create a new hospital organisation.

        Returns (True, hospital_id) on success or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_HOSPITAL_MANAGE)
        if not result.allowed:
            self._audit_denial("ADMIN_HOSPITAL_CREATE_DENIED",
                               result, f"name={name}, code={code}")
            return False, result.denial_reason

        ok, val = self._db.create_hospital(name, code, address)
        if ok:
            self._db.audit(
                "ADMIN_HOSPITAL_CREATED",
                username=result.actor_username,
                details=f"name={name}, code={code}",
            )
        return ok, val

    def set_hospital_active(self, token: str, hospital_id: str,
                            active: bool) -> Tuple[bool, str]:
        """Activate or deactivate a hospital.

        Caller must present a confirmation dialog before invoking this method.
        Returns (True, '') or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_HOSPITAL_MANAGE)
        if not result.allowed:
            action = "ACTIVATE" if active else "DEACTIVATE"
            self._audit_denial(f"ADMIN_HOSPITAL_{action}_DENIED",
                               result, f"hospital_id={hospital_id}")
            return False, result.denial_reason

        ok, err = self._db.set_hospital_active(hospital_id, active)
        if ok:
            action_word = "activated" if active else "deactivated"
            self._db.audit(
                f"ADMIN_HOSPITAL_{action_word.upper()}",
                username=result.actor_username,
                details=f"hospital_id={hospital_id}",
            )
        return ok, err

    def list_hospitals(self, token: str) -> Tuple[bool, object]:
        """Return all hospitals (active and inactive).

        Returns (True, list[dict]) or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_HOSPITAL_MANAGE)
        if not result.allowed:
            return False, result.denial_reason

        # list_hospitals() returns only active; use direct query for full list
        hospitals = self._db._query(
            "SELECT * FROM hospitals ORDER BY name"
        )
        return True, hospitals

    # ─── Hospital Admin Creation ──────────────────────────────────

    def create_hospital_admin(self, token: str, hospital_id: str,
                              username: str, password: str,
                              first_name: str,
                              last_name: str = "",
                              employee_id: str = "") -> Tuple[bool, str]:
        """Create a hospital_admin user assigned to a specific hospital.

        The hospital_id is re-validated server-side against the DB.
        Even if the UI provides a hospital_id, it is verified here.

        Returns (True, generated_user_id) or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_USER_MANAGE)
        if not result.allowed:
            self._audit_denial("ADMIN_HOSPITAL_ADMIN_CREATE_DENIED",
                               result, f"target_hospital={hospital_id}, username={username}")
            return False, result.denial_reason

        # Server-side hospital validation — never trust UI input alone
        hosp = self._db.get_hospital(hospital_id)
        if not hosp:
            log.warning(f"[ADMIN_SVC] create_hospital_admin: invalid hospital_id={hospital_id}")
            self._db.audit(
                "ADMIN_HOSPITAL_ADMIN_CREATE_DENIED",
                username=result.actor_username,
                details=f"invalid hospital_id={hospital_id}",
            )
            return False, f"Hospital not found: {hospital_id}"

        ok, val = self._db.create_full_user(
            username=username,
            password=password,
            role="hospital_admin",
            hospital_id=hospital_id,
            first_name=first_name,
            last_name=last_name,
            employee_id=employee_id,
        )
        if ok:
            self._db.audit(
                "ADMIN_HOSPITAL_ADMIN_CREATED",
                username=result.actor_username,
                details=(
                    f"new_user={username}, hospital_id={hospital_id}, "
                    f"hospital_name={hosp['name']}"
                ),
            )
            log.info(
                f"[ADMIN_SVC] Hospital admin created: {username} "
                f"for hospital {hosp['name']}"
            )
        return ok, val

    # ─── User Overview ────────────────────────────────────────────

    def list_all_users(self, token: str) -> Tuple[bool, object]:
        """Return all users across all hospitals.

        Returns (True, list[dict]) or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_USER_MANAGE)
        if not result.allowed:
            return False, result.denial_reason

        users = self._db._query(
            "SELECT u.id, u.username, u.user_id, u.uuid, "
            "u.first_name, u.last_name, u.is_enabled, u.created_at, "
            "r.name as role, r.scope, "
            "h.name as hospital_name, h.code as hospital_code "
            "FROM users u "
            "JOIN roles r ON u.role_id = r.id "
            "LEFT JOIN hospitals h ON u.hospital_id = h.id "
            "ORDER BY u.username"
        )
        return True, users

    # ─── Audit Logs (Global) ──────────────────────────────────────

    def get_audit_logs(self, token: str,
                       limit: int = 200) -> Tuple[bool, object]:
        """Return the most recent global audit log entries.

        Returns (True, list[dict]) or (False, error_message).
        Never includes passwords, hashes, or private keys.
        """
        result = self._authz.authorize(token, PERM_AUDIT_ACCESS)
        if not result.allowed:
            self._audit_denial("ADMIN_AUDIT_ACCESS_DENIED", result)
            return False, result.denial_reason

        logs = self._db.get_recent_audit_logs(limit)
        return True, logs

    # ─── Application Configuration ────────────────────────────────

    def get_app_config(self, token: str) -> Tuple[bool, object]:
        """Return safe application configuration values for display.

        Only a whitelist of non-sensitive values is returned.
        NEVER returns passwords, hashes, private keys, certificates,
        recovery secrets, or any authentication credentials.

        Returns (True, dict) or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_APP_CONFIG)
        if not result.allowed:
            return False, result.denial_reason

        import os
        safe_config = {
            "version": APP_VERSION,
            "broker_host": BROKER_HOST,
            "broker_port": BROKER_PORT,
            "database_file": os.path.basename(DATABASE_PATH),
            "session_username": result.actor_username,
            "session_role": result.actor_role,
        }
        return True, safe_config

    # ─── Internal ─────────────────────────────────────────────────

    def _audit_denial(self, event_type: str, result, details: str = ""):
        self._db.audit(
            event_type,
            username=result.actor_username or None,
            details=details or result.denial_reason,
        )


# ─── Hospital Admin Service ───────────────────────────────────────

class HospitalAdminService:
    """Service layer for Hospital Admin operations.

    All operations require an active session with role 'hospital_admin'
    (HOSPITAL scope). Hospital identity is derived from the session —
    never accepted from the caller or the UI.

    Roles that may be created or assigned:
        HOSPITAL_ADMIN_ALLOWED_ROLES = {technician, surgeon, doctor, observer}
    Attempting to create or assign hospital_admin or app_admin is a
    permanent denial (ADMIN_ROLE_ESCALATION_DENIED).

    Self-modification guard: admins cannot change their own role or
    disable their own account.
    """

    def __init__(self, db: AetherDatabase = None,
                 authz: AuthorizationService = None):
        self._db = db or AetherDatabase.instance()
        self._authz = authz or AuthorizationService(self._db)

    # ─── Hospital View ────────────────────────────────────────────

    def get_my_hospital(self, token: str) -> Tuple[bool, object]:
        """Return the hospital record for the authenticated admin.

        Returns (True, dict) or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_USER_MANAGE)
        if not result.allowed:
            return False, result.denial_reason

        hospital_id = result.actor_hospital_id
        if not hospital_id:
            return False, "Authenticated user has no assigned hospital."

        hosp = self._db.get_hospital(hospital_id)
        if not hosp:
            return False, f"Hospital record not found for id={hospital_id}"

        # Augment with user/dept counts
        user_count = self._db._query_one(
            "SELECT COUNT(*) as cnt FROM users WHERE hospital_id = ?",
            (hospital_id,),
        )
        dept_count = self._db._query_one(
            "SELECT COUNT(*) as cnt FROM departments "
            "WHERE hospital_id = ? AND is_active = 1",
            (hospital_id,),
        )
        hosp_dict = dict(hosp)
        hosp_dict["user_count"] = user_count["cnt"] if user_count else 0
        hosp_dict["dept_count"] = dept_count["cnt"] if dept_count else 0
        return True, hosp_dict

    # ─── User Management ──────────────────────────────────────────

    def list_my_users(self, token: str) -> Tuple[bool, object]:
        """Return all users in the authenticated admin's hospital.

        Returns (True, list[dict]) or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_USER_MANAGE)
        if not result.allowed:
            return False, result.denial_reason

        hospital_id = result.actor_hospital_id
        if not hospital_id:
            return False, "Authenticated user has no assigned hospital."

        users = self._db.get_users_for_hospital(hospital_id)
        return True, users

    def create_user(self, token: str, username: str, password: str,
                    role: str, first_name: str,
                    last_name: str = "", employee_id: str = "",
                    department_id: Optional[int] = None) -> Tuple[bool, str]:
        """Create a hospital-scoped user.

        Role must be in HOSPITAL_ADMIN_ALLOWED_ROLES.  Attempting to
        create a hospital_admin or app_admin is denied and audited.
        Hospital ID is derived from session — never from caller.

        Returns (True, generated_user_id) or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_USER_MANAGE)
        if not result.allowed:
            self._audit_denial("ADMIN_USER_CREATE_DENIED",
                               result, f"username={username}, role={role}")
            return False, result.denial_reason

        # Role escalation guard (R9)
        if role not in HOSPITAL_ADMIN_ALLOWED_ROLES:
            denial = (
                f"Hospital Admin cannot create users with role '{role}'. "
                f"Allowed roles: {sorted(HOSPITAL_ADMIN_ALLOWED_ROLES)}"
            )
            self._db.audit(
                "ADMIN_ROLE_ESCALATION_DENIED",
                username=result.actor_username,
                details=f"attempted role={role}, target_username={username}",
            )
            log.warning(
                f"[ADMIN_SVC] Role escalation denied: "
                f"{result.actor_username} → create user with role={role}"
            )
            return False, denial

        hospital_id = result.actor_hospital_id
        if not hospital_id:
            return False, "Authenticated user has no assigned hospital."

        ok, val = self._db.create_full_user(
            username=username,
            password=password,
            role=role,
            hospital_id=hospital_id,
            first_name=first_name,
            last_name=last_name,
            employee_id=employee_id,
            department_id=department_id,
        )
        if ok:
            self._db.audit(
                "ADMIN_USER_CREATED",
                username=result.actor_username,
                details=f"new_user={username}, role={role}, hospital_id={hospital_id}",
            )
            log.info(
                f"[ADMIN_SVC] User created: {username} (role={role}) "
                f"by {result.actor_username} in hospital {hospital_id}"
            )
        return ok, val

    def set_user_enabled(self, token: str, target_username: str,
                         enabled: bool) -> Tuple[bool, str]:
        """Enable or disable a hospital user.

        Guards:
          - Target user must belong to actor's hospital (R7).
          - Actor cannot modify own account (R6 / self-lockout).

        Returns (True, '') or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_USER_MANAGE)
        if not result.allowed:
            self._audit_denial("ADMIN_USER_ENABLE_DENIED",
                               result, f"target={target_username}, enabled={enabled}")
            return False, result.denial_reason

        # Self-modification guard
        if target_username.strip().lower() == result.actor_username:
            self._db.audit(
                "ADMIN_SELF_MODIFICATION_DENIED",
                username=result.actor_username,
                details="attempted to change own enabled state",
            )
            return False, "Cannot modify your own account."

        # Target hospital verification (R7)
        err = self._verify_target_hospital(result.actor_hospital_id,
                                           target_username,
                                           "ADMIN_USER_ENABLE_CROSS_HOSPITAL_DENIED")
        if err:
            return False, err

        ok, msg = self._db.set_user_enabled(target_username, enabled)
        if ok:
            action = "ENABLED" if enabled else "DISABLED"
            self._db.audit(
                f"ADMIN_USER_{action}",
                username=result.actor_username,
                details=f"target={target_username}",
            )
        return ok, msg

    def set_user_role(self, token: str, target_username: str,
                      new_role: str) -> Tuple[bool, str]:
        """Change the role of a hospital user.

        Guards:
          - Target must belong to actor's hospital (R7).
          - Actor cannot change own role (R8 / Phase 3 guard).
          - new_role must be in HOSPITAL_ADMIN_ALLOWED_ROLES (R9).

        Returns (True, '') or (False, error_message).
        """
        result = self._authz.authorize(
            token, PERM_ROLE_MANAGE, target_username=target_username)
        if not result.allowed:
            self._audit_denial("ADMIN_ROLE_CHANGE_DENIED",
                               result,
                               f"target={target_username}, new_role={new_role}")
            return False, result.denial_reason

        # Role escalation guard (R9)
        if new_role not in HOSPITAL_ADMIN_ALLOWED_ROLES:
            self._db.audit(
                "ADMIN_ROLE_ESCALATION_DENIED",
                username=result.actor_username,
                details=(
                    f"attempted new_role={new_role}, target={target_username}"
                ),
            )
            log.warning(
                f"[ADMIN_SVC] Role escalation denied: "
                f"{result.actor_username} → assign role={new_role} to {target_username}"
            )
            return False, (
                f"Hospital Admin cannot assign role '{new_role}'. "
                f"Allowed roles: {sorted(HOSPITAL_ADMIN_ALLOWED_ROLES)}"
            )

        # Target hospital verification (R7)
        err = self._verify_target_hospital(result.actor_hospital_id,
                                           target_username,
                                           "ADMIN_ROLE_CHANGE_CROSS_HOSPITAL_DENIED")
        if err:
            return False, err

        ok, msg = self._db.set_user_role(target_username, new_role)
        if ok:
            self._db.audit(
                "ADMIN_USER_ROLE_CHANGED",
                username=result.actor_username,
                details=f"target={target_username}, new_role={new_role}",
            )
        return ok, msg

    # ─── Department Management ────────────────────────────────────

    def list_departments(self, token: str) -> Tuple[bool, object]:
        """Return departments for the authenticated admin's hospital.

        Returns (True, list[dict]) or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_USER_MANAGE)
        if not result.allowed:
            return False, result.denial_reason

        hospital_id = result.actor_hospital_id
        if not hospital_id:
            return False, "Authenticated user has no assigned hospital."

        depts = self._db.get_departments_for_hospital(hospital_id)
        return True, depts

    def create_department(self, token: str, name: str,
                          code: str) -> Tuple[bool, str]:
        """Create a department in the authenticated admin's hospital.

        Returns (True, dept_id_str) or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_USER_MANAGE)
        if not result.allowed:
            self._audit_denial("ADMIN_DEPT_CREATE_DENIED",
                               result, f"name={name}, code={code}")
            return False, result.denial_reason

        hospital_id = result.actor_hospital_id
        if not hospital_id:
            return False, "Authenticated user has no assigned hospital."

        ok, val = self._db.create_department(hospital_id, name, code)
        if ok:
            self._db.audit(
                "ADMIN_DEPARTMENT_CREATED",
                username=result.actor_username,
                details=f"name={name}, code={code}, hospital_id={hospital_id}",
            )
        return ok, val

    # ─── Audit Logs (Hospital-Scoped) ─────────────────────────────

    def get_hospital_audit_logs(self, token: str,
                                limit: int = 100) -> Tuple[bool, object]:
        """Return audit log entries scoped to the authenticated admin's hospital.

        Returns (True, list[dict]) or (False, error_message).
        """
        result = self._authz.authorize(token, PERM_AUDIT_ACCESS)
        if not result.allowed:
            self._audit_denial("ADMIN_AUDIT_ACCESS_DENIED", result)
            return False, result.denial_reason

        hospital_id = result.actor_hospital_id
        if not hospital_id:
            return False, "Authenticated user has no assigned hospital."

        logs = self._db.get_audit_logs_for_hospital(hospital_id, limit)
        return True, logs

    # ─── Internal ─────────────────────────────────────────────────

    def _verify_target_hospital(self, actor_hospital_id: Optional[str],
                                 target_username: str,
                                 denial_event: str) -> Optional[str]:
        """Verify target user belongs to actor's hospital.

        Returns None if verification passes, or an error string on failure.
        Hospital ID is NEVER accepted from caller — always from actor session.
        """
        target_hospital = self._db.get_user_hospital(target_username)
        if target_hospital != actor_hospital_id:
            self._db.audit(
                denial_event,
                details=(
                    f"target={target_username}, "
                    f"target_hospital={target_hospital}, "
                    f"actor_hospital={actor_hospital_id}"
                ),
            )
            log.warning(
                f"[ADMIN_SVC] Cross-hospital operation denied: "
                f"actor_hospital={actor_hospital_id}, "
                f"target={target_username}, target_hospital={target_hospital}"
            )
            return (
                "Operation denied: target user does not belong to your hospital."
            )
        return None

    def _audit_denial(self, event_type: str, result, details: str = ""):
        self._db.audit(
            event_type,
            username=result.actor_username or None,
            details=details or result.denial_reason,
        )
