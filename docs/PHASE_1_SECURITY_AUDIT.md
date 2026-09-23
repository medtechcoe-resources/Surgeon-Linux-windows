# PHASE 1 — SECURITY & AUTHENTICATION AUDIT REPORT
**Aether Surgical Console Rev 4.2**  
**Audit Type:** Read-Only Security Architecture & Authentication Audit  
**Date:** September 2, 2026  
**Status:** COMPLETE (No source code modified)

---

## Executive Summary

This security architecture audit evaluates the authentication, authorization, session management, database persistence, mutual TLS (mTLS), publish-subscribe broker, device identity, and audit logging subsystems of the **Aether Surgical Console Rev 4.2**. 

The current system was engineered as a **100% offline, single-workstation, local-area network (LAN) robotic console** operating strictly within an isolated operating theater. While its baseline cryptographic primitives (TLS 1.3, bcrypt cost 12, CSPRNG tokens, and length-prefixed framing) are well implemented for a standalone workstation, the codebase **lacks any multi-hospital, multi-tenant, organizational scoping, or account administration lifecycle features**. 

Furthermore, several critical security flaws exist in the current implementation:
1. **Disabled accounts continue to validate active sessions** because `validate_session()` omits the `is_enabled` database check.
2. **Device-binding for human operator sessions is bypassed** because the login dialog omits passing `device_id` to `create_session()`.
3. **Sessions survive closing the console application** because no UI triggers an explicit logout, leaving tokens valid in SQLite for up to 8 hours.
4. **The offline Root CA private key is stored unencrypted in plaintext alongside device keys** on the local disk.
5. **There is zero rate limiting or account lockout** against credential brute-forcing.
6. **No concept of `hospital_id`, tenant boundaries, user management UI, or temporary credentials exists.**

---

## Part I: Explicit Answers to Security Questions (A – S)

