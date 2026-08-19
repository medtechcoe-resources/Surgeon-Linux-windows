# ═══════════════════════════════════════════════════════════════════
#  AETHER CONSOLE — TLS CERTIFICATE MANAGEMENT
#  Manages the Aether Local Certificate Authority, broker cert,
#  and client device certificates for mTLS.
#
#  Design:
#    - A local CA (aether_ca.crt / aether_ca.key) signs all certs.
#    - Broker gets broker.crt / broker.key (signed by CA).
#    - Each device gets <device_id>.crt / <device_id>.key.
#    - Clients verify broker against CA; broker verifies clients.
#    - TLS 1.3 minimum protocol.
#    - Certificates are NOT regenerated on every startup.
#    - Certificate rotation happens only via explicit provisioning.
#
#  All paths are configured in shared_networking/config.py.
#  Do NOT call this from hot paths (network send/receive loops).
# ═══════════════════════════════════════════════════════════════════

import os
import ssl
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

log = logging.getLogger(__name__)

# Certificate validity periods
CA_VALIDITY_DAYS = 3650        # 10 years for local CA
CERT_VALIDITY_DAYS = 1825      # 5 years for device/broker certs


class TLSManager:
    """Manages the Aether Local CA, broker certificate, and device certificates.

    This is NOT a singleton — instantiate once during provisioning or
    broker startup and pass the instance where needed.
    """

    def __init__(self, cert_dir: str):
        """
        Args:
            cert_dir: Directory where all certificates and keys are stored.
                      Must be protected by OS permissions and .gitignore.
        """
        self._cert_dir = cert_dir
        self._ca_cert_path = os.path.join(cert_dir, "aether_ca.crt")
        self._ca_key_path  = os.path.join(cert_dir, "aether_ca.key")
        self._broker_cert_path = os.path.join(cert_dir, "broker.crt")
        self._broker_key_path  = os.path.join(cert_dir, "broker.key")
        os.makedirs(cert_dir, exist_ok=True)

    # ─── Public: Path Accessors ───────────────────────────────────

    @property
    def ca_cert_path(self) -> str:
        return self._ca_cert_path

    @property
    def broker_cert_path(self) -> str:
        return self._broker_cert_path

    @property
    def broker_key_path(self) -> str:
        return self._broker_key_path

    def device_cert_path(self, device_id: str) -> str:
        return os.path.join(self._cert_dir, f"{device_id}.crt")

    def device_key_path(self, device_id: str) -> str:
        return os.path.join(self._cert_dir, f"{device_id}.key")

    # ─── Public: Status ───────────────────────────────────────────

    def ca_exists(self) -> bool:
        """Return True if the Aether Local CA already exists."""
        return (os.path.exists(self._ca_cert_path) and
                os.path.exists(self._ca_key_path))

    def broker_cert_exists(self) -> bool:
        """Return True if the broker certificate already exists."""
        return (os.path.exists(self._broker_cert_path) and
                os.path.exists(self._broker_key_path))

    def device_cert_exists(self, device_id: str) -> bool:
        """Return True if a device certificate already exists."""
        return (os.path.exists(self.device_cert_path(device_id)) and
                os.path.exists(self.device_key_path(device_id)))

    # ─── Public: CA & Certificate Generation ─────────────────────

    def create_ca(self) -> bool:
        """Generate the Aether Local CA key and self-signed certificate.

        ONLY called during first-time provisioning. Idempotent if CA
        already exists (returns True without regenerating).
        """
        if self.ca_exists():
            log.info("[TLS] CA already exists — skipping generation")
            return True
        try:
            key = self._generate_rsa_key()
            subject = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "Aether Local CA"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME,
                                   "Aether Surgical Console"),
                x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME,
                                   "Security"),
            ])
            now = datetime.now(timezone.utc)
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(subject)  # self-signed
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now)
                .not_valid_after(now + timedelta(days=CA_VALIDITY_DAYS))
                .add_extension(
                    x509.BasicConstraints(ca=True, path_length=0),
                    critical=True,
                )
                .add_extension(
                    x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                    critical=False,
                )
                .sign(key, hashes.SHA256(), default_backend())
            )
            self._save_key(key, self._ca_key_path)
            self._save_cert(cert, self._ca_cert_path)
            log.info("[TLS] Aether Local CA created successfully")
            return True
        except Exception as e:
            log.error(f"[TLS] CA creation failed: {e}")
            return False

    def create_broker_cert(self, host: str = "127.0.0.1") -> bool:
        """Generate a broker certificate signed by the Aether Local CA.

        ONLY called during first-time provisioning. Idempotent.
        """
        if self.broker_cert_exists():
            log.info("[TLS] Broker cert already exists — skipping")
            return True
        if not self.ca_exists():
            log.error("[TLS] Cannot create broker cert — CA does not exist")
            return False
        try:
            ca_cert, ca_key = self._load_ca()
            key = self._generate_rsa_key()
            subject = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "Aether Broker"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME,
                                   "Aether Surgical Console"),
            ])
            now = datetime.now(timezone.utc)
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(ca_cert.subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now)
                .not_valid_after(now + timedelta(days=CERT_VALIDITY_DAYS))
                .add_extension(
                    x509.BasicConstraints(ca=False, path_length=None),
                    critical=True,
                )
                .add_extension(
                    x509.SubjectAlternativeName([
                        x509.IPAddress(__import__("ipaddress").ip_address(host)),
                        x509.DNSName("localhost"),
                    ]),
                    critical=False,
                )
                .sign(ca_key, hashes.SHA256(), default_backend())
            )
            self._save_key(key, self._broker_key_path)
            self._save_cert(cert, self._broker_cert_path)
            log.info("[TLS] Broker certificate created successfully")
            return True
        except Exception as e:
            log.error(f"[TLS] Broker cert creation failed: {e}")
            return False

    def create_device_cert(self, device_id: str,
                           device_type: str) -> Tuple[bool, str]:
        """Generate a device certificate signed by the Aether Local CA.

        Returns (True, cert_fingerprint) on success, or (False, '') on error.
        Idempotent — returns existing fingerprint if cert already exists.
        """
        if self.device_cert_exists(device_id):
            fingerprint = self.get_cert_fingerprint(
                self.device_cert_path(device_id))
            log.info(f"[TLS] Device cert already exists: {device_id}")
            return True, fingerprint

        if not self.ca_exists():
            log.error("[TLS] Cannot create device cert — CA does not exist")
            return False, ""
        try:
            ca_cert, ca_key = self._load_ca()
            key = self._generate_rsa_key()
            subject = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, device_id),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME,
                                   "Aether Surgical Console"),
                x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME,
                                   device_type),
            ])
            now = datetime.now(timezone.utc)
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(ca_cert.subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now)
                .not_valid_after(now + timedelta(days=CERT_VALIDITY_DAYS))
                .add_extension(
                    x509.BasicConstraints(ca=False, path_length=None),
                    critical=True,
                )
                .sign(ca_key, hashes.SHA256(), default_backend())
            )
            cert_path = self.device_cert_path(device_id)
            key_path  = self.device_key_path(device_id)
            self._save_key(key, key_path)
            self._save_cert(cert, cert_path)

            fingerprint = self.get_cert_fingerprint(cert_path)
            log.info(f"[TLS] Device cert created: {device_id} "
                     f"(fp={fingerprint[:16]}...)")
            return True, fingerprint
        except Exception as e:
            log.error(f"[TLS] Device cert creation failed for {device_id}: {e}")
            return False, ""

    # ─── Public: SSL Contexts ─────────────────────────────────────

    def create_server_context(self) -> ssl.SSLContext:
        """Create a strict TLS 1.3 server (broker) SSL context.

        - Uses broker certificate signed by the Aether Local CA.
        - Requires and verifies client certificates (mTLS).
        - Minimum TLS 1.3.
        - Certificate verification is NEVER disabled.

        Raises RuntimeError if certificates are missing.
        """
        if not self.broker_cert_exists():
            raise RuntimeError(
                "Broker certificate missing — run provisioning first.")
        if not self.ca_exists():
            raise RuntimeError(
                "Aether CA missing — run provisioning first.")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_cert_chain(
            certfile=self._broker_cert_path,
            keyfile=self._broker_key_path,
        )
        # Require and verify client certificates (mTLS)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cafile=self._ca_cert_path)
        # Warn if key file permissions are too permissive
        self.check_key_permissions(self._broker_key_path)
        self.check_key_permissions(self._ca_key_path)
        log.debug("[TLS] Server context created (mTLS, TLS 1.3)")
        return ctx

    def create_client_context(self, device_id: str) -> ssl.SSLContext:
        """Create a strict TLS 1.3 client SSL context for a device.

        - Presents device certificate to broker (mTLS).
        - Verifies broker certificate against the Aether Local CA.
        - Minimum TLS 1.3.
        - Certificate verification is NEVER disabled.

        Raises RuntimeError if certificates are missing.
        """
        if not self.device_cert_exists(device_id):
            raise RuntimeError(
                f"Device cert missing for {device_id} — run provisioning first.")
        if not self.ca_exists():
            raise RuntimeError(
                "Aether CA missing — run provisioning first.")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        # Verify broker cert against our local CA
        ctx.load_verify_locations(cafile=self._ca_cert_path)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = False  # Local IP — no DNS hostname to verify
        # Present our device cert to the broker
        ctx.load_cert_chain(
            certfile=self.device_cert_path(device_id),
            keyfile=self.device_key_path(device_id),
        )
        # Warn if key file permissions are too permissive
        self.check_key_permissions(self.device_key_path(device_id))
        log.debug(f"[TLS] Client context created for device: {device_id}")
        return ctx

    # ─── Public: Certificate Utilities ───────────────────────────

    @staticmethod
    def get_cert_fingerprint(cert_path: str) -> str:
        """Return the SHA-256 fingerprint hex string of a certificate file."""
        with open(cert_path, "rb") as f:
            cert_data = f.read()
        cert = x509.load_pem_x509_certificate(cert_data, default_backend())
        fp = cert.fingerprint(hashes.SHA256())
        return fp.hex()

    @staticmethod
    def get_peer_fingerprint(conn) -> Optional[str]:
        """Extract SHA-256 fingerprint from a TLS-wrapped socket's peer cert.

        Returns hex fingerprint string, or None if no peer cert present.
        """
        try:
            der_cert = conn.getpeercert(binary_form=True)
            if not der_cert:
                return None
            return hashlib.sha256(der_cert).hexdigest()
        except Exception:
            return None

    @staticmethod
    def validate_cert_expiry(cert_path: str) -> Tuple[bool, str]:
        """Check if a certificate is currently valid (not expired, not future).

        Returns (True, '') if valid, (False, reason) if not.
        """
        try:
            with open(cert_path, "rb") as f:
                cert_data = f.read()
            cert = x509.load_pem_x509_certificate(cert_data, default_backend())
            now = datetime.now(timezone.utc)
            if now < cert.not_valid_before_utc:
                return False, "Certificate not yet valid"
            if now > cert.not_valid_after_utc:
                return False, "Certificate has expired"
            return True, ""
        except Exception as e:
            return False, str(e)

    # ─── Internal Helpers ─────────────────────────────────────────

    def _load_ca(self):
        """Load CA certificate and private key objects."""
        with open(self._ca_cert_path, "rb") as f:
            ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())
        with open(self._ca_key_path, "rb") as f:
            ca_key = serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend())
        return ca_cert, ca_key

    @staticmethod
    def _generate_rsa_key():
        """Generate a 2048-bit RSA private key."""
        return rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )

    @staticmethod
    def _save_key(key, path: str):
        """Save an RSA private key to PEM file (no password — protected by OS).

        After writing, restricts file permissions to owner read/write (0o600).
        On Windows, os.chmod has limited effect (no group/world ACLs) but
        is a no-op rather than an error.
        """
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with open(path, "wb") as f:
            f.write(pem)
        try:
            os.chmod(path, 0o600)
        except OSError as e:
            log.warning(f"[TLS] Could not set permissions on {path}: {e}")

    @staticmethod
    def check_key_permissions(path: str):
        """Warn if a key file is readable by group or world.

        On POSIX systems this detects overly permissive key files.
        On Windows, file mode bits are limited — this logs at DEBUG level
        if the stat check succeeds with mode != 0o600.
        """
        try:
            mode = os.stat(path).st_mode & 0o777
            if mode & 0o077:  # Any group or world permission set
                log.warning(
                    f"[TLS] Key file has permissive permissions "
                    f"({oct(mode)}): {path}. "
                    "Recommended: 0o600 (owner read/write only)."
                )
        except OSError:
            pass  # File doesn't exist yet or stat failed — silently skip

    @staticmethod
    def _save_cert(cert, path: str):
        """Save a certificate to PEM file."""
        pem = cert.public_bytes(serialization.Encoding.PEM)
        with open(path, "wb") as f:
            f.write(pem)
