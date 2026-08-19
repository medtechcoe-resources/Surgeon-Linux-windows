# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — PUB-SUB BROKER  (v2 — Secure)
#  Central TCP server that routes messages between clients.
#
#  Security (v2) additions:
#    - mTLS 1.3: all connections must present a valid device cert
#      signed by the Aether Local CA. No plaintext connections allowed.
#    - Broker is the security authority: it NEVER trusts client-
#      provided roles, usernames, or permissions.
#    - Authentication: device cert fingerprint → devices table →
#      session token → users/roles table (all server-side).
#    - RBAC: every publish and subscribe is checked against topic_acls.
#      Unauthorized actions are rejected and audited.
#    - Fail closed: missing cert / bad session / missing ACL → reject.
#    - Audit logging: all security events to SQLite audit_logs table.
#
#  Existing protocol PRESERVED:
#    - 4-byte length-prefixed JSON framing (unchanged)
#    - All existing topic names (unchanged)
#    - Heartbeat, reconnect, client list, handshake topics (unchanged)
# ═══════════════════════════════════════════════════════════════════

import socket
import threading
import logging
import time
from datetime import datetime

from shared_networking.config import (
    BROKER_HOST, BROKER_PORT, HEADER_SIZE, HEARTBEAT_TIMEOUT_S,
    CERTS_DIR, DATABASE_PATH,
    MAX_CLIENTS, RECV_CHUNK_TIMEOUT_S,
    DB_BACKUP_INTERVAL_MINUTES,
)
from shared_networking.protocol import (
    encode_message, decode_header, decode_payload, create_message,
    CTRL_SUBSCRIBE, CTRL_UNSUBSCRIBE, CTRL_HEARTBEAT,
    CTRL_HANDSHAKE, CTRL_CLIENT_LIST, CTRL_CLIENT_UPDATE,
    CTRL_AUTH_REJECT,
    get_message_class, get_class_limit,
)
from shared_networking.database import AetherDatabase
from shared_networking.tls import TLSManager
from shared_networking.logger import get_logger

log = get_logger("BROKER")


# ─── Uncaught Thread Exception Handler ────────────────────────────
def _thread_excepthook(args):
    """Log uncaught exceptions from any broker worker thread."""
    log.critical(
        f"Uncaught exception in thread '{args.thread.name}': "
        f"{args.exc_type.__name__}: {args.exc_value}"
    )


threading.excepthook = _thread_excepthook


class ClientInfo:
    """Tracks a connected client's state."""

    def __init__(self, conn: socket.socket, addr: tuple):
        self.conn = conn
        self.addr = addr
        self.name = f"{addr[0]}:{addr[1]}"
        self.subscriptions: set = set()
        self.publish_topics: list = []
        self.last_heartbeat = time.time()
        self.connect_time = datetime.now()
        self.packets_received = 0
        self.packets_sent = 0

        # Auth context (populated during handshake — all from DB, never client)
        self.username = ""
        self.role = ""          # Always DB-authoritative, never client-provided
        self.session_id = ""
        self.device_id = ""
        self.device_type = ""
        self.authenticated = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "address": f"{self.addr[0]}:{self.addr[1]}",
            "subscriptions": list(self.subscriptions),
            "publish_topics": self.publish_topics,
            "connected_since": self.connect_time.isoformat(),
            "username": self.username,
            "role": self.role,
            "device_id": self.device_id,
            "authenticated": self.authenticated,
        }