| Question | Verdict | Technical Findings & Code References |
| :--- | :--- | :--- |
| **A. Can a user launch a console without authenticating?** | **YES (Locally / Partially)** | In [main.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/main.py#L210-L217), [Robot-Console/main.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/Robot-Console/main.py#L60-L65), and [Observer-Screen/main.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/Observer-Screen/main.py#L51-L56), login is enforced strictly in the outer `main()` function via `LoginDialog.exec()`. If a user imports or launches `AetherConsole` or any screen component directly via Python script, the UI opens without credentials. Furthermore, [Data-Generator/main.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/Data-Generator/main.py#L36-L53) is completely headless and launches without any user authentication dialog. While the Pub-Sub Broker will reject unauthenticated connections during handshake, the local UI and local video/YOLO processing can run locally without authenticating. |
| **B. Can a user bypass login by directly launching another screen?** | **YES** | Every screen in `screens/` ([PreopPlanningScreen](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/preop_planning.py), [LiveVideoScreen](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/live_video.py), [LiveControlScreen](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/live_control.py), [SettingsScreen](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/settings.py), [CommCenterScreen](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/comm_center.py)) is an independent PyQt6 `QWidget`. None of these classes verify session tokens or authentication state internally. Instantiating any screen directly in a standalone Qt runner renders the full screen without a login prompt. |
| **C. Can a user manipulate their role?** | **NO (Broker) / YES (Local GUI)** | At the broker layer, [PubSubBroker._handle_handshake](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py#L430-L434) completely ignores the client-claimed role in the message envelope and authoritatively extracts the role from the SQLite database via [AetherDatabase.validate_session](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L607-L627). However, in the local GUI, [SettingsScreen._apply_role_restrictions](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/settings.py#L655-L679) relies on the constructor parameter `self._role`. Modifying this in memory unhides the admin UI, but backend operations still fail on the broker and database. |
| **D. Can a user manipulate hospital_id or equivalent scope?** | **N/A (NON-EXISTENT)** | There is **no `hospital_id`**, tenant identifier, or organizational scope in any table schema, network protocol, configuration, or UI. The system has no concept of multi-hospital isolation. |
| **E. Can a technician reset another hospital's user?** | **NO (NON-EXISTENT)** | Password reset functionality **does not exist** anywhere in the system. The only password function is [AetherDatabase.change_password](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L521-L537), which requires knowing the current password and only permits an admin to change their own password. Neither technicians nor administrators can reset other users' passwords. |
| **F. Can an authenticated user access broker topics they shouldn't?** | **NO** | Every publish and subscribe action is strictly evaluated against the `topic_acls` table in SQLite by [PubSubBroker._handle_publish](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py#L487-L497) and [PubSubBroker._handle_subscribe](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py#L544-L554). Unauthorized attempts are blocked and logged as `UNAUTHORIZED_PUBLISH` or `UNAUTHORIZED_SUBSCRIBE`. |
| **G. Can an unauthenticated client publish?** | **NO** | Transport-layer mTLS rejects connections lacking a certificate signed by `Aether Local CA`. Furthermore, [PubSubBroker._handle_publish](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py#L476-L486) checks `if not sender.authenticated:` and denies the action before checking ACLs. |
| **H. Can an unauthenticated client subscribe?** | **NO** | [PubSubBroker._handle_subscribe](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py#L529-L539) and [PubSubBroker._handle_client_list_request](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py#L580-L589) reject and audit any unauthenticated client attempts. |
| **I. Can a disabled user continue using an existing session?** | **YES (CRITICAL BUG)** | While [AetherDatabase.verify_user](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L494-L498) blocks new logins if `is_enabled == 0`, [AetherDatabase.validate_session](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L606-L625) **omits `u.is_enabled = 1`** from its SQL query! Active sessions generated prior to disabling an account remain completely valid until natural 8-hour expiry. In addition, connected broker clients are never evicted when user status changes in the DB. |
| **J. What happens when a password changes?** | **SESSIONS REMAIN ACTIVE** | [AetherDatabase.change_password](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L531-L536) only executes `UPDATE users SET password_hash = ?`. It **does not invalidate existing active sessions** in the `sessions` table. All existing sessions on all devices remain active and usable. |
| **K. What happens when a role changes?** | **CACHED ON BROKER** | In [broker.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py#L432), the user role is retrieved during initial handshake and cached on `ClientInfo.role`. If an admin updates a user's role in the DB, the live connected client retains their prior role and permissions until their TCP socket disconnects. |
| **L. What happens when a user is disabled?** | **INCOMPLETE REVOCATION** | Setting `users.is_enabled = 0` blocks future password verifications. However, existing sessions are NOT invalidated, `validate_session()` does not check `is_enabled`, and active broker connections are NOT terminated. |
| **M. Can sessions survive logout?** | **NO (If explicitly triggered)** | Calling [ConnectionManager.logout](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/connection_manager.py#L281-L296) sends `_logout` to the broker, invoking [AetherDatabase.invalidate_session](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L629-L636) (`is_active = 0`). Subsequent validation fails. **CRITICAL CAVEAT:** No logout button exists anywhere in the UI; closing the console window only triggers `_do_disconnect()`, leaving the session active in SQLite. |
| **N. Are passwords ever stored plaintext?** | **NO** | Passwords are consistently hashed using `bcrypt` with cost factor 12 and random salts via [AetherDatabase._hash_password](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L803-L809). Plaintext passwords are not persisted in SQLite or files. |
| **O. Are recovery credentials stored plaintext?** | **N/A (NON-EXISTENT)** | There are no recovery credentials, recovery keys, or reset questions implemented in the codebase. |
| **P. Are secrets written into logs?** | **NO (Partial Comm Leak)** | Passwords, private keys, and full session tokens are not logged in `aether.log` or `audit_logs` (only 8-character token prefixes are logged). However, [screens/comm_center.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/comm_center.py#L358-L372) renders raw JSON message payloads on screen, which could leak clinical patient data. |
| **Q. Are private keys protected?** | **NO (CRITICAL FLAW)** | All RSA private keys (`aether_ca.key`, `broker.key`, `surgeon_console.key`, etc.) are saved as unencrypted PEM files (`serialization.NoEncryption()`) in `data/certs/` via [TLSManager._save_key](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/tls.py#L388-L406). On Windows, [TLSManager.check_key_permissions](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/tls.py#L408-L422) is bypassed (`if os.name == 'nt': return`). The Root CA key is co-located on disk with device keys. |
| **R. Are there default/admin credentials?** | **NO (Hardcoded Defaults Absent)** | Provisioning via [provisioning._create_admin_account](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/provisioning.py#L129-L174) requires the administrator to enter an interactive password (min 8 chars). There are no pre-seeded default credentials in the DB. (Static dev tokens like `"datagen-001"` exist only in the test generator). |
| **S. Is there any hidden/backdoor account?** | **NO** | Database queries confirm only provisioned users exist in `users`. No backdoor users exist. However, service devices (`data_generator`, `robot_console`, `observer_screen`) bypass user accounts entirely based on certificate device type. |

---

## Part II: Detailed Evaluation of the 29 Security Architecture Areas

### 1. User Accounts
- **Storage:** Stored in the SQLite `users` table ([database.py:170-178](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L170-L178)) with columns: `id`, `username`, `password_hash`, `role_id`, `is_enabled`, `created_at`, `last_login_at`.
- **Normalization:** Usernames are converted to lowercase and stripped of whitespace (`username.strip().lower()`).
- **Account Creation:** The only implementation is [AetherDatabase.create_user](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L433-L471). There is **no administrative GUI or API** to create, list, edit, or delete users post-provisioning.
- **Account Disabling:** The `is_enabled` column exists (default `1`), but there are no UI controls or helper methods to toggle it.

### 2. User IDs
- Integer autoincrement primary key (`id` in `users`).
- Referenced as foreign key `user_id` in `sessions` table.
- Internal database IDs are not exposed over the broker wire protocol; only `username` and `session_id` are transmitted.

### 3. Password Hashing
- Algorithm: `bcrypt` via the Python `bcrypt` library ([database.py:803-809](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L803-L809)).
- Cost factor: 12 rounds (`bcrypt.gensalt(rounds=12)`).
- Hash format: `$2b$12$...` stored as UTF-8 text.
- Minimum password length: 8 characters enforced in `database.py` and `provisioning.py`.

### 4. Login Process
- GUI: [LoginDialog](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/login_dialog.py#L61) modal dialog.
- Verification: [AuthManager.verify](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/authentication.py#L46) delegates to [AetherDatabase.verify_user](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L472).
- Validates user existence, verifies `is_enabled == 1`, runs `bcrypt.checkpw()`.
- On success: updates `last_login_at`, writes `LOGIN_SUCCESS` to `audit_logs`, generates session token via `AuthManager.create_session()`.

### 5. Logout Process
- Implementation: [ConnectionManager.logout](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/connection_manager.py#L281) creates and sends a `_logout` message (`CTRL_LOGOUT`) containing `session_id`.
- Broker Handling: [PubSubBroker._handle_logout](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py#L360) marks the session inactive in SQLite (`is_active = 0`), audits `USER_LOGOUT`, and terminates the client socket.
- **Vulnerability:** No UI screen (Header, Navigation, or Settings) has a Logout button. Users simply close the application, which triggers `cleanup()` / `_do_disconnect()`. The session remains active in the DB.

### 6. Sessions
- Tokens: 32 bytes of cryptographically secure random bytes formatted as URL-safe base64 strings (`secrets.token_urlsafe(32)`, yielding ~43 characters).
- Storage: Stored in SQLite `sessions` table ([database.py:190-198](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L190-L198)): `id`, `token`, `user_id`, `device_id`, `created_at`, `expires_at`, `is_active`.
- Validation: [AetherDatabase.validate_session](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L583) verifies `token`, `is_active = 1`, and `expires_at > now`.

### 7. Session Expiration
- Fixed duration: 8 hours (`SESSION_LIFETIME_HOURS = 8` in [database.py:39](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L39)).
- Expiration check: Lexicographical comparison of ISO 8601 UTC timestamps against `datetime.now(timezone.utc).isoformat()`.
- Garbage collection: [AetherDatabase.cleanup_expired_sessions](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L638) is executed periodically by the broker's heartbeat monitor thread.
- Missing feature: **No idle/inactivity timeout**. A workstation left unattended remains authenticated for the full 8-hour window.

### 8. Role-Based Access Control (RBAC)
- Defined roles: `admin`, `user`, `data_generator` in the `roles` table.
- Additional mapped service roles in broker: `robot_console`, `observer` ([database.py:350-410](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L350-L410)).
- Authoritative assignment: Broker queries `roles` via `sessions.user_id` foreign key. Client-provided roles in handshake payloads are overridden.

### 9. Permissions
- Enforced at the topic level using the `topic_acls` table: `role`, `topic`, `can_publish`, `can_subscribe`.
- Principle of Default Deny: If an entry is missing in `topic_acls`, `check_acl()` returns `False`.

### 10. Hospital / Tenant Concepts
- **STATUS: COMPLETELY ABSENT (NOT IMPLEMENTED)**
- No tables, columns, configuration parameters, or protocol fields exist for multi-tenancy.
- All consoles, databases, certificates, and broker topics assume a single flat installation.

### 11. Device Identity
- Defined in `devices` table: `id`, `device_id`, `device_type`, `cert_fingerprint`, `is_enabled`, `registered_at`, `last_seen_at`.
- Registered devices: `surgeon_console`, `robot_console`, `observer_screen`, `data_generator`.
- Device type is encoded in certificate Subject OU; device ID is encoded in Common Name (CN).
- Broker verifies incoming TLS socket fingerprint against `devices.cert_fingerprint`.

### 12. Mutual TLS (mTLS)
- Managed by [TLSManager](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/tls.py#L39).
- Version: Strictly TLS 1.3 (`ctx.minimum_version = ssl.TLSVersion.TLSv1_3`).
- CA: Local self-signed root CA (`aether_ca.crt`, 10-year validity).
- Certificates: RSA 2048-bit keys signed with SHA-256 (5-year validity).
- Verification: Server requires client cert (`CERT_REQUIRED`), client verifies broker cert against CA.

### 13. Broker Authentication
- Two-tier authentication:
  1. **Transport Layer:** Client certificate must be signed by the CA and its SHA-256 fingerprint registered in `devices`.
  2. **Application Layer:** During `_handshake`, service devices authenticate via certificate identity; human operators authenticate via `session_id` token validated against SQLite.

### 14. Broker Authorization
- Checked dynamically on every incoming data message ([broker.py:487](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py#L487)) and subscription request ([broker.py:544](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py#L544)).
- Failed authorizations are rejected and logged to the audit table.

### 15. Topic ACLs
Current matrix in SQLite `topic_acls`:
- `admin`: Publish & Subscribe on all 12 topics.
- `user`: Subscribe to vitals, telemetry, alerts, status, video; Publish ONLY to `system_control`.
- `data_generator`: Publish ONLY to `patient_vitals`, `robot_telemetry`, `alerts`, `connection_status`.
- `robot_console`: Publish to `connection_status`, `video_broadcast`; Subscribe to telemetry, vitals, alerts, `robot_commands`, `system_control`.
- `observer`: Subscribe ONLY to telemetry, vitals, alerts, frames, detection, logs, status.

### 16. Audit Logging
- SQLite table `audit_logs` ([database.py:209-217](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L209-L217)).
- Captures `timestamp`, `event_type`, `username`, `device_id`, `details`, `ip_address`.
- Resilient: Write failures increment `_audit_failures` and log errors without crashing real-time threads.
- Export: Logs can be exported via Settings UI ([settings.py:529](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/settings.py#L529)).

### 17. Password Recovery
- **STATUS: NOT IMPLEMENTED**
- No forgot-password workflow, recovery token, email dispatcher, or secondary factor exists.

### 18. Temporary Passwords
- **STATUS: NOT IMPLEMENTED**
- Passwords do not expire; no forced password change on first login.

### 19. Account Lockout
- **STATUS: NOT IMPLEMENTED**
- No failure counter or account lock logic exists. Failed logins return `(False, "")` and record an audit log, but do not rate-limit or lock the user.

### 20. Failed Login Handling
- Handled in [AetherDatabase.verify_user](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L472-L520).
- Emits `LOGIN_FAILED` audit event with details (`unknown user`, `account disabled`, or `wrong password`).
- UI displays a generic red error label: "Invalid username or password."

### 21. Database Protection
- SQLite file `data/aether.db` is an unencrypted standard SQLite database file.
- Uses WAL journal mode and executes `PRAGMA integrity_check` on startup.
- Automated timestamped backups created every 60 minutes in `data/backups/`.
- **Gap:** No encryption at rest. Anyone with physical or filesystem access can read the database directly.

### 22. Certificate & Private Key Protection
- RSA private keys are stored in `data/certs/` without encryption (`serialization.NoEncryption()`).
- File permissions check (`0o600`) in [tls.py:408](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/tls.py#L408) is completely bypassed on Windows.
- The Root CA key (`aether_ca.key`) resides directly in the same directory as device keys.

### 23. Default Credentials
- No hardcoded administrative credentials exist in code or DB seed.
- First-time setup forces the operator to create an admin account via CLI prompt.

### 24. Hardcoded Credentials
- No user passwords are hardcoded in application files.
- Static tokens and names: `"aether_datagen"` and token `"datagen-001"` in [Data-Generator/publisher.py:63-69](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/Data-Generator/publisher.py#L63-L69).

### 25. Privilege Escalation Risks
- A regular user cannot escalate privileges through broker protocol tampering because the broker queries the role authoritatively from SQLite.
- Risk: If an attacker obtains local administrative file write access, they can modify `data/aether.db` to change `users.role_id` or alter `topic_acls`.

### 26. Cross-User Access Risks
- Sessions without device binding: Because [login_dialog.py:228](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/login_dialog.py#L228) passes `device_id=None` to `create_session()`, `device_id` is stored as `NULL`.
- In [database.py:613](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L613), the query contains `(d.device_id = ? OR s.device_id IS NULL)`. Thus, any device presenting that token is accepted.
- If user session tokens are intercepted, cross-user impersonation is possible.

### 27. Cross-Device Access Risks
- Service devices (`data_generator`, `robot_console`, `observer_screen`) are trusted implicitly once their certificate fingerprint matches.
- If a client certificate and private key are copied to another workstation, that workstation can masquerade as the device.

### 28. Cross-Hospital Access Risks
- **FATAL GAP:** The system has no tenant or hospital isolation. If two hospital local networks were connected or shared a database, any valid certificate or user account would possess global access.

### 29. Authentication Bypass Possibilities
- Direct instantiation of GUI screen widgets (`screens/*`) bypasses login entirely.
- In [video_broadcaster.py:213](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/Robot-Console/services/video_broadcaster.py#L213) and [video_stream.py:108](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/video_stream.py#L108), fallback code exists that reverts to unencrypted raw TCP if TLS fails to initialize.

---

## Part III: Architectural Analysis & Data Flows

### 1. Current Architecture Overview
The current system architecture is an offline, multi-process distributed desktop suite communicating via local TCP sockets:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Surgeon Console UI (main.py)                       │
│  - LoginDialog (AuthManager)                                            │
│  - ConnectionManager (mTLS Client, TCP Port 5000)                       │
│  - VideoReceiver (mTLS Server, TCP Port 5001)                           │
│  - Screens: Planning, Video (YOLO), Control, Settings, Comm Center      │
└──────────────────┬────────────────────────────────▲─────────────────────┘
                   │ TCP 5000 (mTLS)                │ TCP 5001 (mTLS Video)
                   ▼                                │
┌──────────────────────────────────────────────┐    │
│            Pub-Sub Broker (broker.py)        │    │
│  - Strict mTLS Server (TLS 1.3)              │    │
│  - SQLite Database (aether.db)               │    │
│    * users, roles, devices, sessions         │    │
│    * topic_acls, audit_logs                  │    │
│  - RBAC Message Router                       │    │
└────────▲─────────────────────────────▲───────┘    │
         │ TCP 5000 (mTLS)             │ TCP 5000   │
         │                             │ (mTLS)     │
┌────────┴─────────────┐     ┌─────────┴────────────┴─────────────────────┐
│    Data Generator    │     │               Robot Console                │
│ (Data-Generator/)    │     │              (Robot-Console/)              │
│ - Publishes vitals,  │     │ - UI Controls & Telemetry                  │
│   telemetry, alerts  │     │ - VideoBroadcastService (Port 5001 Source) │
└──────────────────────┘     └────────────────────────────────────────────┘
```

### 2. Authentication Flow
```
User -> LoginDialog -> AuthManager.verify(user, pass) -> AetherDatabase.verify_user()
                             │
                             ├─ [Fail] ──> Log LOGIN_FAILED ──> Show error in dialog
                             │
                             └─ [Pass] ──> Log LOGIN_SUCCESS ──> Update last_login_at
                                               │
                                               ▼
                         AuthManager.create_session(user, role)
                                               │
                                               ▼
                      AetherDatabase.create_session() (Token generated)
                                               │
                                               ▼
                      ConnectionManager configured with Token
```

### 3. Session Flow
1. **Creation:** `secrets.token_urlsafe(32)` generates token string; inserted into `sessions` table with UTC `expires_at = now + 8 hours` and `is_active = 1`.
2. **Transmission:** Client includes token in `CTRL_HANDSHAKE` payload to the broker over mTLS.
3. **Broker Validation:** Broker calls `AetherDatabase.validate_session(token)`. Checks `is_active = 1` and `expires_at > now`.
4. **Eviction:** Periodically, `cleanup_expired_sessions()` purges rows where `expires_at < now` or `is_active = 0`.
5. **Vulnerability:** If user closes the window, `cleanup()` disconnects the socket without invalidating the token. The token remains valid in SQLite.

### 4. RBAC Flow
1. **Resolution:** Broker identifies role:
   - For human operators: from `roles.name` joined on `sessions.user_id`.
   - For service devices: from `devices.device_type` mapped to service role.
2. **Enforcement:**
   - On publish: `AetherDatabase.check_acl(sender.role, topic, 'publish')`.
   - On subscribe: `AetherDatabase.check_acl(sender.role, topic, 'subscribe')`.
3. **Rejection:** Unauthorized operations trigger `UNAUTHORIZED_PUBLISH` / `UNAUTHORIZED_SUBSCRIBE` audit events; messages are dropped.

### 5. mTLS Flow
```
Client (ConnectionManager)                           Broker (PubSubBroker)
       │                                                      │
       ├────────────── Raw TCP Connect (Port 5000) ──────────>│
       │                                                      │
       │<───────────── TLS 1.3 Handshake Request ─────────────┤ (Requests client cert)
       │                                                      │
       ├────────────── Presents <device_id>.crt ─────────────>│
       │                                                      ├─ Extracts peer fingerprint
       │                                                      ├─ Queries devices table
       │                                                      └─ [Not found/revoked] -> Close
       │                                                      │
       │<───────────── Presents broker.crt ───────────────────┤
       ├─ Verifies broker.crt against aether_ca.crt           │
       │                                                      │
       ├══════════════ Established Secure mTLS Channel ═══════┤
```

### 6. Broker Authorization Flow
```
Client (Authenticated mTLS Socket)                   Broker (Worker Thread)
       │                                                      │
       ├────────────── Data Message [4-byte len][JSON] ──────>│
       │                                                      ├─ Check: sender.authenticated?
       │                                                      ├─ Check: len <= class_limit?
       │                                                      ├─ Check: topic_acls allow pub?
       │                                                      │    ├─ [No] ─> Audit & Drop
       │                                                      │    └─ [Yes] ─> Route
       │<───────────── Dispatched to Topic Subscribers ───────┤
```

### 7. Database Security Architecture
- Database Engine: SQLite 3 with WAL (Write-Ahead Logging) journal mode.
- Threading Model: Thread-local connections via `threading.local()`, preventing multi-threading corruption.
- Integrity: Runs `PRAGMA integrity_check` on open.
- Backup: Automated timestamped copies in `data/backups/` rotated to maintain 5 copies.
- Vulnerability: Unencrypted SQLite file stored on disk (`data/aether.db`).

### 8. Audit Logging Subsystem
- Immutable table `audit_logs` records security and administrative actions.
- Log fields: `timestamp`, `event_type`, `username`, `device_id`, `details`, `ip_address`.
- Fault tolerant: DB write exceptions do not disrupt real-time message routing.

---

## Part IV: Vulnerability & Gap Classification

### Critical Vulnerabilities (Must Fix Before Multi-Hospital Deployment)

1. **[CRIT-SEC-01] Disabled Account Active Session Retention**
   - **File:** [shared_networking/database.py:607-624](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L607-L624)
   - **Flaw:** `validate_session()` does not verify `u.is_enabled = 1`. A disabled user's active session remains valid until the 8-hour timeout.
   - **Impact:** Administrative revocation of a compromised user account fails to terminate access.

2. **[CRIT-SEC-02] Broken Session Device Binding**
   - **File:** [shared_networking/login_dialog.py:228](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/login_dialog.py#L228) and [shared_networking/database.py:613](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L613)
   - **Flaw:** `LoginDialog` calls `create_session()` without passing `device_id`. The session is saved with `device_id = NULL`. In `validate_session()`, the SQL query allows `s.device_id IS NULL`, allowing the session token to be used from any device.
   - **Impact:** Session tokens are not bound to hardware devices.

3. **[CRIT-SEC-03] Exposure of Offline Root CA Private Key**
   - **File:** [data/certs/aether_ca.key](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/data/certs/aether_ca.key) and [shared_networking/tls.py:398](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/tls.py#L398)
   - **Flaw:** `aether_ca.key` is stored unencrypted in the local application folder. In a multi-hospital deployment, having the Root CA key on client workstations allows anyone to forge certificates for any hospital or device.
   - **Impact:** Total compromise of the PKI trust root.

4. **[CRIT-SEC-04] Complete Absence of Hospital/Tenant Scoping**
   - **Location:** Database schema, broker routing, and packet envelope.
   - **Flaw:** No `hospital_id` or organizational isolation exists.
   - **Impact:** In multi-hospital systems, data and controls would leak across hospital boundaries.

### High-Risk Issues

1. **[HIGH-SEC-01] Missing Account Lockout & Brute-Force Rate Limiting**
   - **Location:** [database.py:472](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L472) and [login_dialog.py:208](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/login_dialog.py#L208)
   - **Flaw:** No limit on consecutive failed login attempts. An attacker can run brute-force dictionary attacks without account locking or progressive delays.

2. **[HIGH-SEC-02] Sessions Survive Console Application Closure**
   - **Location:** [widgets/header.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/widgets/header.py) and [main.py:173](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/main.py#L173)
   - **Flaw:** The UI has no logout button. Closing the window triggers `_do_disconnect()`, which leaves the session active in SQLite for up to 8 hours.

3. **[HIGH-SEC-03] Password Change Does Not Invalidate Active Sessions**
   - **Location:** [database.py:521-537](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L521-L537)
   - **Flaw:** Changing a password does not revoke active sessions, leaving compromised sessions active.

4. **[HIGH-SEC-04] Video Stream Unencrypted Plaintext Fallback**
   - **Location:** [video_broadcaster.py:213-215](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/Robot-Console/services/video_broadcaster.py#L213-L215) and [video_stream.py:108](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/video_stream.py#L108)
   - **Flaw:** Broadcaster catches TLS initialization errors and silently falls back to raw unencrypted TCP socket streaming.

### Medium-Risk Issues

1. **[MED-SEC-01] Unencrypted SQLite Database at Rest**
   - **Location:** `data/aether.db`
   - **Flaw:** Database is standard unencrypted SQLite. User hashes, audit logs, and device fingerprints are readable by any local process.

2. **[MED-SEC-02] No User Management Interface Post-Provisioning**
   - **Location:** `screens/settings.py`
   - **Flaw:** Once initial provisioning completes, there is no UI to add, remove, disable, or audit users.

3. **[MED-SEC-03] Fixed 8-Hour Session With No Inactivity Timeout**
   - **Location:** [database.py:39](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py#L39)
   - **Flaw:** Workstations in surgical theaters left unattended remain unlocked indefinitely until 8 hours elapse.

4. **[MED-SEC-04] Windows File Permission Check Bypass**
   - **Location:** [tls.py:410](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/tls.py#L410)
   - **Flaw:** `check_key_permissions()` exits immediately on Windows without checking Windows DACLs.

### Low-Risk Issues

1. **[LOW-SEC-01] Stale Password Length Text in Settings UI**
   - **Location:** [screens/settings.py:290](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/settings.py#L290) placeholder displays "min 6 chars" while backend enforces 8 chars.
2. **[LOW-SEC-02] Hardcoded Localhost Binding Defaults**
   - **Location:** [config.py:17](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/config.py#L17) defaults to `127.0.0.1`.
3. **[LOW-SEC-03] Comm Center Displays Plaintext Message Payloads**
   - **Location:** [screens/comm_center.py:350](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/comm_center.py#L350) renders live packet payloads in the GUI.

---

## Part V: Recommended Multi-Hospital Architecture

To transform Aether Console into a secure multi-hospital system, the following architecture must be implemented in future phases:

### 1. Multi-Tenant Database Schema Evolution
```sql
CREATE TABLE hospitals (
    id          TEXT PRIMARY KEY,  -- e.g. 'HOSP-MAYO-01'
    name        TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

-- Extend users table:
ALTER TABLE users ADD COLUMN hospital_id TEXT REFERENCES hospitals(id);
ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN locked_until TEXT;
ALTER TABLE users ADD COLUMN force_password_change INTEGER NOT NULL DEFAULT 0;

-- Extend devices table:
ALTER TABLE devices ADD COLUMN hospital_id TEXT REFERENCES hospitals(id);

-- Extend sessions table:
ALTER TABLE sessions ADD COLUMN hospital_id TEXT REFERENCES hospitals(id);
ALTER TABLE sessions ADD COLUMN last_activity_at TEXT;
```

### 2. Multi-Hospital Scoped PKI
- **Central Offline Root CA:** Stored securely offline (never distributed on workstations).
- **Intermediate CA per Hospital:** Each hospital receives an Intermediate CA certificate (`hospital_A_ca.crt`) that signs its local device and broker certificates.
- **Subject Alternative Names & Extensions:** Certificate Subject includes `hospital_id` in Organizational Unit (OU) or custom X.509 extension.

### 3. Topic Scoping with Hospital Namespace
Broker topics must be namespaced by hospital:
- Standard: `hospital/{hospital_id}/{topic_name}` (e.g. `hospital/HOSP-01/robot_telemetry`)
- Broker enforces that clients can only publish and subscribe to topics prefixed with their authenticated `hospital_id`.

### 4. Session & Lifecycle Hardening
- Enforce `u.is_enabled = 1` in `validate_session()`.
- Add sliding inactivity timeout (e.g. 15 minutes of silence locks the console).
- Invalidate all active sessions when a password is changed or an account is disabled.
- Add an explicit **LOGOUT** button to the UI header and call `ConnectionManager.logout()`.

---

## Part VI: File Impact Matrix

### Files That Will Need Modification (Phase 2+)

| File | Proposed Modifications |
| :--- | :--- |
| [shared_networking/database.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/database.py) | Add `hospitals` table; add `hospital_id`, `failed_attempts`, `locked_until`, `force_password_change` to `users`; check `u.is_enabled = 1` in `validate_session`; add account lockout and reset routines. |
| [shared_networking/authentication.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/authentication.py) | Pass `hospital_id` and `device_id` into `verify()`, `create_session()`, and `validate_session()`. |
| [shared_networking/broker.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/broker.py) | Enforce hospital namespace scoping on topics (`hospital/{hospital_id}/{topic}`); disallow cross-hospital routing; evict connected clients when revoked. |
| [shared_networking/connection_manager.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/connection_manager.py) | Send `hospital_id` in handshake; handle session timeout and lockout control signals. |
| [shared_networking/login_dialog.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/login_dialog.py) | Pass `device_id` and selected `hospital_id` into `create_session()`; show lockout and password change prompts. |
| [shared_networking/tls.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/tls.py) | Remove plaintext root CA key storage from client deployments; support Intermediate CA validation and hospital ID extension checks. |
| [shared_networking/provisioning.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/provisioning.py) | Support provisioning hospitals, hospital-specific administrators, and intermediate certificates. |
| [shared_networking/video_stream.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/video_stream.py) | Remove raw TCP fallback; enforce strict hospital-scoped certificate verification. |
| [Robot-Console/services/video_broadcaster.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/Robot-Console/services/video_broadcaster.py) | Remove raw TCP fallback; fail closed if TLS cannot be established. |
| [widgets/header.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/widgets/header.py) | Add a prominent, styled **LOGOUT** button that calls `ConnectionManager.logout()`. |
| [screens/settings.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/screens/settings.py) | Add User Management panel (create user, disable user, assign role, view audit trail); fix min password length placeholder. |
| [main.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/main.py) | Ensure window close triggers clean logout; handle session expiration signals. |

### Files That Should NOT Be Modified

| File | Rationale |
| :--- | :--- |
| [shared_networking/protocol.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/protocol.py) | The 4-byte big-endian framing and JSON serialization are solid and thoroughly tested. Topic names can be parameterized without altering the low-level wire framing. |
| [shared_networking/message_types.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/message_types.py) | Base message topic definitions are standardized across consoles and generators. |
| [shared_networking/logger.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/logger.py) | Rotating file handler and category logger are stable and performant. |
| [theme_manager.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/theme_manager.py) | Theme switching and palette manager are non-security UI components. |
| [models/patient_vitals_model.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/models/patient_vitals_model.py) | Clinical vitals state machine and watchdog logic should remain intact. |
| [widgets/estop.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/widgets/estop.py) | Emergency Stop hardware/UI logic must remain strictly isolated from authentication concerns. |
| [yolo_pipeline.py](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/yolo_pipeline.py) | Computer vision inference engine is orthogonal to multi-hospital access control. |

---

## Part VII: Migration Risks & Testing Strategy

### Migration Risks
1. **Backward Compatibility of Existing Database:** Updating schema (`hospitals`, new columns on `users` and `sessions`) requires automated migration logic (`ALTER TABLE`) that preserves existing admin credentials in `data/aether.db`.
2. **PKI Transition:** Distributing Intermediate CAs without invalidating existing operational consoles requires a planned CA migration window.
3. **Topic Renaming Deadlock:** If broker topics transition to `hospital/{id}/{topic}` while legacy components publish to flat topic names, telemetry will be silently dropped unless topic translation adapters are implemented.

### Testing Strategy
1. **Automated Unit Tests:**
   - Verify account lockout after $N$ failed attempts.
   - Verify active session revocation when an account is disabled.
   - Verify active session revocation when a password is changed.
   - Verify `validate_session()` fails if `device_id` mismatches.
2. **Multi-Tenant Isolation Tests:**
   - Client from Hospital A attempting to publish to Hospital B topic must receive `UNAUTHORIZED_PUBLISH`.
   - Client from Hospital A attempting to subscribe to Hospital B must receive `UNAUTHORIZED_SUBSCRIBE`.
   - Technician in Hospital A attempting to reset Hospital B user must be rejected.
3. **Session Expiry & Inactivity Tests:**
   - Validate that idle sessions expire after configured timeout.
   - Verify that UI Logout clears session token from SQLite.

---
*Report compiled autonomously by Antigravity Agentic Security Audit Subsystem.*
