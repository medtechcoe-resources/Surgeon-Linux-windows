# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — NETWORKING PACKAGE
#  Public API for the shared pub-sub networking library.
# ═══════════════════════════════════════════════════════════════════

from shared_networking.config import (
    BROKER_HOST, BROKER_PORT, HEARTBEAT_INTERVAL_S,
    RECONNECT_INTERVAL_S,
    # Legacy constants kept for reference — considered deprecated
    ENCRYPTION_KEY_PATH, CREDENTIALS_PATH,
    APP_VERSION, SYSTEM_NAME,
    DATABASE_PATH, CERTS_DIR,
    VITALS_PUBLISH_INTERVAL_S, TELEMETRY_PUBLISH_INTERVAL_S,
)
from shared_networking.protocol import (
    create_message, encode_message, decode_header, decode_payload,
    format_json_pretty, is_control_message,
)
from shared_networking.database import AetherDatabase
from shared_networking.authentication import AuthManager
from shared_networking.connection_manager import ConnectionManager
from shared_networking.broker import PubSubBroker
from shared_networking.logger import get_logger
from shared_networking.message_types import (
    TOPIC_ROBOT_TELEMETRY, TOPIC_ROBOT_STATUS, TOPIC_PATIENT_VITALS,
    TOPIC_ALERTS, TOPIC_SYSTEM_STATUS, TOPIC_CONNECTION_STATUS,
)