class PubSubBroker:
    """TCP Pub-Sub Broker with mTLS and server-side RBAC.

    Architecture:
    - One accept thread for new connections (mTLS).
    - One receive thread per connected client.
    - Lock-guarded send to any client.
    - Heartbeat monitor thread checks for dead clients.
    - RBAC checked on every publish/subscribe operation.
    - Audit log written to SQLite on every security event.
    """

    def __init__(self, host: str = None, port: int = None):
        self._host = host or BROKER_HOST
        self._port = port or BROKER_PORT
        self._server_socket: socket.socket = None
        self._running = False
        self._lock = threading.Lock()

        # Client registry: socket fd → ClientInfo
        self._clients: dict[int, ClientInfo] = {}

        # Database (shared singleton)
        self._db = AetherDatabase.instance()

        # TLS manager
        self._tls = TLSManager(CERTS_DIR)

    # ─── Public API ───────────────────────────────────────────────

    def start(self):
        """Start the broker server with mTLS."""
        # Ensure database is open
        if not self._db.is_ready:
            if not self._db.open(DATABASE_PATH):
                raise RuntimeError("Broker: Failed to open security database")

        # Build TLS server context (mTLS — requires client certs)
        try:
            tls_ctx = self._tls.create_server_context()
        except RuntimeError as e:
            raise RuntimeError(
                f"Broker: TLS not ready — {e}\n"
                "Run 'python -m shared_networking.provisioning' first."
            ) from e

        # Create raw server socket and wrap with TLS
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw_sock.bind((self._host, self._port))
        raw_sock.listen(10)
        self._server_socket = tls_ctx.wrap_socket(
            raw_sock, server_side=True)
        self._running = True

        log.info(f"Broker started on {self._host}:{self._port} (mTLS 1.3)")
        if not self._host.startswith("127.") and self._host not in ("localhost", ""):
            log.warning(
                f"[SECURITY] Broker bound to non-loopback address {self._host}. "
                "Ensure network access is restricted to trusted local LAN only."
            )
        print("=" * 60)
        print("  AETHER PUB-SUB BROKER  (Secure)")
        print(f"  Listening on {self._host}:{self._port}")
        print("  Transport: mTLS 1.3")
        print("  Auth: SQLite + device certs")
        print("=" * 60)

        # Start heartbeat monitor
        monitor = threading.Thread(
            target=self._heartbeat_monitor, daemon=True,
            name="Broker-HeartbeatMonitor")
        monitor.start()

        # Accept loop (blocking)
        self._accept_loop()

    def stop(self):
        """Stop the broker cleanly.

        Snapshots the client socket list before closing to avoid a
        lock-contention deadlock with _remove_client (which also
        acquires self._lock via the recv thread's OSError path).
        """
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass

        # Snapshot sockets outside the lock, then close them without
        # re-acquiring the lock (avoids deadlock with _remove_client).
        with self._lock:
            client_sockets = [c.conn for c in self._clients.values()]
            self._clients.clear()

        for sock in client_sockets:
            try:
                sock.close()
            except Exception:
                pass

        # WAL checkpoint as normal maintenance on clean shutdown.
        try:
            self._db.checkpoint()
        except Exception:
            pass

        log.info("Broker stopped")

    # ─── Accept Loop ──────────────────────────────────────────────

    def _accept_loop(self):
        """Accept incoming TLS client connections."""
        while self._running:
            try:
                conn, addr = self._server_socket.accept()

                # ── Max-client limit ────────────────────────────────
                with self._lock:
                    current_count = len(self._clients)
                if current_count >= MAX_CLIENTS:
                    log.warning(
                        f"[SECURITY] Rejected connection from {addr} — "
                        f"max clients reached ({MAX_CLIENTS})"
                    )
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue

                log.info(f"New TLS connection from {addr}")
                print(f"  [+] Client connected: {addr[0]}:{addr[1]}")

                client = ClientInfo(conn, addr)

                # Extract client certificate fingerprint from the TLS handshake
                peer_fp = TLSManager.get_peer_fingerprint(conn)
                if not peer_fp:
                    # No client cert → reject immediately (fail closed)
                    log.warning(f"[SECURITY] Rejected {addr} — no client certificate")
                    self._db.audit(
                        "DEVICE_AUTH_FAILED",
                        ip_address=str(addr[0]),
                        details="no client certificate presented")
                    conn.close()
                    continue

                # Verify device via fingerprint
                ok, device_id, device_type = self._db.verify_device(peer_fp)
                if not ok:
                    log.warning(
                        f"[SECURITY] Rejected {addr} — "
                        f"unregistered/revoked device (fp={peer_fp[:16]}...)")
                    self._db.audit(
                        "DEVICE_AUTH_FAILED",
                        ip_address=str(addr[0]),
                        details=f"unknown fingerprint {peer_fp[:16]}")
                    self._send_auth_reject(conn, "Unregistered or revoked device")
                    conn.close()
                    continue

                client.device_id = device_id
                client.device_type = device_type
                log.info(f"Device verified: {device_id} ({device_type})")

                with self._lock:
                    self._clients[conn.fileno()] = client

                # Start client handler thread
                t = threading.Thread(
                    target=self._client_handler,
                    args=(client,), daemon=True,
                    name=f"Broker-Client-{device_id}")
                t.start()

            except OSError:
                if self._running:
                    log.error("Accept error")
                break

    # ─── Client Handler ───────────────────────────────────────────

    def _client_handler(self, client: ClientInfo):
        """Handle messages from a single authenticated client."""
        while self._running:
            try:
                header = self._recv_exact(client.conn, HEADER_SIZE)
                if header is None:
                    break

                payload_len = decode_header(header)
                payload_bytes = self._recv_exact(client.conn, payload_len)
                if payload_bytes is None:
                    break

                message = decode_payload(payload_bytes)
                client.packets_received += 1
                topic = message.get("topic", "")

                # ── Post-decode per-class size enforcement ───────────
                # The protocol encodes the topic inside the JSON, so class
                # cannot be determined before reading the payload. We enforce
                # class limits here, after decode, and disconnect on violation.
                class_limit = get_class_limit(topic)
                if payload_len > class_limit:
                    msg_class = get_message_class(topic)
                    log.warning(
                        f"[SECURITY] Oversized {msg_class} payload from "
                        f"{client.name}: {payload_len} bytes > "
                        f"{class_limit} bytes limit for topic '{topic}'. "
                        f"Disconnecting client."
                    )
                    self._db.audit(
                        "OVERSIZED_PAYLOAD",
                        username=client.username,
                        device_id=client.device_id,
                        details=(
                            f"topic={topic} class={msg_class} "
                            f"size={payload_len} limit={class_limit}"
                        ),
                    )
                    break  # Disconnect

                # Route by message type
                if topic == CTRL_HANDSHAKE:
                    self._handle_handshake(client, message)
                elif topic == CTRL_SUBSCRIBE:
                    self._handle_subscribe(client, message)
                elif topic == CTRL_UNSUBSCRIBE:
                    self._handle_unsubscribe(client, message)
                elif topic == CTRL_HEARTBEAT:
                    client.last_heartbeat = time.time()
                elif topic == CTRL_CLIENT_LIST:
                    self._handle_client_list_request(client)
                else:
                    # Data message — RBAC check then route to subscribers
                    self._handle_publish(client, message, topic)

            except Exception as e:
                if self._running:
                    log.warning(f"Error from {client.name}: {e}")
                break

        # Client disconnected
        self._remove_client(client)

    # ─── Handshake & Authentication ───────────────────────────────

    def _handle_handshake(self, client: ClientInfo, message: dict):
        """Process a client handshake.

        Security rules:
          1. Device identity comes from the mTLS cert (already verified).
          2. Session token is validated against the DB.
          3. Role is ALWAYS read from the DB — never from the client.
          4. Unauthenticated or invalid session → reject & disconnect.
        """
        payload = message.get("payload", {})
        client.name = payload.get("client_name", client.name)
        requested_pub_topics = payload.get("publish_topics", [])
        requested_sub_topics = payload.get("subscribe_topics", [])

        # Client-provided username (informational — must be confirmed by session)
        client_username = payload.get("username", "")
        client_session_id = payload.get("session_id", "")

        # For service accounts (data_generator, robot_console, observer_screen)
        # that connect without a user login, grant role based on device_type
        SERVICE_ROLES = {
            "data_generator": "data_generator",
            "robot":          "robot_console",
            "observer":       "observer",
        }

        if client.device_type in SERVICE_ROLES:
            # Service device — no user session needed
            client.role = SERVICE_ROLES[client.device_type]
            client.username = client.device_id
            client.authenticated = True
            log.info(f"Service device authenticated: {client.device_id} "
                     f"(role={client.role})")
            self._db.audit(
                "HANDSHAKE_SUCCESS",
                username=client.device_id,
                device_id=client.device_id,
                details=f"service role={client.role}")
        elif client_username and client_session_id:
            # Human operator — validate session against DB with device binding.
            # The broker-verified device_id from the mTLS cert is passed so
            # the DB can enforce session/device binding (recommended, not a
            # blocker — sessions with NULL device_id are accepted for compat).
            valid, db_username, db_role = self._db.validate_session(
                client_session_id, device_id=client.device_id)
            if not valid:
                log.warning(
                    f"[SECURITY] Rejected handshake from {client.name} — "
                    f"invalid/expired session")
                self._db.audit(
                    "HANDSHAKE_REJECTED",
                    username=client_username,
                    device_id=client.device_id,
                    details="invalid or expired session")
                self._send_auth_reject(client.conn, "Invalid or expired session")
                self._remove_client(client)
                return

            # Role is ALWAYS from the DB — never the client-provided value
            client.username = db_username
            client.role = db_role   # authoritative
            client.session_id = client_session_id
            client.authenticated = True
            log.info(
                f"Authenticated: {client.name} "
                f"(user={db_username}, role={db_role})")
            self._db.audit(
                "HANDSHAKE_SUCCESS",
                username=db_username,
                device_id=client.device_id,
                details=f"role={db_role}")
        else:
            # No credentials — reject (fail closed)
            log.warning(
                f"[SECURITY] Rejected {client.name} — no auth credentials")
            self._db.audit(
                "HANDSHAKE_REJECTED",
                device_id=client.device_id,
                details="no credentials provided")
            self._send_auth_reject(client.conn, "Authentication required")
            self._remove_client(client)
            return

        # Apply ACL-filtered subscriptions
        for topic in requested_pub_topics:
            if self._db.check_acl(client.role, topic, "publish"):
                client.publish_topics.append(topic)
        for topic in requested_sub_topics:
            if self._db.check_acl(client.role, topic, "subscribe"):
                client.subscriptions.add(topic)

        log.info(
            f"Handshake from '{client.name}' — "
            f"pub={client.publish_topics}, "
            f"sub={list(client.subscriptions)}")

        # Broadcast updated client list
        self._broadcast_client_update()

    # ─── Message Routing ──────────────────────────────────────────

    def _handle_publish(self, sender: ClientInfo, message: dict,
                        topic: str):
        """Route a published message after RBAC check."""
        if not sender.authenticated:
            log.warning(
                f"[SECURITY] Unauthenticated publish attempt on '{topic}' "
                f"from {sender.name}")
            self._db.audit(
                "UNAUTHORIZED_PUBLISH",
                username=sender.username,
                device_id=sender.device_id,
                details=f"topic={topic} (not authenticated)")
            return

        if not self._db.check_acl(sender.role, topic, "publish"):
            log.warning(
                f"[SECURITY] RBAC denied publish on '{topic}' "
                f"for role '{sender.role}' ({sender.name})")
            self._db.audit(
                "UNAUTHORIZED_PUBLISH",
                username=sender.username,
                device_id=sender.device_id,
                details=f"topic={topic} role={sender.role}")
            return

        # Permitted — route to subscribers
        self._route_message(sender, message, topic)

    def _route_message(self, sender: ClientInfo, message: dict,
                       topic: str):
        """Route a permitted message to all subscribers of the topic."""
        data = encode_message(message)

        subscribers_count = 0
        with self._lock:
            for fd, client in list(self._clients.items()):
                if client is sender:
                    continue
                if topic in client.subscriptions:
                    try:
                        client.conn.sendall(data)
                        client.packets_sent += 1
                        subscribers_count += 1
                    except Exception as e:
                        log.debug(f"Send failed to '{client.name}' on topic '{topic}': {e}")
                        # Dead client will be cleaned up by the heartbeat monitor
        log.debug(
            f"Routed '{topic}' from '{sender.name}' "
            f"to {subscribers_count} subscribers")

    # ─── Control Message Handlers ─────────────────────────────────

    def _handle_subscribe(self, client: ClientInfo, message: dict):
        """Process a subscription request with RBAC check."""
        if not client.authenticated:
            log.warning(
                f"[SECURITY] Unauthenticated subscribe attempt from {client.name} "
                f"(device={client.device_id})"
            )
            self._db.audit(
                "UNAUTHORIZED_SUBSCRIBE",
                device_id=client.device_id,
                details="subscribe attempt before authentication")
            return

        topics = message.get("payload", {}).get("topics", [])
        permitted = []
        denied = []
        for topic in topics:
            if self._db.check_acl(client.role, topic, "subscribe"):
                client.subscriptions.add(topic)
                permitted.append(topic)
            else:
                denied.append(topic)
                self._db.audit(
                    "UNAUTHORIZED_SUBSCRIBE",
                    username=client.username,
                    device_id=client.device_id,
                    details=f"topic={topic} role={client.role}")

        if permitted:
            log.info(f"'{client.name}' subscribed to {permitted}")
        if denied:
            log.warning(
                f"[SECURITY] Denied subscription for {client.name}: {denied}")

    def _handle_unsubscribe(self, client: ClientInfo, message: dict):
        """Process an unsubscription request."""
        if not client.authenticated:
            log.warning(
                f"[SECURITY] Unauthenticated unsubscribe attempt from {client.name} "
                f"(device={client.device_id})"
            )
            return
        topics = message.get("payload", {}).get("topics", [])
        client.subscriptions -= set(topics)
        log.info(f"'{client.name}' unsubscribed from {topics}")

    def _handle_client_list_request(self, client: ClientInfo):
        """Send the list of connected clients to the requester.

        Requires the client to be fully authenticated. An unauthenticated
        client requesting the list would receive usernames, roles, and
        device IDs for all active connections.
        """
        if not client.authenticated:
            log.warning(
                f"[SECURITY] Unauthenticated client-list request from "
                f"{client.name} (device={client.device_id})"
            )
            self._db.audit(
                "UNAUTHORIZED_CLIENT_LIST",
                device_id=client.device_id,
                details="client-list request before authentication")
            return

        clients_data = []
        with self._lock:
            for fd, c in self._clients.items():
                clients_data.append(c.to_dict())

        response = create_message(CTRL_CLIENT_LIST, "broker", {
            "clients": clients_data,
        })
        try:
            client.conn.sendall(encode_message(response))
        except Exception as e:
            log.debug(f"Send failed (client-list) to '{client.name}': {e}")

    # ─── Client Management ────────────────────────────────────────

    def _remove_client(self, client: ClientInfo):
        """Remove a disconnected or rejected client."""
        with self._lock:
            fd = None
            for f, c in self._clients.items():
                if c is client:
                    fd = f
                    break
            if fd is not None:
                del self._clients[fd]

        try:
            client.conn.close()
        except Exception:
            pass

        log.info(f"Client disconnected: {client.name} "
                 f"(user={client.username}, device={client.device_id})")
        print(f"  [-] Client disconnected: {client.name}")

        # Invalidate session (only for authenticated human operator sessions)
        if client.session_id:
            self._db.invalidate_session(client.session_id)

        # Only audit DEVICE_DISCONNECTED for clients that completed handshake.
        # Pre-auth rejections are already audited at DEVICE_AUTH_FAILED /
        # HANDSHAKE_REJECTED — writing DEVICE_DISCONNECTED for them would
        # produce confusing log entries with empty username/device.
        if client.authenticated:
            self._db.audit(
                "DEVICE_DISCONNECTED",
                username=client.username,
                device_id=client.device_id,
            )

        # Broadcast updated client list
        self._broadcast_client_update()

        # Broadcast connection_status to inform subscribers
        status_msg = create_message("connection_status", "broker", {
            "event": "client_disconnected",
            "client_name": client.name,
            "timestamp": datetime.now().isoformat(),
        })
        self._broadcast_to_topic("connection_status", status_msg)

    def _broadcast_client_update(self):
        """Broadcast updated client list to all connected clients."""
        clients_data = []
        with self._lock:
            for fd, c in self._clients.items():
                clients_data.append(c.to_dict())

        msg = create_message(CTRL_CLIENT_UPDATE, "broker", {
            "clients": clients_data,
        })
        data = encode_message(msg)

        with self._lock:
            for fd, client in list(self._clients.items()):
                try:
                    client.conn.sendall(data)
                    client.packets_sent += 1
                except Exception as e:
                    log.debug(f"Send failed (client-update) to '{client.name}': {e}")

    def _broadcast_to_topic(self, topic: str, message: dict):
        """Send a message to all subscribers of a topic (no RBAC — broker-generated)."""
        data = encode_message(message)
        with self._lock:
            for fd, client in list(self._clients.items()):
                if topic in client.subscriptions:
                    try:
                        client.conn.sendall(data)
                        client.packets_sent += 1
                    except Exception as e:
                        log.debug(f"Send failed (broadcast '{topic}') to '{client.name}': {e}")

    # ─── Auth Reject ──────────────────────────────────────────────

    def _send_auth_reject(self, conn: socket.socket, reason: str):
        """Send an _auth_reject control message then close the connection."""
        try:
            msg = create_message(CTRL_AUTH_REJECT, "broker", {
                "reason": reason,
            })
            conn.sendall(encode_message(msg))
        except Exception:
            pass

    # ─── Heartbeat Monitor ────────────────────────────────────────

    def _heartbeat_monitor(self):
        """Periodically check for dead clients and clean expired sessions."""
        _last_backup_time = time.time()
        _backup_interval_s = DB_BACKUP_INTERVAL_MINUTES * 60

        while self._running:
            time.sleep(HEARTBEAT_TIMEOUT_S)
            now = time.time()
            dead = []

            with self._lock:
                for fd, client in list(self._clients.items()):
                    if now - client.last_heartbeat > HEARTBEAT_TIMEOUT_S:
                        dead.append(client)

            for client in dead:
                log.warning(f"Heartbeat timeout for '{client.name}'")
                print(f"  [!] Heartbeat timeout: {client.name}")
                self._remove_client(client)

            # Periodic DB maintenance (non-blocking, runs on monitor thread)
            try:
                self._db.cleanup_expired_sessions()
            except Exception as e:
                log.error(f"[DB] cleanup_expired_sessions failed: {e}")

            # Periodic auto-backup + WAL checkpoint
            if now - _last_backup_time >= _backup_interval_s:
                _last_backup_time = now
                try:
                    self._db.auto_backup()
                    self._db.checkpoint()
                except Exception as e:
                    log.error(f"[DB] Periodic backup/checkpoint failed: {e}")

    # ─── Utility ──────────────────────────────────────────────────

    @staticmethod
    def _recv_exact(conn: socket.socket, num_bytes: int):
        """Read exactly num_bytes from a socket into a pre-allocated bytearray.

        Uses a per-chunk socket timeout (RECV_CHUNK_TIMEOUT_S) to prevent
        a slow or stalling client from holding the broker's receive thread
        indefinitely. The timeout is reset to blocking mode after the
        receive completes so the connection can wait normally for the
        next message header.

        Returns bytes on success, or None if the connection was closed
        or a network error occurred.
        """
        if num_bytes == 0:
            return b""

        buf = bytearray(num_bytes)
        view = memoryview(buf)
        received = 0

        try:
            conn.settimeout(RECV_CHUNK_TIMEOUT_S)
            while received < num_bytes:
                try:
                    n = conn.recv_into(view[received:], num_bytes - received)
                    if not n:
                        return None  # Connection closed cleanly
                    received += n
                except (ConnectionResetError, ConnectionAbortedError, OSError):
                    return None
        finally:
            # Always restore blocking mode, even if an exception occurs
            try:
                conn.settimeout(None)
            except OSError:
                pass

        return bytes(buf)
