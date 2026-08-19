"""Tests for protocol.get_message_class() and get_class_limit()."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from shared_networking.protocol import get_message_class, get_class_limit, VIDEO_TOPICS, TELEMETRY_TOPICS
from shared_networking.config import MAX_PAYLOAD_CTRL, MAX_PAYLOAD_TELEMETRY, MAX_PAYLOAD_VIDEO


class TestGetMessageClass:
    def test_video_topics_return_video(self):
        for t in VIDEO_TOPICS:
            assert get_message_class(t) == "video", t

    def test_telemetry_topics_return_telemetry(self):
        for t in TELEMETRY_TOPICS:
            assert get_message_class(t) == "telemetry", t

    def test_unknown_topic_is_ctrl(self):
        assert get_message_class("_subscribe") == "ctrl"
        assert get_message_class("_heartbeat") == "ctrl"
        assert get_message_class("some_random_topic") == "ctrl"

    def test_empty_topic_is_ctrl(self):
        assert get_message_class("") == "ctrl"


class TestGetClassLimit:
    def test_video_limit(self):
        assert get_class_limit("video_broadcast") == MAX_PAYLOAD_VIDEO

    def test_telemetry_limit(self):
        assert get_class_limit("patient_vitals") == MAX_PAYLOAD_TELEMETRY

    def test_ctrl_limit(self):
        assert get_class_limit("_subscribe") == MAX_PAYLOAD_CTRL

    def test_limits_are_ordered(self):
        assert MAX_PAYLOAD_VIDEO > MAX_PAYLOAD_TELEMETRY > MAX_PAYLOAD_CTRL > 0
