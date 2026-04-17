import logging
import os
import subprocess
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

CHARLES_APP = "/Applications/Charles.app/Contents/MacOS/Charles"
CERT_DIR = Path.home() / ".gugong-helper"
CA_DIR = CERT_DIR / "ca"
CERTS_DIR = CERT_DIR / "certs"

CA_CERT_PATH = CA_DIR / "charles-ca-cert.pem"
CA_KEY_PATH = CA_DIR / "charles-ca-key.pem"

TARGET_DOMAINS = ["lotswap.dpm.org.cn", "*.dpm.org.cn"]


class CertManager:
    def __init__(self, target_host: str = "lotswap.dpm.org.cn"):
        self._target_host = target_host
        self._cert_path = CERTS_DIR / f"{target_host}.crt"
        self._key_path = CERTS_DIR / f"{target_host}.key"

    @property
    def cert_path(self) -> Path:
        return self._cert_path

    @property
    def key_path(self) -> Path:
        return self._key_path

    @property
    def ca_ready(self) -> bool:
        return CA_CERT_PATH.exists() and CA_KEY_PATH.exists()

    @property
    def server_cert_ready(self) -> bool:
        return self._cert_path.exists() and self._key_path.exists()

    def ensure_ready(self) -> None:
        if not self.ca_ready:
            self._export_charles_ca()
        if not self.server_cert_ready:
            self._sign_server_cert()

    def _export_charles_ca(self) -> None:
        if not Path(CHARLES_APP).exists():
            raise FileNotFoundError(
                "Charles not found at /Applications/Charles.app\n"
                "Install Charles or manually place CA cert+key in:\n"
                f"  {CA_CERT_PATH}\n  {CA_KEY_PATH}"
            )

        CA_DIR.mkdir(parents=True, exist_ok=True)
        p12_path = CERT_DIR / "tmp-export.p12"
        password = secrets.token_hex(16)

        try:
            logger.info("Exporting Charles CA via CLI...")
            result = subprocess.run(
                [CHARLES_APP, "ssl", "export", str(p12_path), password],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0 or not p12_path.exists():
                raise RuntimeError(
                    f"Charles CLI export failed (exit {result.returncode}): "
                    f"{result.stderr.strip()}"
                )

            subprocess.run(
                [
                    "openssl",
                    "pkcs12",
                    "-in",
                    str(p12_path),
                    "-out",
                    str(CA_CERT_PATH),
                    "-clcerts",
                    "-nokeys",
                    "-passin",
                    f"pass:{password}",
                    "-legacy",
                ],
                capture_output=True,
                check=True,
                timeout=10,
            )

            subprocess.run(
                [
                    "openssl",
                    "pkcs12",
                    "-in",
                    str(p12_path),
                    "-out",
                    str(CA_KEY_PATH),
                    "-nocerts",
                    "-nodes",
                    "-passin",
                    f"pass:{password}",
                    "-legacy",
                ],
                capture_output=True,
                check=True,
                timeout=10,
            )

            os.chmod(CA_KEY_PATH, 0o600)

            cert_mod = subprocess.run(
                ["openssl", "x509", "-in", str(CA_CERT_PATH), "-noout", "-modulus"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            key_mod = subprocess.run(
                ["openssl", "rsa", "-in", str(CA_KEY_PATH), "-noout", "-modulus"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()

            if cert_mod != key_mod:
                CA_CERT_PATH.unlink(missing_ok=True)
                CA_KEY_PATH.unlink(missing_ok=True)
                raise RuntimeError("Cert/key modulus mismatch after extraction")

            logger.info("Charles CA exported and verified")

        finally:
            p12_path.unlink(missing_ok=True)

    def _sign_server_cert(self) -> None:
        if not self.ca_ready:
            raise RuntimeError("CA cert/key not available, call ensure_ready() first")

        CERTS_DIR.mkdir(parents=True, exist_ok=True)
        csr_path = CERTS_DIR / "tmp.csr"
        san = ",".join(f"DNS:{d}" for d in TARGET_DOMAINS)

        try:
            subprocess.run(
                ["openssl", "genrsa", "-out", str(self._key_path), "2048"],
                capture_output=True,
                check=True,
                timeout=10,
            )
            os.chmod(self._key_path, 0o600)

            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-new",
                    "-key",
                    str(self._key_path),
                    "-subj",
                    f"/CN={self._target_host}",
                    "-out",
                    str(csr_path),
                ],
                capture_output=True,
                check=True,
                timeout=10,
            )

            subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-days",
                    "365",
                    "-in",
                    str(csr_path),
                    "-CA",
                    str(CA_CERT_PATH),
                    "-CAkey",
                    str(CA_KEY_PATH),
                    "-CAcreateserial",
                    "-out",
                    str(self._cert_path),
                    "-extfile",
                    "/dev/stdin",
                ],
                input=f"subjectAltName={san}".encode(),
                capture_output=True,
                check=True,
                timeout=10,
            )

            verify = subprocess.run(
                [
                    "openssl",
                    "verify",
                    "-CAfile",
                    str(CA_CERT_PATH),
                    str(self._cert_path),
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "OK" not in verify.stdout:
                raise RuntimeError(f"Cert verification failed: {verify.stdout}")

            logger.info(f"Server cert signed for {self._target_host} (SAN: {san})")

        finally:
            csr_path.unlink(missing_ok=True)
            (CERTS_DIR / "charles-ca-cert.srl").unlink(missing_ok=True)

    def check_charles_ca_trusted(self) -> bool:
        result = subprocess.run(
            [
                "security",
                "find-certificate",
                "-a",
                "-c",
                "Charles",
                "/Library/Keychains/System.keychain",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "Charles Proxy CA" in result.stdout
