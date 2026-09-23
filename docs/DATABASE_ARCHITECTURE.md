# Aether Surgical Console — Database Architecture
**Rev 4.2 — Phase 2: Multi-Hospital Data Architecture**  
**Status:** Implemented  
**Migration Version:** M001

---

## Overview

The Aether Surgical Console database is a self-contained SQLite 3 file stored at `data/aether.db`. It serves as the single source of truth for all security-relevant state: user credentials, roles, sessions, device identities, topic access control, and audit logs.

Phase 2 introduces a **multi-hospital organizational model** layered on top of the Phase 1 single-workstation foundation. The change is **additive and non-destructive** — existing data is preserved in place.

---

## Organizational Hierarchy

```
AETHER APPLICATION
    │
    ├── app_admin           scope = APPLICATION
    │                       hospital_id = NULL
    │
    └── HOSPITAL
          │
          ├── hospital_admin    scope = HOSPITAL
          ├── technician        scope = HOSPITAL
          ├── surgeon           scope = HOSPITAL
          ├── doctor            scope = HOSPITAL
          └── observer          scope = HOSPITAL
```

**Scope semantics:**
| Scope | Meaning |
| :--- | :--- |
| `APPLICATION` | Full cross-hospital visibility; `hospital_id = NULL` |
| `HOSPITAL` | Scoped to exactly one hospital; `hospital_id = <uuid>` |
| `DEVICE` | Machine identity (data generators, robots); no hospital |

---

## Schema — Entity Relationship

```
hospitals ──┬──< departments
            │
            ├──< users >──── roles
            │       │
            │       └──< sessions >──── devices
            │
            └──< sessions

roles ───────────── topic_acls

users ───────────── audit_logs (username FK, not enforced)
```

---

## Tables

### `hospitals`
Organizational unit. Each hospital is a tenant boundary.

```sql
CREATE TABLE hospitals (
    id          TEXT    PRIMARY KEY,        -- UUID4 (immutable)
    code        TEXT    NOT NULL UNIQUE,    -- Short code ≤12 chars, e.g. 'APOLLO'
    name        TEXT    NOT NULL,           -- Full name e.g. 'Apollo Hospitals Chennai'
    address     TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL            -- UTC ISO 8601
);
```

**Notes:**
- `code` is the source of the first 2 characters of generated `user_id` values.
- `id` (UUID4) is the FK referenced by `users.hospital_id` and `sessions.hospital_id`.
- `is_active = 0` is soft-delete; active hospitals: `WHERE is_active = 1`.

---

### `departments`
Optional sub-units within a hospital.

```sql
CREATE TABLE departments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hospital_id TEXT    NOT NULL REFERENCES hospitals(id),
    name        TEXT    NOT NULL,
    code        TEXT    NOT NULL,           -- e.g. 'SURG', 'CARD', 'ORTH'
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    UNIQUE(hospital_id, code)              -- Code unique per hospital
);
```

---

### `roles`
All roles in the system with their organizational scope.

```sql
CREATE TABLE roles (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT    NOT NULL UNIQUE,
    scope   TEXT    NOT NULL DEFAULT 'HOSPITAL'  -- Added M001
);
```

**Seeded roles (full set after M001):**

| `id` | `name` | `scope` | Description |
| :--- | :--- | :--- | :--- |
| 1 | `admin` | `APPLICATION` | Legacy admin (preserved) |
| 2 | `user` | `HOSPITAL` | Legacy user / surgeon (preserved) |
| 3 | `data_generator` | `DEVICE` | Data stream device (preserved) |
| 4 | `app_admin` | `APPLICATION` | Application-level administrator |
| 5 | `hospital_admin` | `HOSPITAL` | Administrator of one hospital |
| 6 | `technician` | `HOSPITAL` | Hospital operational administrator |
| 7 | `surgeon` | `HOSPITAL` | Operating surgeon |
| 8 | `doctor` | `HOSPITAL` | Physician / consultant |
| 9 | `observer` | `HOSPITAL` | Read-only observer |

> Roles 1–3 are retained for full backward compatibility. All existing code referencing `role='admin'` continues to work.

---

### `users`
User identity and credentials. Extended in Phase 2 with multi-hospital profile fields.

