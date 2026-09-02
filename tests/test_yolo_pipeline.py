"""Tests for YoloPipeline methods."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
import pytest

# Ensure a QApplication instance exists for QObject / signals
@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

from yolo_pipeline import YoloPipeline


class TestYoloPipeline:
    def test_stop_method_exists_and_callable(self):
        pipeline = YoloPipeline()
        assert hasattr(pipeline, "stop")
        assert hasattr(pipeline, "stop_video")
        # Ensure calling stop does not raise AttributeError
        pipeline.stop()

    def test_stop_video_method(self):
        pipeline = YoloPipeline()
        pipeline.stop_video()
