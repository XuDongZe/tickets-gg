import base64
import http.client
import http.server
import json
import logging
import select
import socket
import socketserver
import ssl
import threading
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

TARGET_HOST = "lotswap.dpm.org.cn"


def _get_token_remaining(token: str) -> float:
    if not token or not token.startswith("eyJ") or token.count(".") != 2:
        return -1
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if exp:
            return float(exp) - time.time()
    except Exception:
        pass
    return -1


class _MITMHandler(http.server.BaseHTTPRequestHandler):
    cert_file: str = ""
    key_file: str = ""
    target_host: str = TARGET_HOST
    on_token_captured = None

    def do_CONNECT(self):
        host = self.path.split(":")[0]
        if self.target_host in host:
            self._intercept()
        else:
            self._tunnel()

    def _intercept(self):
        self.send_response(200, "Connection Established")
        self.end_headers()

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert_file, self.key_file)
        try:
            self.connection = ctx.wrap_socket(self.connection, server_side=True)
        except ssl.SSLError as e:
            logger.debug(f"TLS handshake failed: {e}")
            return

        self.rfile = self.connection.makefile("rb")
        self.wfile = self.connection.makefile("wb")

        try:
            self.handle_one_request()
        except Exception as e:
            logger.debug(f"Decrypted stream error: {e}")

    def _tunnel(self):
        host, _, port = self.path.partition(":")
        port = int(port) if port else 443
        try:
            upstream = socket.create_connection((host, port), timeout=5)
        except Exception:
            self.send_error(502)
            return

        self.send_response(200, "Connection Established")
        self.end_headers()

        conns = [self.connection, upstream]
        try:
            while True:
                rlist, _, xlist = select.select(conns, [], conns, 10)
                if xlist or not rlist:
                    break
                for r in rlist:
                    other = conns[1] if r is conns[0] else conns[0]
                    data = r.recv(8192)
                    if not data:
                        return
                    other.sendall(data)
        finally:
            upstream.close()

    def _forward_and_capture(self):
        token = self.headers.get("access-token")

        # Debug: 打印所有请求的 token
        if token:
            import base64
            import json

            try:
                parts = token.split(".")
                if len(parts) == 3:
                    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
                    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                    import time

                    exp = payload.get("exp", 0)
                    remaining = exp - time.time()
                    logger.info(
                        f"Request {self.command} {self.path}: token exp in {remaining:.0f}s"
                    )
            except Exception:
                pass

        is_jwt = (
            token
            and token.startswith("eyJ")
            and token.count(".") == 2
            and len(token) > 50
        )

        if is_jwt and self.on_token_captured:
            source = f"{self.command} {self.path}"
            logger.info(f"Token captured from {source}")
            self.on_token_captured(token, source)

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else None

        try:
            conn = http.client.HTTPSConnection(self.target_host, timeout=10)
            headers = {}
            for k, v in self.headers.items():
                if k.lower() not in (
                    "proxy-connection",
                    "connection",
                    "host",
                    "transfer-encoding",
                ):
                    headers[k] = v
            headers["Host"] = self.target_host
            conn.request(self.command, self.path, body, headers)
            resp = conn.getresponse()
            resp_body = resp.read()

            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
            self.wfile.flush()
            conn.close()
        except Exception as e:
            logger.debug(f"Upstream forward failed: {e}")
            try:
                self.send_error(502, str(e))
            except Exception:
                pass

    do_GET = _forward_and_capture
    do_POST = _forward_and_capture
    do_PUT = _forward_and_capture
    do_HEAD = _forward_and_capture
    do_OPTIONS = _forward_and_capture
    do_DELETE = _forward_and_capture
    do_PATCH = _forward_and_capture

    def log_message(self, format, *args):
        pass

    def log_error(self, format, *args):
        pass


class TokenCaptureProxy:
    def __init__(
        self,
        cert_file: str | Path,
        key_file: str | Path,
        listen_host: str = "127.0.0.1",
        listen_port: int = 9090,
        target_host: str = TARGET_HOST,
    ):
        self._cert_file = str(cert_file)
        self._key_file = str(key_file)
        self._host = listen_host
        self._port = listen_port
        self._target_host = target_host
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._token: str | None = None
        self._token_source: str = ""
        self._event = threading.Event()

    @property
    def captured_token(self) -> str | None:
        return self._token

    @property
    def token_source(self) -> str:
        return self._token_source

    @property
    def listen_port(self) -> int:
        return self._port

    def _on_token(self, token: str, source: str) -> None:
        remaining = _get_token_remaining(token)
        if remaining < 60:
            logger.info(f"Skipping expired token (exp in {remaining:.0f}s)")
            return

        if self._token is None:
            self._token = token
            self._token_source = source
            self._event.set()

    def start(self) -> None:
        handler_class = type(
            "Handler",
            (_MITMHandler,),
            {
                "cert_file": self._cert_file,
                "key_file": self._key_file,
                "target_host": self._target_host,
                "on_token_captured": self._on_token,
            },
        )

        for port in range(self._port, self._port + 10):
            try:
                self._server = http.server.ThreadingHTTPServer(
                    (self._host, port), handler_class
                )
                self._port = port
                break
            except OSError:
                logger.debug(f"Port {port} busy, trying next")
                continue
        else:
            raise OSError(f"No available port in range {self._port}-{self._port + 9}")

        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="mitm-proxy"
        )
        self._thread.start()
        logger.info(f"MITM proxy listening on {self._host}:{self._port}")

    def wait_for_token(self, timeout: float = 120.0) -> str | None:
        self._event.wait(timeout=timeout)
        return self._token

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        self._thread = None
        logger.info("MITM proxy stopped")