```sql
CREATE TABLE users (
    -- Core identity (Phase 1)
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    username                TEXT    NOT NULL UNIQUE,       -- Login name (lower-case)
    password_hash           TEXT    NOT NULL,              -- bcrypt cost-12 hash
    role_id                 INTEGER NOT NULL REFERENCES roles(id),
    is_enabled              INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT    NOT NULL,
    last_login_at           TEXT,

    -- Immutable multi-hospital identity (Phase 2 / M001)
    uuid                    TEXT    UNIQUE,                -- UUID4 (never changes)
    user_id                 TEXT    UNIQUE,                -- Human-readable, see formula

    -- Organizational scope
    hospital_id             TEXT    REFERENCES hospitals(id),  -- NULL = APPLICATION scope

    -- Profile fields
    first_name              TEXT,
    last_name               TEXT,
    employee_id             TEXT,                          -- HR / badge number
    department_id           INTEGER REFERENCES departments(id),

    -- Account lifecycle
    failed_login_attempts   INTEGER NOT NULL DEFAULT 0,
    locked_until            TEXT,                          -- NULL = not locked
    force_password_change   INTEGER NOT NULL DEFAULT 0,
    password_changed_at     TEXT
);
```

**Key design decisions:**

| Field | Rationale |
| :--- | :--- |
| `uuid` | Immutable internal identity. Never used in API calls. Cannot be changed even if `username` changes. |
| `user_id` | Human-readable (badge-style). See formula below. Used in displays and audit logs. |
| `hospital_id = NULL` | **Application scope** — this is the correct state for `app_admin`. There is no "SYSTEM" sentinel hospital. |
| `id` (integer) | Internal PK for SQL joins only. Never exposed externally. |

---

### `user_id` Generation Formula

```
user_id = HOSPITAL_CODE[:2].upper()
        + FIRST_NAME[:2].upper()
        + EMPLOYEE_ID[:2].upper()
```

| Input | Contribution | Example |
| :--- | :--- | :--- |
| Hospital: `Apollo Hospitals` (code: `APOLLO`) | First 2 alpha chars: `AP` | `AP` |
| First name: `Karthik` | First 2 alpha chars: `KA` | `KA` |
| Employee ID: `47291` | First 2 alphanumeric chars: `47` | `47` |
| **Result** | | **`APKA47`** |

**Collision handling — dash suffix (never extended employee ID):**
```
Base:        APKA47
1st collision: APKA47-01
2nd collision: APKA47-02
...
99th:          APKA47-99
```

> The employee-ID portion is **never extended** (e.g. `APKA472` would be confusing — it could look like a different employee). The base is always preserved; only a dash-numeric suffix is appended.

**Special case — pre-migration / application-scope users:**
Users created before Phase 2 (or app_admin users with no hospital/name data) receive a `SYS`-prefixed ID:
```
username[:2].upper() → admin → SYSAD01
```

---

### `sessions`

```sql
CREATE TABLE sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    token               TEXT    NOT NULL UNIQUE,   -- secrets.token_urlsafe(32)
    user_id             INTEGER NOT NULL REFERENCES users(id),
    device_id           INTEGER REFERENCES devices(id),
    created_at          TEXT    NOT NULL,
    expires_at          TEXT    NOT NULL,           -- 8 hours after created_at
    is_active           INTEGER NOT NULL DEFAULT 1,

    -- Phase 2 additions (M001)
    hospital_id         TEXT    REFERENCES hospitals(id),
    last_activity_at    TEXT                        -- For future inactivity timeout
);
```

---

### `devices`

```sql
CREATE TABLE devices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id           TEXT    NOT NULL UNIQUE,         -- e.g. 'surgeon_console'
    device_type         TEXT    NOT NULL,                -- e.g. 'surgeon', 'robot'
    cert_fingerprint    TEXT    NOT NULL UNIQUE,         -- SHA-256 of DER cert
    is_enabled          INTEGER NOT NULL DEFAULT 1,
    registered_at       TEXT    NOT NULL,
    last_seen_at        TEXT
);
```

---

### `topic_acls`

```sql
CREATE TABLE topic_acls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    role            TEXT    NOT NULL,
    topic           TEXT    NOT NULL,
    can_publish     INTEGER NOT NULL DEFAULT 0,
    can_subscribe   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(role, topic)
);
```

Default deny: if a `(role, topic)` pair has no row, both `can_publish` and `can_subscribe` are `False`.

---

### `audit_logs`

```sql
CREATE TABLE audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,   -- UTC ISO 8601
    event_type  TEXT    NOT NULL,   -- e.g. LOGIN_SUCCESS, USER_CREATED
    username    TEXT,
    device_id   TEXT,
    details     TEXT,
    ip_address  TEXT
);
```

Audit log rows are never deleted. Failures to write are counted in `AetherDatabase.audit_health` and logged, but do not crash the application.

---

## Indexes

Created by migration M001:

