# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — PUB-SUB WIRE PROTOCOL
#  Defines the message envelope format and encoding/decoding for
#  TCP communication between broker and clients.
#
#  Wire format: [4-byte length header][JSON payload]
#
#  Security note (v2):
#    All transport-level encryption is handled by TLS 1.3 at the
#    socket layer. This module does NOT perform application-level
#    encryption. The plaintext fallback and Fernet hooks that existed
#    in the previous version have been REMOVED — no silent downgrade
#    to plaintext is possible.
#
#  The 4-byte length-prefixed JSON framing is PRESERVED unchanged.
#  All topic names and control message topics are PRESERVED unchanged.
# ═══════════════════════════════════════════════════════════════════

import json
import struct
import logging
from datetime import datetime
from typing import Optional

from shared_networking.config import (
    HEADER_SIZE, HEADER_FORMAT, MAX_PAYLOAD_SIZE,
    MAX_PAYLOAD_CTRL, MAX_PAYLOAD_TELEMETRY, MAX_PAYLOAD_VIDEO,
)

log = logging.getLogger(__name__)


# ─── Message Class Classification ────────────────────────────────
# The wire format is [4-byte length][JSON payload]. The topic (and
# therefore message class) is encoded INSIDE the JSON, so class
# cannot be known until after the payload is received and decoded.
# These sets drive post-decode per-class size enforcement in the
# broker. A protocol redesign to move the topic into the header is
# explicitly out of scope.

VIDEO_TOPICS: frozenset = frozenset({
    "video_broadcast",
    "video_frame",
    "video_detection",
})

TELEMETRY_TOPICS: frozenset = frozenset({
    "patient_vitals",
    "robot_telemetry",
    "alerts",
    "system_status",
    "connection_status",
    "robot_commands",
    "system_control",
    "robot_status",
    "system_logs",
})

# Control topics (internal protocol, all prefixed with "_")
# Any topic not in VIDEO_TOPICS or TELEMETRY_TOPICS is treated as
# control for size-enforcement purposes.

_CLASS_LIMITS: dict = {
    "ctrl":      MAX_PAYLOAD_CTRL,
    "telemetry": MAX_PAYLOAD_TELEMETRY,
    "video":     MAX_PAYLOAD_VIDEO,
}


def get_message_class(topic: str) -> str:
    """Return the message class for a topic: 'ctrl', 'telemetry', or 'video'.

    Used for post-decode per-class payload size enforcement.
    Control topics are those starting with '_' or not in either
    TELEMETRY_TOPICS or VIDEO_TOPICS.
    """
    if topic in VIDEO_TOPICS:
        return "video"
    if topic in TELEMETRY_TOPICS:
        return "telemetry"
    return "ctrl"


def get_class_limit(topic: str) -> int:
    """Return the maximum permitted payload size (bytes) for a topic.

    This is a post-decode secondary check. The pre-read hard limit
    (MAX_PAYLOAD_SIZE) is always enforced before the payload is read.
    """
    return _CLASS_LIMITS[get_message_class(topic)]


# ─── Internal Control Topics ─────────────────────────────────────
CTRL_SUBSCRIBE   = "_subscribe"
CTRL_UNSUBSCRIBE = "_unsubscribe"
CTRL_HEARTBEAT   = "_heartbeat"
CTRL_HANDSHAKE   = "_handshake"
CTRL_CLIENT_LIST = "_client_list"
CTRL_CLIENT_UPDATE = "_client_update"
CTRL_AUTH_REJECT = "_auth_reject"    # New: broker sends on auth failure


def create_message(topic: str, source: str, payload: dict) -> dict:
    """Create a standard pub-sub message envelope."""
    return {
        "topic":     topic,
        "source":    source,
        "timestamp": datetime.now().isoformat(),
        "payload":   payload,
    }


def create_heartbeat(source: str) -> dict:
    """Create a heartbeat control message."""
    return create_message(CTRL_HEARTBEAT, source, {"status": "alive"})


def create_handshake(client_name: str, publish_topics: list = None,
                     subscribe_topics: list = None,
                     username: str = "", role: str = "",
                     session_id: str = "") -> dict:
    """Create a handshake message for initial broker registration.

    Includes authentication context (username, session_id).
    NOTE: The broker NEVER trusts the client-provided role.
          The role field is informational only and is overridden by
          the broker's database lookup.
    """
    return create_message(CTRL_HANDSHAKE, client_name, {
        "client_name":      client_name,
        "version":          "2.0",
        "publish_topics":   publish_topics or [],
        "subscribe_topics": subscribe_topics or [],
        "username":         username,
        "session_id":       session_id,
        # role is sent for legacy UI display only — broker ignores it for auth
        "role":             role,
    })


def create_subscribe(source: str, topics: list) -> dict:
    """Create a subscription request message."""
    return create_message(CTRL_SUBSCRIBE, source, {"topics": topics})


def create_unsubscribe(source: str, topics: list) -> dict:
    """Create an unsubscription request message."""
    return create_message(CTRL_UNSUBSCRIBE, source, {"topics": topics})


def create_client_list_request(source: str) -> dict:
    """Create a request for the list of connected clients."""
    return create_message(CTRL_CLIENT_LIST, source, {})


# ─── Encoding / Decoding ─────────────────────────────────────────

def encode_message_full(message: dict) -> tuple:
    """Encode a message dict into length-prefixed JSON bytes for TCP.

    Returns:
        (full_wire_bytes, plaintext_json_bytes, encrypted_payload_bytes)

    The third element is always b'' because transport-level encryption
    (TLS 1.3) is used instead of application-level Fernet encryption.
    The tuple signature is preserved for backwards compatibility with
    existing callers (e.g. ConnectionManager, CommunicationTab).
    """
    plaintext_bytes = json.dumps(
        message, separators=(",", ":"), default=str
    ).encode("utf-8")

    header = struct.pack(HEADER_FORMAT, len(plaintext_bytes))
    return header + plaintext_bytes, plaintext_bytes, b""


def encode_message(message: dict) -> bytes:
    """Encode a message dict into length-prefixed JSON bytes for TCP."""
    return encode_message_full(message)[0]


def decode_header(header_bytes: bytes) -> int:
    """Decode the 4-byte length header to get payload size."""
    if len(header_bytes) != HEADER_SIZE:
        raise ValueError(f"Invalid header size: {len(header_bytes)}")
    length = struct.unpack(HEADER_FORMAT, header_bytes)[0]
    if length > MAX_PAYLOAD_SIZE:
        raise ValueError(f"Payload too large: {length} bytes")
    return length


def decode_payload_full(payload_bytes: bytes) -> tuple:
    """Decode payload bytes into a message dict.

    Returns:
        (message_dict, plaintext_bytes, b'')

    TLS handles all decryption at the socket layer. This function
    simply parses the JSON payload. No plaintext fallback logic exists.
    A malformed payload raises an exception (fail closed).
    """
    try:
        message = json.loads(payload_bytes.decode("utf-8"))
        return message, payload_bytes, b""
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Malformed payload — rejecting message: {e}") from e


def decode_payload(payload_bytes: bytes) -> dict:
    """Decode payload bytes into a message dict."""
    return decode_payload_full(payload_bytes)[0]


def format_json_pretty(data: dict) -> str:
    """Format a dict as pretty-printed JSON for display."""
    return json.dumps(data, indent=2, default=str)


def is_control_message(message: dict) -> bool:
    """Check if a message is an internal control message."""
    topic = message.get("topic", "")
    return topic.startswith("_")
