# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — DEDICATED SECURE VIDEO STREAMING (PORT 5001)
#  High-performance, low-latency binary TCP video transport.
#  Strictly 1-to-1: Robot Console (source) -> Surgeon Console (receiver).
#  Authenticated via mTLS using the Aether Local CA.
# ═══════════════════════════════════════════════════════════════════

import socket
import ssl
import struct
import threading
import logging
import time
from typing import Optional
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage

from shared_networking.config import VIDEO_HOST, VIDEO_PORT, CERTS_DIR
from shared_networking.tls import TLSManager

log = logging.getLogger(__name__)

HEADER_FORMAT = "!I"
HEADER_SIZE = 4
MAX_FRAME_SIZE = 50 * 1024 * 1024  # 50 MB safety ceiling


class VideoReceiver(QObject):
    """Secure TCP Video Receiver (Server) running on Port 5001.

    Enforces strict 1-to-1 topology:
      - Only accepts authenticated Robot Console connections.
      - Rejects Observer Screens, Data Generator, or unknown/unauthenticated endpoints.
      - Decodes length-prefixed JPEG frames to QImage for the Live Video UI.
    """

    frame_received = pyqtSignal(QImage)
    status_changed = pyqtSignal(str)
    client_connected = pyqtSignal(str)
    client_rejected = pyqtSignal(str, str)  # (address, reason)

    def __init__(self, host: str = VIDEO_HOST, port: int = VIDEO_PORT,
                 certs_dir: str = CERTS_DIR, authorized_device: str = "robot_console",
                 use_tls: bool = True, parent=None):
        super().__init__(parent)
        self._host = host
        self._port = port
        self._certs_dir = certs_dir
        self._authorized_device = authorized_device
        self._use_tls = use_tls
        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._active_client_sock: Optional[socket.socket] = None
        self._active_client_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start listening for incoming video connections."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._server_loop,
            daemon=True,
            name="VideoReceiverServer"
        )
        self._thread.start()
        log.info(f"[VideoReceiver] Listening on TCP {self._host}:{self._port} (TLS={self._use_tls})")

    def stop(self):
        """Stop receiver server and close sockets cleanly."""
        self._running = False

        with self._active_client_lock:
            if self._active_client_sock:
                try:
                    self._active_client_sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self._active_client_sock.close()
                except Exception:
                    pass
                self._active_client_sock = None

        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

        if self._thread and self._thread != threading.current_thread() and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        log.info("[VideoReceiver] Stopped")

    def _server_loop(self):
        """Main server loop listening for client connections."""
        tls_mgr = TLSManager(self._certs_dir)
        tls_ctx = None

        if self._use_tls:
            try:
                # Surgeon console serves video port, requires client mTLS cert
                tls_ctx = tls_mgr.create_server_context(server_device="surgeon_console")
            except Exception as e:
                log.warning(f"[VideoReceiver] Could not create TLS server context ({e}). Falling back if configured.")
                tls_ctx = None

        try:
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            raw_sock.bind((self._host, self._port))
            raw_sock.listen(2)
            raw_sock.settimeout(1.0)

            if tls_ctx is not None:
                self._server_sock = tls_ctx.wrap_socket(raw_sock, server_side=True)
            else:
                self._server_sock = raw_sock

        except Exception as e:
            log.error(f"[VideoReceiver] Bind failed on {self._host}:{self._port}: {e}")
            self._running = False
            return

        while self._running:
            try:
                client_sock, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    log.warning(f"[VideoReceiver] Accept error: {e}")
                break

            # Validate client authorization
            if not self._validate_client(client_sock, addr, tls_mgr):
                try:
                    client_sock.close()
                except Exception:
                    pass
                continue

            # Ensure strictly 1-to-1 connection
            with self._active_client_lock:
                if self._active_client_sock is not None:
                    log.warning(f"[SECURITY] Rejected extra video connection from {addr} — stream already active")
                    self.client_rejected.emit(f"{addr[0]}:{addr[1]}", "Stream already active (1-to-1 only)")
                    try:
                        client_sock.close()
                    except Exception:
                        pass
                    continue
                self._active_client_sock = client_sock

            log.info(f"[VideoReceiver] Authorized video source connected from {addr}")
            self.status_changed.emit(f"Connected: {addr[0]}")
            self.client_connected.emit(f"{addr[0]}:{addr[1]}")

            try:
                self._handle_client(client_sock)
            finally:
                with self._active_client_lock:
                    if self._active_client_sock is client_sock:
                        self._active_client_sock = None

        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

    def _validate_client(self, client_sock: socket.socket, addr: tuple, tls_mgr: TLSManager) -> bool:
        """Authenticate that connecting client is specifically the authorized Robot Console."""
        if not self._use_tls:
            return True

        peer_fp = TLSManager.get_peer_fingerprint(client_sock)
        if not peer_fp:
            log.warning(f"[SECURITY] Rejected video connection from {addr} — no client certificate presented")
            self.client_rejected.emit(f"{addr[0]}:{addr[1]}", "No client certificate")
            return False

        # Verify device against the authorized robot_console certificate fingerprint
        if tls_mgr.device_cert_exists(self._authorized_device):
            authorized_fp = tls_mgr.get_cert_fingerprint(
                tls_mgr.device_cert_path(self._authorized_device)
            )
            if peer_fp.lower() != authorized_fp.lower():
                log.warning(
                    f"[SECURITY] Rejected unauthorized video connection from {addr} "
                    f"(fp={peer_fp[:16]}... != authorized {self._authorized_device})"
                )
                self.client_rejected.emit(
                    f"{addr[0]}:{addr[1]}",
                    f"Unauthorized device certificate (not {self._authorized_device})"
                )
                return False

        log.info(f"[VideoReceiver] Peer certificate verified: {self._authorized_device} ({addr[0]})")
        return True

    def _handle_client(self, client_sock: socket.socket):
        """Handle receiving binary JPEG frames from the connected video broadcaster."""
        client_sock.settimeout(2.0)
        try:
            while self._running:
                # Read 4-byte length header
                header_bytes = self._recv_all(client_sock, HEADER_SIZE)
                if not header_bytes:
                    break

                frame_len = struct.unpack(HEADER_FORMAT, header_bytes)[0]
                if frame_len <= 0 or frame_len > MAX_FRAME_SIZE:
                    log.warning(f"[VideoReceiver] Invalid frame size: {frame_len}")
                    break

                # Read raw JPEG bytes
                jpeg_bytes = self._recv_all(client_sock, frame_len)
                if not jpeg_bytes:
                    break

                # Decode QImage directly from raw JPEG buffer
                img = QImage.fromData(jpeg_bytes)
                if not img.isNull():
                    self.frame_received.emit(img)

        except (socket.timeout, ConnectionResetError, BrokenPipeError, ssl.SSLError):
            pass
        except Exception as e:
            log.warning(f"[VideoReceiver] Video stream error: {e}")
        finally:
            try:
                client_sock.close()
            except Exception:
                pass
            log.info("[VideoReceiver] Video client disconnected")
            self.status_changed.emit("Disconnected")

    def _recv_all(self, sock: socket.socket, length: int) -> Optional[bytes]:
        """Helper to receive exactly `length` bytes from socket with timeout."""
        data = bytearray()
        while len(data) < length and self._running:
            try:
                packet = sock.recv(min(length - len(data), 65536))
                if not packet:
                    return None
                data.extend(packet)
            except (socket.timeout, ssl.SSLError):
                if not self._running:
                    return None
                continue
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                return None
        return bytes(data) if len(data) == length else None
