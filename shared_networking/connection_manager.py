# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — CONNECTION MANAGER
#  Manages the TCP socket lifecycle for pub-sub clients.
#  Handles connect, disconnect, auto-reconnect, heartbeat,
#  background receive thread, and packet statistics.
#
#  Security (v2):
#    All transport-level encryption is TLS 1.3 at the socket layer.
#    Application-level Fernet encryption has been removed.
#    The client wraps its socket with a device-specific TLS context
#    before sending any data. Certificate verification is always on.
# ═══════════════════════════════════════════════════════════════════

import socket
import ssl
import time
import threading
import logging
from datetime import datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from shared_networking.config import (
    BROKER_HOST, BROKER_PORT,
    HEARTBEAT_INTERVAL_S, RECONNECT_INTERVAL_S, MAX_RECONNECT_ATTEMPTS,
    HEADER_SIZE, CERTS_DIR,
)
from shared_networking.protocol import (
    encode_message_full, decode_header, decode_payload_full,
    create_heartbeat, create_handshake, create_subscribe,
    create_unsubscribe, create_client_list_request,
    CTRL_HEARTBEAT, CTRL_CLIENT_UPDATE, CTRL_CLIENT_LIST,
    CTRL_AUTH_REJECT,
    is_control_message,
)
from shared_networking.tls import TLSManager

log = logging.getLogger(__name__)


def _get_tls_context(client_name: str) -> ssl.SSLContext:
    """Return a strict TLS 1.3 client context for the named device.

    Raises RuntimeError if the device certificate is missing (not yet
    provisioned) so the connection attempt fails with a clear error
    instead of silently connecting without encryption.
    """
    tls = TLSManager(CERTS_DIR)
    return tls.create_client_context(client_name)


