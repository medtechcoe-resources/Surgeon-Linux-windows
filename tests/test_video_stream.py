"""Unit tests for dedicated TCP Video Stream (Port 5001)."""
import sys
import os
import time
import pytest

proj_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, proj_root)
sys.path.insert(0, os.path.join(proj_root, "Robot-Console"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QColor

@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

from shared_networking.video_stream import VideoReceiver
from services.video_broadcaster import VideoBroadcastService


class TestTCPVideoStream:
    def test_video_receiver_and_broadcaster(self, qapp):
        # Create test QImage (red 100x100 box)
        img = QImage(100, 100, QImage.Format.Format_RGB888)
        img.fill(QColor("red"))

        received_images = []

        # Select port 5001, or fallback to an available port if 5001 is in use by running system
        import socket
        test_port = 5001
        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", test_port))
            sock.close()
        except OSError:
            sock.bind(("127.0.0.1", 0))
            test_port = sock.getsockname()[1]
            sock.close()

        # Start VideoReceiver server on test_port
        receiver = VideoReceiver(host="127.0.0.1", port=test_port)
        receiver.frame_received.connect(lambda qimg: received_images.append(qimg))
        receiver.start()

        time.sleep(0.2)

        # Start VideoBroadcastService client on test_port
        broadcaster = VideoBroadcastService(port=test_port)
        broadcaster.start_broadcast()

        time.sleep(0.2)

        # Send test frame
        broadcaster.broadcast_qimage(img)

        # Allow network send & process Qt event queue for cross-thread signals
        for _ in range(50):
            qapp.processEvents()
            time.sleep(0.02)
            if len(received_images) > 0:
                break

        # Stop services cleanly
        broadcaster.stop()
        receiver.stop()

        assert len(received_images) > 0, "No frames received over TCP 5001"
        rcv_img = received_images[0]
        assert rcv_img.width() == 100
        assert rcv_img.height() == 100
