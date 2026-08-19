# ═══════════════════════════════════════════════════════════════════
#  ROBOT CONSOLE — TCP CLIENT
#  Handles all TCP communication with the Surgeon Console.
#  Runs on dedicated QThreads to keep the UI responsive.
#
#  Architecture Note (traced 2026-08-13):
#  This is the Robot-Console's own networking layer and is ACTIVELY
#  USED by Robot-Console/ui/main_window.py. It is intentionally
#  separate from shared_networking.connection_manager (the Surgeon
#  Console's pub-sub client), as it operates over a separate
#  direct TCP connection and has its own protocol layer
#  (Robot-Console/networking/protocol.py).
#
#  This client does NOT use mTLS and is considered LEGACY relative
#  to the shared_networking pub-sub architecture. Migration to the
#  shared pub-sub model is a future scope item. Do not remove or
#  rewrite this module without first migrating main_window.py and
#  validating full Robot-Console functionality.
# ═══════════════════════════════════════════════════════════════════

import socket
import time
import threading
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

from networking.protocol import (
    create_heartbeat, create_handshake, create_message,
    MSG_HEARTBEAT,
)
from constants import (
    TCP_HOST, TCP_PORT,
    HEARTBEAT_INTERVAL_MS, MAX_RECONNECT_ATTEMPTS,
)