class ConnectionManager(QObject):
    """Manages TCP connection to the pub-sub broker.

    Provides publish/subscribe functionality with automatic reconnect,
    heartbeat, packet statistics, and encryption diagnostics.
    All UI-relevant events are emitted as Qt signals for thread-safe updates.
    """

    # ─── Signals ──────────────────────────────────────────────────
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str, str)          # (level, message)
    stats_updated = pyqtSignal(dict)
    message_received = pyqtSignal(str, dict)    # (topic, full_message)
    raw_data_sent = pyqtSignal(dict, bytes, bytes)      # (message, plaintext, encrypted)
    raw_data_received = pyqtSignal(dict, bytes, bytes)  # (message, plaintext, encrypted)
    client_list_received = pyqtSignal(list)     # list of client dicts

    # Private signals for thread safety
    _start_timers_sig = pyqtSignal()
    _stop_timers_sig = pyqtSignal()
    _start_reconnect_sig = pyqtSignal()

    def __init__(self, client_name: str, publish_topics: list = None,
                 subscribe_topics: list = None,
                 username: str = "", role: str = "",
                 session_id: str = "", parent=None):
        super().__init__(parent)
        self._client_name = client_name
        self._publish_topics = publish_topics or []
        self._subscribe_topics = subscribe_topics or []

        # Auth context
        self._username = username
        self._role = role
        self._session_id = session_id

        self._socket: socket.socket = None
        self._is_connected = False
        self._host = BROKER_HOST
        self._port = BROKER_PORT
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
        self._connect_time = None

        # Background threads
        self._receive_thread: threading.Thread = None
        self._running = False

        # Heartbeat timer
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(int(HEARTBEAT_INTERVAL_S * 1000))
        self._heartbeat_timer.timeout.connect(self._send_heartbeat)

        # Auto-reconnect timer
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.setInterval(int(RECONNECT_INTERVAL_S * 1000))
        self._reconnect_timer.timeout.connect(self._auto_reconnect)
        self._auto_reconnect_enabled = True

        # Data rate tracking
        self._prev_bytes_sent = 0
        self._prev_bytes_received = 0
        self._prev_rate_time = time.time()
        self._data_rate_in = 0.0
        self._data_rate_out = 0.0

        self._rate_timer = QTimer(self)
        self._rate_timer.setInterval(1000)
        self._rate_timer.timeout.connect(self._calculate_rates)

        # Connect private signals
        self._start_timers_sig.connect(self._on_start_timers)
        self._stop_timers_sig.connect(self._on_stop_timers)
        self._start_reconnect_sig.connect(self._on_start_reconnect)

    def _on_start_timers(self):
        self._heartbeat_timer.start()
        self._rate_timer.start()

    def _on_stop_timers(self):
        self._heartbeat_timer.stop()
        self._rate_timer.stop()

    def _on_start_reconnect(self):
        self._reconnect_timer.start()

    # ─── Properties ───────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def client_name(self) -> str:
        return self._client_name

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def username(self) -> str:
        return self._username

    @property
    def role(self) -> str:
        return self._role

    @property
    def session_id(self) -> str:
        return self._session_id

    # ─── Auth Context ─────────────────────────────────────────────

    def set_auth_context(self, username: str, role: str, session_id: str):
        """Set authentication context for handshake messages."""
        self._username = username
        self._role = role
        self._session_id = session_id

    # ─── Public API ───────────────────────────────────────────────

    def connect_to_broker(self, host: str = None, port: int = None):
        """Connect to the pub-sub broker."""
        if self._is_connected:
            self.log_message.emit("WARNING", "Already connected")
            return

        if host:
            self._host = host
        if port:
            self._port = port

        self.log_message.emit("INFO",
                              f"Connecting to broker {self._host}:{self._port}...")

        try:
            # Build raw TCP socket and wrap with TLS 1.3 before any data flows
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(5.0)

            tls_ctx = _get_tls_context(self._client_name)
            self._socket = tls_ctx.wrap_socket(
                raw_sock, server_hostname=None)
            self._socket.connect((self._host, self._port))
            self._socket.settimeout(None)

            self._is_connected = True
            self._running = True
            self._connect_time = datetime.now()

            # Send handshake with auth context
            # NOTE: broker never trusts client-provided role
            handshake = create_handshake(
                self._client_name,
                self._publish_topics,
                self._subscribe_topics,
                username=self._username,
                role=self._role,
                session_id=self._session_id,
            )
            self._send_raw(handshake)

            # Subscribe to topics
            if self._subscribe_topics:
                sub_msg = create_subscribe(
                    self._client_name, self._subscribe_topics)
                self._send_raw(sub_msg)

            # Start receive thread
            self._receive_thread = threading.Thread(
                target=self._receive_loop, daemon=True,
                name=f"PubSub-Recv-{self._client_name}")
            self._receive_thread.start()

            # Start heartbeat and rate timers (thread-safe)
            self._start_timers_sig.emit()

            self.connected.emit()
            self.log_message.emit("INFO",
                                  f"Connected to broker {self._host}:{self._port} (TLS 1.3)")
            self._emit_stats()

        except Exception as e:
            self._is_connected = False
            err = f"Connection failed: {e}"
            self.error_occurred.emit(err)
            self.log_message.emit("ERROR", err)
            self._errors += 1
            if self._socket:
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None

            # Schedule auto-reconnect
            if self._auto_reconnect_enabled:
                self._schedule_reconnect()

    def disconnect_from_broker(self):
        """Gracefully disconnect from the broker."""
        self._auto_reconnect_enabled = False
        self._reconnect_timer.stop()
        self._do_disconnect()
        self.log_message.emit("INFO", "Disconnected from broker")

    def restart_connection(self):
        """Disconnect and reconnect to the broker."""
        self.log_message.emit("INFO", "Restarting communication...")
        was_auto = self._auto_reconnect_enabled
        self.disconnect_from_broker()
        self._auto_reconnect_enabled = was_auto
        QTimer.singleShot(1000, lambda: self.connect_to_broker())

    def publish(self, topic: str, payload: dict):
        """Publish a message to a topic."""
        if not self._is_connected:
            return
        from shared_networking.protocol import create_message
        message = create_message(topic, self._client_name, payload)
        self._send_raw(message)

    def subscribe(self, topics: list):
        """Subscribe to additional topics."""
        new_topics = [t for t in topics if t not in self._subscribe_topics]
        if not new_topics:
            return
        self._subscribe_topics.extend(new_topics)
        if self._is_connected:
            msg = create_subscribe(self._client_name, new_topics)
            self._send_raw(msg)

    def unsubscribe(self, topics: list):
        """Unsubscribe from topics."""
        for t in topics:
            if t in self._subscribe_topics:
                self._subscribe_topics.remove(t)
        if self._is_connected:
            msg = create_unsubscribe(self._client_name, topics)
            self._send_raw(msg)

    def request_client_list(self):
        """Request the list of connected clients from the broker."""
        if self._is_connected:
            self._send_raw(create_client_list_request(self._client_name))

    def enable_auto_reconnect(self, enabled: bool = True):
        """Enable or disable auto-reconnect."""
        self._auto_reconnect_enabled = enabled

    def get_stats(self) -> dict:
        """Return current connection statistics."""
        uptime = "---"
        if self._connect_time and self._is_connected:
            delta = datetime.now() - self._connect_time
            mins, secs = divmod(int(delta.total_seconds()), 60)
            hours, mins = divmod(mins, 60)
            uptime = f"{hours:02d}:{mins:02d}:{secs:02d}"

        # Detect active TLS version from the live socket
        tls_version = "---"
        if self._socket and self._is_connected:
            try:
                tls_version = self._socket.version() or "---"
            except Exception:
                pass

        return {
            "is_connected": self._is_connected,
            "client_name": self._client_name,
            "remote_address": f"{self._host}:{self._port}",
            "packets_sent": self._packets_sent,
            "packets_received": self._packets_received,
            "bytes_sent": self._bytes_sent,
            "bytes_received": self._bytes_received,
            "errors": self._errors,
            "last_sent_time": self._last_sent_time,
            "last_received_time": self._last_received_time,
            "reconnect_count": self._reconnect_count,
            "data_rate_in": round(self._data_rate_in, 1),
            "data_rate_out": round(self._data_rate_out, 1),
            "uptime": uptime,
            "publish_topics": self._publish_topics,
            "subscribe_topics": self._subscribe_topics,
            # Auth context
            "username": self._username,
            "role": self._role,
            "session_id": self._session_id,
            # Transport security
            "encryption_enabled": True,
            "encryption_algorithm": f"TLS ({tls_version})",
            "encryption_errors": 0,
            "decryption_errors": 0,
        }

    def cleanup(self):
        """Stop all timers and disconnect. Call on application shutdown."""
        self._auto_reconnect_enabled = False
        self._reconnect_timer.stop()
        self._heartbeat_timer.stop()
        self._rate_timer.stop()
        self._do_disconnect()

    # ─── Internal: Send ───────────────────────────────────────────

    def _send_raw(self, message: dict):
        """Encode and send a message over the socket."""
        if not self._socket or not self._is_connected:
            return

        try:
            data, plaintext_bytes, encrypted_bytes = encode_message_full(message)
            with self._lock:
                self._socket.sendall(data)
                
            self.raw_data_sent.emit(message, plaintext_bytes, encrypted_bytes)

            self._packets_sent += 1
            self._bytes_sent += len(data)
            self._last_sent_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        except Exception as e:
            self._errors += 1
            err = f"Send error: {e}"
            self.error_occurred.emit(err)
            self.log_message.emit("ERROR", err)
            self._handle_connection_lost()

    def _send_heartbeat(self):
        """Send a periodic heartbeat to the broker."""
        if self._is_connected:
            self._send_raw(create_heartbeat(self._client_name))

    # ─── Internal: Receive Loop ───────────────────────────────────

    def _receive_loop(self):
        """Background thread: continuously receive data from broker."""
        while self._running and self._socket:
            try:
                header = self._recv_exact(self._socket, HEADER_SIZE)
                if header is None:
                    break

                payload_len = decode_header(header)
                payload_bytes = self._recv_exact(self._socket, payload_len)
                if payload_bytes is None:
                    break

                message, plaintext_bytes, encrypted_bytes = decode_payload_full(payload_bytes)
                self.raw_data_received.emit(message, plaintext_bytes, encrypted_bytes)
                self._packets_received += 1
                self._bytes_received += HEADER_SIZE + payload_len
                self._last_received_time = (
                    datetime.now().strftime("%H:%M:%S.%f")[:-3])

                topic = message.get("topic", "")

                # Route control messages
                if topic == CTRL_HEARTBEAT:
                    continue
                elif topic == CTRL_CLIENT_LIST or topic == CTRL_CLIENT_UPDATE:
                    clients = message.get("payload", {}).get("clients", [])
                    self.client_list_received.emit(clients)
                    continue
                elif topic == CTRL_AUTH_REJECT:
                    reason = message.get("payload", {}).get("reason", "Auth rejected")
                    self.log_message.emit("ERROR", f"Broker rejected connection: {reason}")
                    self.error_occurred.emit(f"Auth rejected: {reason}")
                    # Disconnect — do not reconnect on auth rejection
                    self._auto_reconnect_enabled = False
                    break

                # Emit data message
                self.message_received.emit(topic, message)

            except Exception as e:
                if self._running:
                    self._errors += 1
                    self.error_occurred.emit(f"Receive error: {e}")
                    self.log_message.emit("ERROR", f"Receive error: {e}")
                break

        if self._running:
            self._handle_connection_lost()

    @staticmethod
    def _recv_exact(conn, num_bytes: int):
        """Read exactly num_bytes from a socket.

        Uses a pre-allocated bytearray + memoryview to avoid O(n²)
        copying that occurs with bytes concatenation for large payloads.
        Returns bytes on success, None if the connection was closed.
        """
        if num_bytes == 0:
            return b""
        buf = bytearray(num_bytes)
        view = memoryview(buf)
        received = 0
        while received < num_bytes:
            try:
                n = conn.recv_into(view[received:], num_bytes - received)
                if not n:
                    return None
                received += n
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                return None
        return bytes(buf)

    # ─── Internal: Connection Management ──────────────────────────

    def _log_warning(self, msg: str):
        """Emit a warning log message."""
        self.log_message.emit("WARNING", msg)

    def _do_disconnect(self):
        """Internal disconnect without logging or auto-reconnect logic.

        Sequences: set _running=False → close socket → join receive thread.
        Joining the receive thread (with a timeout) ensures the thread
        exits cleanly and does not become a zombie on shutdown.
        """
        was_connected = self._is_connected
        self._running = False
        self._is_connected = False
        self._stop_timers_sig.emit()

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

        # Join the receive thread so it does not linger after disconnect.
        # Use a timeout to avoid blocking the caller indefinitely if the
        # thread is stuck in a kernel call that doesn't respond to socket close.
        if self._receive_thread and self._receive_thread.is_alive():
            self._receive_thread.join(timeout=3.0)
            if self._receive_thread.is_alive():
                self._log_warning("Receive thread did not exit cleanly after disconnect")
        self._receive_thread = None

        if was_connected:
            self.disconnected.emit()
            self._emit_stats()

    def _handle_connection_lost(self):
        """Handle unexpected connection loss."""
        if not self._is_connected:
            return

        self._do_disconnect()
        self.log_message.emit("WARNING", "Connection lost")

        if self._auto_reconnect_enabled:
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        """Schedule an auto-reconnect attempt."""
        if MAX_RECONNECT_ATTEMPTS > 0 and \
                self._reconnect_count >= MAX_RECONNECT_ATTEMPTS:
            self.log_message.emit("ERROR",
                                  "Max reconnect attempts reached")
            return
        self.log_message.emit("INFO",
                              f"Reconnecting in {RECONNECT_INTERVAL_S}s...")
        self._start_reconnect_sig.emit()

    def _auto_reconnect(self):
        """Auto-reconnect attempt."""
        self._reconnect_count += 1
        self.log_message.emit("INFO",
                              f"Reconnect attempt #{self._reconnect_count}")
        self.connect_to_broker()

    def _calculate_rates(self):
        """Calculate data rates."""
        now = time.time()
        elapsed = now - self._prev_rate_time
        if elapsed <= 0:
            return

        self._data_rate_out = (
            self._bytes_sent - self._prev_bytes_sent) / elapsed
        self._data_rate_in = (
            self._bytes_received - self._prev_bytes_received) / elapsed

        self._prev_bytes_sent = self._bytes_sent
        self._prev_bytes_received = self._bytes_received
        self._prev_rate_time = now

        self._emit_stats()

    def _emit_stats(self):
        """Emit current stats to the UI."""
        self.stats_updated.emit(self.get_stats())
