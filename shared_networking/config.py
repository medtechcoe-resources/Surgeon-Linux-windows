# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — NETWORKING CONFIGURATION
#  Single source of truth for all networking parameters.
#  Import from here instead of hardcoding values.
# ═══════════════════════════════════════════════════════════════════

import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)

# ─── Application ─────────────────────────────────────────────────
APP_VERSION = "4.2"
SYSTEM_NAME = "Aether Surgical Console"

# ─── Broker / Server ─────────────────────────────────────────────
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 5000

# ─── Security: Database ──────────────────────────────────────────
# SQLite database lives in the project's data/ directory.
# data/ is in .gitignore — never committed to source control.
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
DATABASE_PATH = os.path.join(_DATA_DIR, "aether.db")

# ─── Security: Certificates ──────────────────────────────────────
# All certificates and keys live in data/certs/.
# Never committed to source control (.gitignore covers data/).
CERTS_DIR = os.path.join(_DATA_DIR, "certs")

# ─── Legacy paths (kept for migration reference — NOT used by code) ─
# The following constants are retained so any existing code that
# imports them does not break with an ImportError during migration.
# They should be considered deprecated and removed once all callers
# have been updated.
ENCRYPTION_KEY_PATH = os.path.join(_THIS_DIR, "aether.key")     # DEPRECATED
CREDENTIALS_PATH    = os.path.join(_THIS_DIR, "credentials.json")  # DEPRECATED

# ─── Heartbeat ───────────────────────────────────────────────────
HEARTBEAT_INTERVAL_S = 2          # Send heartbeat every 2 seconds
HEARTBEAT_TIMEOUT_S = 6           # Consider dead after 3 missed heartbeats

# ─── Reconnection ────────────────────────────────────────────────
RECONNECT_INTERVAL_S = 3          # Wait 3 seconds before reconnect attempt
MAX_RECONNECT_ATTEMPTS = 0        # 0 = unlimited

# ─── Topics ──────────────────────────────────────────────────────
TOPICS = [
    "patient_vitals",
    "robot_telemetry",
    "alerts",
    "system_status",
    "connection_status",
    "video_broadcast",
]

# ─── Wire Protocol ───────────────────────────────────────────────
HEADER_SIZE = 4                   # 4-byte big-endian unsigned int
HEADER_FORMAT = "!I"              # struct format: network byte order, unsigned int
MAX_PAYLOAD_SIZE = 50 * 1024 * 1024   # 50 MB — pre-read hard limit (required for video)

# ─── Per-Class Payload Limits (post-decode secondary enforcement) ─
# Because the protocol is [4-byte length][JSON payload], the topic/
# message class cannot be determined until AFTER the payload is
# received and decoded. These limits are therefore applied after
# decode: a client that sent an oversized payload for its class is
# immediately disconnected.
MAX_PAYLOAD_CTRL       =       64 * 1024   # 64 KB  — control messages
MAX_PAYLOAD_TELEMETRY  =      512 * 1024   # 512 KB — telemetry/data messages
MAX_PAYLOAD_VIDEO      = 50 * 1024 * 1024  # 50 MB  — video messages (unchanged)

# ─── Broker Resource Limits ───────────────────────────────────────
MAX_CLIENTS = 20                  # Maximum simultaneous broker connections

# Per-chunk socket timeout during large payload receive (seconds).
# Prevents a slow or stalling client from holding the broker's
# receive thread indefinitely.
RECV_CHUNK_TIMEOUT_S = 30

# ─── Database Backup ─────────────────────────────────────────────
# Auto-backup runs periodically from the broker heartbeat monitor.
# Backups are stored in data/backups/ and rotated to keep the last N.
DB_BACKUP_INTERVAL_MINUTES = 60   # Backup every 60 minutes
DB_BACKUP_KEEP_COUNT = 5          # Retain the last 5 backups

# ─── Publish Intervals ──────────────────────────────────────────
VITALS_PUBLISH_INTERVAL_S = 1.0   # Surgeon Console publishes vitals every 1s
TELEMETRY_PUBLISH_INTERVAL_S = 1.0  # Robot Console publishes telemetry every 1s
