"""Comprehensive unit tests for the Aether wire protocol, message encoding, and framing."""
import sys
import os
import struct
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared_networking.protocol import (
    get_message_class, get_class_limit,
    VIDEO_TOPICS, TELEMETRY_TOPICS,
    create_message, create_heartbeat, create_handshake,
    create_subscribe, create_unsubscribe, create_client_list_request,
    create_logout, encode_message, encode_message_full,
    decode_header, decode_payload, decode_payload_full,
    is_control_message, CTRL_HEARTBEAT, CTRL_HANDSHAKE,
    CTRL_SUBSCRIBE, CTRL_UNSUBSCRIBE, CTRL_CLIENT_LIST,
    CTRL_LOGOUT, CTRL_AUTH_REJECT,
)
from shared_networking.config import (
    HEADER_SIZE, HEADER_FORMAT, MAX_PAYLOAD_SIZE,
    MAX_PAYLOAD_CTRL, MAX_PAYLOAD_TELEMETRY, MAX_PAYLOAD_VIDEO,
)


class TestMessageClassification:
    def test_video_topics_return_video(self):
        for t in VIDEO_TOPICS:
            assert get_message_class(t) == "video", t

    def test_telemetry_topics_return_telemetry(self):
        for t in TELEMETRY_TOPICS:
            assert get_message_class(t) == "telemetry", t

    def test_unknown_topic_is_ctrl(self):
        assert get_message_class("_subscribe") == "ctrl"
        assert get_message_class("_heartbeat") == "ctrl"
        assert get_message_class("_logout") == "ctrl"
        assert get_message_class("some_random_topic") == "ctrl"

    def test_empty_topic_is_ctrl(self):
        assert get_message_class("") == "ctrl"


class TestClassLimits:
    def test_video_limit(self):
        assert get_class_limit("video_broadcast") == MAX_PAYLOAD_VIDEO

    def test_telemetry_limit(self):
        assert get_class_limit("patient_vitals") == MAX_PAYLOAD_TELEMETRY

    def test_ctrl_limit(self):
        assert get_class_limit("_subscribe") == MAX_PAYLOAD_CTRL

    def test_limits_are_ordered(self):
        assert MAX_PAYLOAD_VIDEO > MAX_PAYLOAD_TELEMETRY > MAX_PAYLOAD_CTRL > 0


class TestMessageEnvelopeAndHelpers:
    def test_create_message(self):
        msg = create_message("patient_vitals", "surgeon", {"hr": 75})
        assert msg["topic"] == "patient_vitals"
        assert msg["source"] == "surgeon"
        assert msg["payload"] == {"hr": 75}
        assert "timestamp" in msg

    def test_create_heartbeat(self):
        msg = create_heartbeat("robot_console")
        assert msg["topic"] == CTRL_HEARTBEAT
        assert is_control_message(msg)

    def test_create_handshake(self):
        msg = create_handshake("surgeon_console", ["alerts"], ["patient_vitals"], "admin", "admin", "token123")
        assert msg["topic"] == CTRL_HANDSHAKE
        assert msg["payload"]["client_name"] == "surgeon_console"
        assert msg["payload"]["username"] == "admin"
        assert msg["payload"]["session_id"] == "token123"

    def test_create_subscribe_and_unsubscribe(self):
        sub = create_subscribe("observer", ["alerts", "patient_vitals"])
        assert sub["topic"] == CTRL_SUBSCRIBE
        assert sub["payload"]["topics"] == ["alerts", "patient_vitals"]

        unsub = create_unsubscribe("observer", ["alerts"])
        assert unsub["topic"] == CTRL_UNSUBSCRIBE
        assert unsub["payload"]["topics"] == ["alerts"]

    def test_create_logout(self):
        msg = create_logout("surgeon_console", "session_abc")
        assert msg["topic"] == CTRL_LOGOUT
        assert msg["payload"]["session_id"] == "session_abc"
        assert is_control_message(msg)


class TestEncodingAndDecoding:
    def test_encode_and_decode_roundtrip(self):
        original = {"topic": "patient_vitals", "source": "surgeon", "payload": {"hr": 80, "spo2": 99}}
        wire_data = encode_message(original)

        assert len(wire_data) >= HEADER_SIZE
        header = wire_data[:HEADER_SIZE]
        payload_len = decode_header(header)
        payload_bytes = wire_data[HEADER_SIZE:HEADER_SIZE + payload_len]

        decoded = decode_payload(payload_bytes)
        assert decoded["topic"] == original["topic"]
        assert decoded["payload"]["hr"] == 80
        assert decoded["payload"]["spo2"] == 99

    def test_decode_invalid_header_length(self):
        with pytest.raises(ValueError, match="Invalid header size"):
            decode_header(b"\x00\x01")

    def test_decode_oversized_header_limit(self):
        oversized = MAX_PAYLOAD_SIZE + 100
        header = struct.pack(HEADER_FORMAT, oversized)
        with pytest.raises(ValueError, match="Payload too large"):
            decode_header(header)

    def test_decode_malformed_json_payload(self):
        corrupted = b"{not: valid json"
        with pytest.raises(ValueError, match="Malformed payload"):
            decode_payload(corrupted)

    def test_decode_non_utf8_payload(self):
        corrupted = b"\xff\xfe\xfd\x00\x12"
        with pytest.raises(ValueError, match="Malformed payload"):
            decode_payload(corrupted)

    def test_encode_full_returns_three_tuple(self):
        msg = {"topic": "alerts", "payload": {}}
        full_wire, plaintext, encrypted = encode_message_full(msg)
        assert isinstance(full_wire, bytes)
        assert isinstance(plaintext, bytes)
        assert encrypted == b""
