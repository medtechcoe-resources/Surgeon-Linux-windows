"""Tests for PubSubBroker security, fine-grained locking, concurrent socket writes, and session preservation."""
import sys
import os
import socket
import threading
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared_networking.broker import PubSubBroker, ClientInfo
from shared_networking.database import AetherDatabase
from shared_networking.protocol import (
    create_handshake, create_message,
    encode_message, decode_payload, CTRL_HANDSHAKE, CTRL_SUBSCRIBE,
    CTRL_CLIENT_LIST, CTRL_LOGOUT,
)


@pytest.fixture
def test_broker():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = AetherDatabase()
    db._ready = False
    db._path = None
    db.open(db_path)
    db.create_user("surgeon", "SurgicalPass123", "user")

    broker = PubSubBroker(host="127.0.0.1", port=5099)
    broker._db = db

    yield broker, db, db_path

    try:
        os.remove(db_path)
    except Exception:
        pass


class TestBrokerSecurity:
    def test_client_info_send_bytes_synchronization(self):
        """Test concurrent multi-threaded writes through ClientInfo.send_bytes."""
        server_sock, client_sock = socket.socketpair()

        client = ClientInfo(client_sock, ("127.0.0.1", 50000))
        test_payload = b"X" * 1024
        total_packets = 50
        num_threads = 5
        expected_bytes = total_packets * num_threads * 1024

        received_bytes = [0]
        stop_reader = threading.Event()

        def reader_worker():
            server_sock.settimeout(0.5)
            while not stop_reader.is_set():
                try:
                    chunk = server_sock.recv(65536)
                    if chunk:
                        received_bytes[0] += len(chunk)
                    else:
                        break
                except socket.timeout:
                    continue
                except Exception:
                    break

        r_thread = threading.Thread(target=reader_worker, daemon=True)
        r_thread.start()

        def sender_worker():
            for _ in range(total_packets):
                assert client.send_bytes(test_payload) is True

        threads = [threading.Thread(target=sender_worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        time.sleep(0.2)
        stop_reader.set()
        r_thread.join(timeout=1.0)

        server_sock.close()
        client_sock.close()

        assert client.packets_sent == total_packets * num_threads
        assert received_bytes[0] == expected_bytes

    def test_temporary_tcp_disconnect_preserves_session(self, test_broker):
        """Proves that a TCP disconnect does NOT invalidate the user session in SQLite."""
        broker, db, _ = test_broker

        # Create session in DB
        token = db.create_session("surgeon", "user")
        valid, _, _ = db.validate_session(token)
        assert valid is True

        # Simulate client connecting and disconnecting
        s1, s2 = socket.socketpair()
        client = ClientInfo(s1, ("127.0.0.1", 50001))
        client.username = "surgeon"
        client.session_id = token
        client.authenticated = True

        broker._clients[s1.fileno()] = client

        # Remove client (simulating TCP drop)
        broker._remove_client(client)

        # Verify session is STILL VALID in database for reconnect
        valid, username, role = db.validate_session(token)
        assert valid is True
        assert username == "surgeon"
        assert role == "user"

        s1.close()
        s2.close()

    def test_explicit_logout_invalidates_session(self, test_broker):
        """Proves that an explicit _logout message DOES invalidate the session in SQLite."""
        broker, db, _ = test_broker

        token = db.create_session("surgeon", "user")
        valid, _, _ = db.validate_session(token)
        assert valid is True

        s1, s2 = socket.socketpair()
        client = ClientInfo(s1, ("127.0.0.1", 50002))
        client.username = "surgeon"
        client.session_id = token
        client.authenticated = True

        broker._clients[s1.fileno()] = client

        # Handle explicit logout
        logout_msg = {"topic": CTRL_LOGOUT, "payload": {"session_id": token}}
        broker._handle_logout(client, logout_msg)

        # Verify session is now INVALID in database
        valid, _, _ = db.validate_session(token)
        assert valid is False

        s1.close()
        s2.close()
