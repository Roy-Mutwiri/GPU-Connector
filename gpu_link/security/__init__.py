"""TLS identity, Windows DPAPI credential storage, and authentication."""

import base64
import ctypes
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def data_dir():
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GPU Link"
    path.mkdir(parents=True, exist_ok=True)
    return path


def lan_address(host, allow_loopback=False):
    try:
        ip = ipaddress.ip_address(host)
        return ip.version == 4 and (allow_loopback and ip.is_loopback or
            not ip.is_loopback and (ip in ipaddress.ip_network("10.0.0.0/8") or
            ip in ipaddress.ip_network("172.16.0.0/12") or
            ip in ipaddress.ip_network("192.168.0.0/16") or ip.is_link_local))
    except ValueError:
        return False


def token_matches(expected, supplied):
    return isinstance(supplied, str) and len(supplied) <= 128 and hmac.compare_digest(
        expected.encode(), supplied.encode())


def protect(raw, decrypt=False):
    if os.name != "nt":
        return raw  # Non-Windows development only; file mode is restricted below.

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("Windows credential protection failed")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def save_secret(path, value):
    path.write_bytes(protect(value))
    if os.name != "nt":
        path.chmod(0o600)


class Identity:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else data_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        credential = self.directory / "worker.secret"
        if credential.exists():
            self.token = protect(credential.read_bytes(), True).decode()
        else:
            self.token = secrets.token_urlsafe(32)
            save_secret(credential, self.token.encode())
        self.cert_path = self.directory / "worker.crt"
        key_path = self.directory / "worker.key.dpapi"
        if not self.cert_path.exists() or not key_path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "GPU Link Worker")])
            now = datetime.now(timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
                .sign(key, hashes.SHA256()))
            self.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            save_secret(key_path, key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        self.fingerprint = cert.fingerprint(hashes.SHA256()).hex()
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        # OpenSSL accepts encrypted PEM; no plaintext key is written to disk.
        key = serialization.load_pem_private_key(protect(key_path.read_bytes(), True), password=None)
        password = secrets.token_urlsafe(32).encode()
        encrypted = self.directory / "worker.key.pem"
        encrypted.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(password)))
        try:
            self.context.load_cert_chain(self.cert_path, encrypted, password)
        finally:
            encrypted.unlink(missing_ok=True)

    def invitation(self, host, port):
        raw = json.dumps({"protocol": "GPU-LINK/1", "host": host, "port": port,
                          "token": self.token, "fingerprint": self.fingerprint}).encode()
        return "gpulink:" + base64.urlsafe_b64encode(raw).decode()


def parse_invitation(value):
    if not value.startswith("gpulink:") or len(value) > 4096:
        raise ValueError("Invalid GPU Link connection information")
    obj = json.loads(base64.urlsafe_b64decode(value[8:]))
    if obj.get("protocol") != "GPU-LINK/1" or not lan_address(obj.get("host", "")):
        raise ValueError("Invitation must use GPU-LINK/1 and a private LAN IPv4 address")
    if type(obj.get("port")) is not int or not 1024 <= obj["port"] <= 65535:
        raise ValueError("Invalid port")
    if not isinstance(obj.get("token"), str) or not 32 <= len(obj["token"]) <= 128:
        raise ValueError("Invalid token")
    if len(obj.get("fingerprint", "")) != 64:
        raise ValueError("Invalid certificate fingerprint")
    bytes.fromhex(obj["fingerprint"])
    return obj


def check_certificate(sock, fingerprint):
    actual = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest()
    if not hmac.compare_digest(actual, fingerprint.lower().replace(":", "")):
        raise ssl.SSLError("Worker certificate changed or fingerprint is incorrect; verify on the worker")
