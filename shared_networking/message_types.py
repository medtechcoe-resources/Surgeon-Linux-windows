# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — MESSAGE TYPES & TOPIC CONSTANTS
#  Single source of truth for all topic names and control messages.
#  Existing topic names are preserved — no renaming.
# ═══════════════════════════════════════════════════════════════════

# ─── Data Topics (published by applications) ─────────────────────

TOPIC_ROBOT_TELEMETRY = "robot_telemetry"
TOPIC_ROBOT_STATUS = "robot_status"
TOPIC_PATIENT_VITALS = "patient_vitals"
TOPIC_ALERTS = "alerts"
TOPIC_SYSTEM_STATUS = "system_status"
TOPIC_CONNECTION_STATUS = "connection_status"
TOPIC_VIDEO_FRAME = "video_frame"
TOPIC_VIDEO_DETECTION = "video_detection"
TOPIC_SYSTEM_LOGS = "system_logs"
TOPIC_ROBOT_COMMANDS = "robot_commands"
TOPIC_SYSTEM_CONTROL = "system_control"
TOPIC_VIDEO_BROADCAST = "video_broadcast"  # Robot Console -> Surgeon Console live video

# ─── All Topics List ──────────────────────────────────────────────────

ALL_TOPICS = [
    TOPIC_ROBOT_TELEMETRY,
    TOPIC_ROBOT_STATUS,
    TOPIC_PATIENT_VITALS,
    TOPIC_ALERTS,
    TOPIC_SYSTEM_STATUS,
    TOPIC_CONNECTION_STATUS,
    TOPIC_VIDEO_FRAME,
    TOPIC_VIDEO_DETECTION,
    TOPIC_SYSTEM_LOGS,
    TOPIC_ROBOT_COMMANDS,
    TOPIC_SYSTEM_CONTROL,
    TOPIC_VIDEO_BROADCAST,
]

# ─── Client Publish/Subscribe Profiles ─────────────────────────────────
# Architecture (v2): Data Generator produces all simulated data.
# UI consoles are pure subscribers except connection_status.

DATA_GENERATOR_PUBLISHES = [
    TOPIC_PATIENT_VITALS,
    TOPIC_ROBOT_TELEMETRY,
    TOPIC_ALERTS,
    TOPIC_CONNECTION_STATUS,
]

SURGEON_PUBLISHES = [
    TOPIC_SYSTEM_CONTROL,
]

SURGEON_SUBSCRIBES = [
    TOPIC_PATIENT_VITALS,
    TOPIC_ROBOT_TELEMETRY,
    TOPIC_ROBOT_STATUS,
    TOPIC_ALERTS,
    TOPIC_CONNECTION_STATUS,
    TOPIC_SYSTEM_STATUS,
    TOPIC_VIDEO_BROADCAST,
]

ROBOT_PUBLISHES = [
    TOPIC_CONNECTION_STATUS,
    TOPIC_VIDEO_BROADCAST,
]

ROBOT_SUBSCRIBES = [
    TOPIC_ROBOT_TELEMETRY,
    TOPIC_PATIENT_VITALS,
    TOPIC_ALERTS,
    TOPIC_ROBOT_COMMANDS,
    TOPIC_SYSTEM_CONTROL,
]

OBSERVER_SUBSCRIBES = [
    TOPIC_ROBOT_TELEMETRY,
    TOPIC_ROBOT_STATUS,
    TOPIC_PATIENT_VITALS,
    TOPIC_ALERTS,
    TOPIC_VIDEO_FRAME,
    TOPIC_VIDEO_DETECTION,
    TOPIC_SYSTEM_LOGS,
    TOPIC_CONNECTION_STATUS,
    TOPIC_SYSTEM_STATUS,
]