class TCPClient(QObject):
    """TCP client that communicates with the Surgeon Console.

    Emits Qt signals for all events so the UI can update safely
    from the main thread.
    """

    # ─── Signals ──────────────────────────────────────────────────
    connected       = pyqtSignal()
    disconnected    = pyqtSignal()
    data_received   = pyqtSignal(dict)          # parsed message dict
    data_sent       = pyqtSignal(dict)          # sent message dict
    error_occurred  = pyqtSignal(str)           # error description
    log_message     = pyqtSignal(str, str)      # (level, message)
    stats_updated   = pyqtSignal(dict)          # connection stats

    def __init__(self, parent=None):
        super().__init__(parent)
        self._socket: socket.socket = None
        self._is_connected = False
        self._host = TCP_HOST
        self._port = TCP_PORT
        self._lock = threading.Lock()

        # Statistics
        self._packets_sent = 0
        self._packets_received = 0
        self._bytes_sent = 0
        self._bytes_received = 0
        self._errors = 0
        self._last_sent_time = "---"
        self._last_received_time = "---"
        self._reconnect_count = 0

        # Threads
        self._receive_thread: threading.Thread = None
        self._running = False

        # Heartbeat timer (runs on main thread via Qt event loop)
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(HEARTBEAT_INTERVAL_MS)
        self._heartbeat_timer.timeout.connect(self._send_heartbeat)

    # ─── Properties ───────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    # ─── Public API ───────────────────────────────────────────────

    def connect_to_server(self, host: str = None, port: int = None):
        """Establish a TCP connection to the Surgeon Console."""
        if self._is_connected:
            self.log_message.emit("WARNING", "Already connected")
            return

        if host:
            self._host = host
        if port:
            self._port = port

        self.log_message.emit("INFO",
            f"Connecting to {self._host}:{self._port}...")

        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(5.0)
            self._socket.connect((self._host, self._port))
            self._socket.settimeout(None)  # blocking mode for receive thread

            self._is_connected = True
            self._running = True

            # Send handshake
            handshake = create_handshake("Robot Console v1.0")
            self._send_raw(handshake)

            # Start receive thread
            self._receive_thread = threading.Thread(
                target=self._receive_loop, daemon=True, name="TCP-Recv")
            self._receive_thread.start()

            # Start heartbeat
            self._heartbeat_timer.start()

            self.connected.emit()
            self.log_message.emit("INFO",
                f"Connected to {self._host}:{self._port}")
            self._emit_stats()

        except Exception as e:
            self._is_connected = False
            err = f"Connection failed: {e}"
            self.error_occurred.emit(err)
            self.log_message.emit("ERROR", err)
            if self._socket:
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None

    def disconnect_from_server(self):
        """Gracefully disconnect from the Surgeon Console."""
        if not self._is_connected:
            return

        self.log_message.emit("INFO", "Disconnecting...")
        self._running = False
        self._is_connected = False
        self._heartbeat_timer.stop()

        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        self.disconnected.emit()
        self.log_message.emit("INFO", "Disconnected")
        self._emit_stats()

    def send_data(self, msg_type: str, payload: dict):
        """Send a message to the Surgeon Console."""
        if not self._is_connected:
            return

        message = create_message(msg_type, payload)
        self._send_raw(message)

    def reconnect(self):
        """Attempt to reconnect to the server."""
        self.disconnect_from_server()
        self._reconnect_count += 1
        self.log_message.emit("INFO",
            f"Reconnect attempt #{self._reconnect_count}")
        # Delay before reconnecting
        QTimer.singleShot(2000, lambda: self.connect_to_server())

    def get_stats(self) -> dict:
        """Return current connection statistics."""
        return {
            "is_connected": self._is_connected,
            "remote_address": f"{self._host}:{self._port}",
            "packets_sent": self._packets_sent,
            "packets_received": self._packets_received,
            "bytes_sent": self._bytes_sent,
            "bytes_received": self._bytes_received,
            "errors": self._errors,
            "last_sent_time": self._last_sent_time,
            "last_received_time": self._last_received_time,
            "reconnect_count": self._reconnect_count,
        }

    # ─── Internal: Send ───────────────────────────────────────────

    def _send_raw(self, message: dict):
        """Encode and send a message over the socket."""
        if not self._socket or not self._is_connected:
            return

        try:
            data = encode_message(message)
            with self._lock:
                self._socket.sendall(data)

            self._packets_sent += 1
            self._bytes_sent += len(data)
            self._last_sent_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            # Don't emit for heartbeats (too noisy)
            if message.get("type") != MSG_HEARTBEAT:
                self.data_sent.emit(message)

            self._emit_stats()

        except Exception as e:
            self._errors += 1
            err = f"Send error: {e}"
            self.error_occurred.emit(err)
            self.log_message.emit("ERROR", err)
            self._handle_connection_lost()

    def _send_heartbeat(self):
        """Send a periodic heartbeat message."""
        if self._is_connected:
            self._send_raw(create_heartbeat())

    # ─── Internal: Receive Loop ───────────────────────────────────

    def _receive_loop(self):
        """Background thread: continuously receive data from server."""
        while self._running and self._socket:
            try:
                # Read the 4-byte header
                header = self._recv_exact(HEADER_SIZE)
                if header is None:
                    break

                # Decode payload length and read payload
                payload_len = decode_header(header)
                if payload_len > 10 * 1024 * 1024:  # 10 MB safety limit
                    self.log_message.emit("ERROR",
                        f"Payload too large: {payload_len} bytes")
                    continue

                payload_bytes = self._recv_exact(payload_len)
                if payload_bytes is None:
                    break

                message = decode_payload(payload_bytes)
                self._packets_received += 1
                self._bytes_received += HEADER_SIZE + payload_len
                self._last_received_time = (
                    datetime.now().strftime("%H:%M:%S.%f")[:-3])

                # Don't emit for heartbeats
                if message.get("type") != MSG_HEARTBEAT:
                    self.data_received.emit(message)

                self._emit_stats()

            except Exception as e:
                if self._running:
                    self._errors += 1
                    self.error_occurred.emit(f"Receive error: {e}")
                    self.log_message.emit("ERROR", f"Receive error: {e}")
                break

        # If we exit the loop while still supposed to be running,
        # the connection was lost
        if self._running:
            self._handle_connection_lost()

    def _recv_exact(self, num_bytes: int):
        """Read exactly num_bytes from the socket."""
        data = b""
        while len(data) < num_bytes:
            try:
                chunk = self._socket.recv(num_bytes - len(data))
                if not chunk:
                    return None  # connection closed
                data += chunk
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                return None
        return data

    # ─── Internal: Connection Management ──────────────────────────

    def _handle_connection_lost(self):
        """Handle an unexpected connection loss."""
        if not self._is_connected:
            return

        self._is_connected = False
        self._running = False
        self._heartbeat_timer.stop()

        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        self.disconnected.emit()
        self.log_message.emit("WARNING", "Connection lost")
        self._emit_stats()

    def _emit_stats(self):
        """Emit current stats to the UI."""
        self.stats_updated.emit(self.get_stats())
