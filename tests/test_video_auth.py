"""Tests for Video channel security & strict 1-to-1 authorization:
- Robot Console -> Surgeon Console video ALLOWED
- Observer -> Surgeon video port REJECTED
- Data Generator -> Surgeon video port REJECTED
- Unknown device -> Surgeon video port REJECTED
- Multiple simultaneous connections REJECTED (Strict 1-to-1)
"""
import sys
import os
import socket
import struct
import time
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Robot-Console"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QColor

from shared_networking.video_stream import VideoReceiver, HEADER_FORMAT
from shared_networking.tls import TLSManager
from shared_networking.config import CERTS_DIR
from services.video_broadcaster import VideoBroadcastService


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestVideoAuthorization:
    def test_robot_console_to_surgeon_console_video_pass(self, qapp):
        """Robot Console connects and streams video to Surgeon Console."""
        test_port = 5091
        receiver = VideoReceiver(host="127.0.0.1", port=test_port, use_tls=True)
        received_frames = []
        receiver.frame_received.connect(lambda qimg: received_frames.append(qimg))
        receiver.start()

        time.sleep(0.3)

        broadcaster = VideoBroadcastService(host="127.0.0.1", port=test_port, use_tls=True)
        broadcaster.start_broadcast()

        time.sleep(0.3)

        img = QImage(64, 64, QImage.Format.Format_RGB888)
        img.fill(QColor("blue"))
        broadcaster.broadcast_qimage(img)

        for _ in range(50):
            qapp.processEvents()
            time.sleep(0.02)
            if len(received_frames) > 0:
                break

        broadcaster.stop()
        receiver.stop()

        assert len(received_frames) > 0, "Authorized Robot Console video frame was not received"
        assert received_frames[0].width() == 64
        assert received_frames[0].height() == 64

    def test_observer_rejected_from_video_port(self, qapp):
        """Observer device cert connecting to video port must be REJECTED immediately."""
        test_port = 5092
        receiver = VideoReceiver(host="127.0.0.1", port=test_port, use_tls=True)
        rejected_events = []
        receiver.client_rejected.connect(lambda addr, reason: rejected_events.append((addr, reason)))
        receiver.start()

        time.sleep(0.3)

        tls_mgr = TLSManager(CERTS_DIR)
        observer_ctx = tls_mgr.create_client_context("observer_screen")

        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(2.0)
        sock = observer_ctx.wrap_socket(raw_sock, server_hostname=None)

        try:
            sock.connect(("127.0.0.1", test_port))
            # Try to send a fake frame
            data = b"FAKE_FRAME"
            sock.sendall(struct.pack(HEADER_FORMAT, len(data)) + data)
            time.sleep(0.3)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass

        for _ in range(20):
            qapp.processEvents()
            time.sleep(0.02)

        receiver.stop()

        assert len(rejected_events) > 0, "Observer connection was not rejected"
        assert "observer_screen" in rejected_events[0][1] or "Unauthorized" in rejected_events[0][1]

    def test_data_generator_rejected_from_video_port(self, qapp):
        """Data Generator connecting to video port must be REJECTED immediately."""
        test_port = 5093
        receiver = VideoReceiver(host="127.0.0.1", port=test_port, use_tls=True)
        rejected_events = []
        receiver.client_rejected.connect(lambda addr, reason: rejected_events.append((addr, reason)))
        receiver.start()

        time.sleep(0.3)

        tls_mgr = TLSManager(CERTS_DIR)
        data_gen_ctx = tls_mgr.create_client_context("data_generator")

        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(2.0)
        sock = data_gen_ctx.wrap_socket(raw_sock, server_hostname=None)

        try:
            sock.connect(("127.0.0.1", test_port))
            time.sleep(0.3)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass

        for _ in range(20):
            qapp.processEvents()
            time.sleep(0.02)

        receiver.stop()

        assert len(rejected_events) > 0, "Data Generator connection was not rejected"