| Index | Table | Column(s) | Notes |
| :--- | :--- | :--- | :--- |
| `idx_users_uuid` | `users` | `uuid` | **UNIQUE** — enforces immutability |
| `idx_users_user_id` | `users` | `user_id` | **UNIQUE** — badge ID uniqueness |
| `idx_users_hospital` | `users` | `hospital_id` | Lookup for hospital scoping |
| `idx_sessions_hospital` | `sessions` | `hospital_id` | Session scoping queries |
| `idx_dept_hospital` | `departments` | `hospital_id` | Department lookups |

> SQLite cannot add `UNIQUE` constraints via `ALTER TABLE ADD COLUMN`. Uniqueness for `uuid` and `user_id` is enforced through `UNIQUE INDEX`, which is equivalent in effect.

---

## Migration Engine

The migration system is implemented in [`shared_networking/migration.py`](file:///c:/Users/ReddyKrishnaSriKarth/Desktop/Surgeon%20Console%20UI/shared_networking/migration.py).

### Version tracking
Uses `PRAGMA user_version` — SQLite's built-in schema version integer (starts at 0).

### Migration log

| Version | Name | Changes |
| :--- | :--- | :--- |
| 0 | *(baseline)* | Phase 1 schema: roles, users, devices, sessions, topic_acls, audit_logs |
| 1 | `M001_multi_hospital_foundation` | `hospitals`, `departments` tables; extended `users` and `sessions` columns; role `scope`; 6 new roles; user backfill; indexes |

### Idempotency guarantee
Every migration step checks before acting:
- `ALTER TABLE ADD COLUMN` → guarded by `PRAGMA table_info` check
- `INSERT role` → uses `INSERT OR IGNORE`
- Index creation → uses `CREATE INDEX IF NOT EXISTS`
- User backfill → `WHERE uuid IS NULL`

Running `AetherDatabase.open()` multiple times on the same database is safe.

### Rollback on failure
The entire M001 batch runs in a single transaction. If any step raises an exception, `conn.rollback()` is called. The migration failure is logged at `ERROR` level; the application continues using the pre-migration schema.

---

## Security Properties Maintained

| Property | Status |
| :--- | :--- |
| Passwords stored as bcrypt cost-12 hashes | ✅ Unchanged |
| Plaintext passwords never logged | ✅ Unchanged |
| Session tokens: `secrets.token_urlsafe(32)` | ✅ Unchanged |
| Thread-local WAL-mode connections | ✅ Unchanged |
| `PRAGMA integrity_check` on open | ✅ Unchanged |
| Automated timestamped backups (60 min) | ✅ Unchanged |

### Security fixes applied in Phase 2

| ID | Fix |
| :--- | :--- |
| **CRIT-SEC-01** | `validate_session()` now enforces `AND u.is_enabled = 1` in both query branches. Disabling a user account immediately revokes all active session tokens. |
| **HIGH-SEC-03** | `change_password()` now executes `UPDATE sessions SET is_active = 0` for all active sessions belonging to the user after a successful password update. |

---

## API Reference

### Hospital Management
```python
db.create_hospital(name, code, address=None)  -> (bool, hospital_id | error)
db.get_hospital(hospital_id)                  -> dict | None
db.get_hospital_by_code(code)                 -> dict | None
db.list_hospitals()                           -> list[dict]
```

### Extended User Management
```python
db.create_full_user(
    username, password, role,
    hospital_id=None,     # None for app_admin (APPLICATION scope)
    first_name=None,
    last_name=None,
    employee_id=None,
    department_id=None,
) -> (bool, user_id | error)

db.get_users_for_hospital(hospital_id)  -> list[dict]
db.get_user_hospital(username)          -> hospital_id | None
db.get_user_profile(username)           -> dict | None
```

### User ID Generation (standalone, importable)
```python
from shared_networking.migration import generate_user_id_base, make_unique_user_id

base = generate_user_id_base("APOLLO", "Karthik", "47291")  # → "APKA47"
uid  = make_unique_user_id(conn, base)                        # → unique variant
```

---

## Compatibility Notes

### Backward compatibility
- `create_user()`, `verify_user()`, `create_session()`, `validate_session()`, `check_acl()`, `audit()` — all unchanged in signature and return type.
- All 54 Phase 1 tests continue to pass without modification.
- `robot_console`, `observer`, `data_generator` service devices — unaffected.
- Broker handshake, mTLS, protocol framing — unchanged (Phase 3 scope).

### Known limitations (addressed in later phases)
| Gap | Phase |
| :--- | :--- |
| Broker topics not namespaced by `hospital_id` | Phase 3 |
| No UI for hospital / user management | Phase 4 |
| No account lockout enforcement (column added; logic pending) | Phase 4 |
| No inactivity timeout (`last_activity_at` column added; logic pending) | Phase 4 |
| No password recovery | Phase 5 |
| mTLS certificate per-hospital isolation | Phase 3 |
