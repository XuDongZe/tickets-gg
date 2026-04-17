import atexit
import http.server
import json
import logging
import os
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path.home() / ".gugong-helper" / "proxy_state.json"

PAC_TEMPLATE = """function FindProxyForURL(url, host) {{
    if ({conditions}) {{
        return "PROXY {proxy_host}:{proxy_port}";
    }}
    return "DIRECT";
}}"""


def _detect_network_service() -> str:
    try:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'printf "open\\nget State:/Network/Global/IPv4\\nd.show" | scutil '
                "| awk '/PrimaryService/{print $3}'",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        guid = result.stdout.strip()
        if guid:
            result2 = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'printf "open\\nget Setup:/Network/Service/{guid}\\nd.show" | scutil '
                    f"| awk -F\": \" '/UserDefinedName/{{print $2}}'",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            name = result2.stdout.strip()
            if name:
                return name
    except Exception:
        pass
    logger.warning("Could not detect network service, falling back to Wi-Fi")
    return "Wi-Fi"


class SystemProxyManager:
    def __init__(
        self,
        proxy_host: str = "127.0.0.1",
        proxy_port: int = 9090,
        target_domains: list[str] | None = None,
        pac_server_port: int = 19090,
    ):
        self._proxy_host = proxy_host
        self._proxy_port = proxy_port
        self._domains = target_domains or [".dpm.org.cn"]
        self._pac_port = pac_server_port
        self._service: str = ""
        self._pac_dir: str = ""
        self._pac_server: http.server.HTTPServer | None = None
        self._pac_thread: threading.Thread | None = None
        self._active = False
        self._original_handlers: dict = {}

    @property
    def active(self) -> bool:
        return self._active

    def setup(self) -> None:
        recover_stale_proxy()

        self._service = _detect_network_service()
        logger.info(f"Network service: {self._service}")

        self._pac_dir = tempfile.mkdtemp(prefix="gugong-pac-")
        conditions = " || ".join(f'dnsDomainIs(host, "{d}")' for d in self._domains)
        pac_content = PAC_TEMPLATE.format(
            conditions=conditions,
            proxy_host=self._proxy_host,
            proxy_port=self._proxy_port,
        )
        pac_path = os.path.join(self._pac_dir, "proxy.pac")
        with open(pac_path, "w") as f:
            f.write(pac_content)

        for port in range(self._pac_port, self._pac_port + 10):
            try:
                handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
                    *a, directory=self._pac_dir, **kw
                )
                self._pac_server = http.server.HTTPServer(("127.0.0.1", port), handler)
                self._pac_server.timeout = 1
                self._pac_port = port
                break
            except OSError:
                continue
        else:
            raise OSError(
                f"No available port for PAC server in {self._pac_port}-{self._pac_port + 9}"
            )

        self._pac_thread = threading.Thread(
            target=self._pac_server.serve_forever,
            daemon=True,
            name="pac-server",
        )
        self._pac_thread.start()

        pac_url = f"http://127.0.0.1:{self._pac_port}/proxy.pac"

        subprocess.run(
            ["networksetup", "-setautoproxyurl", self._service, pac_url],
            capture_output=True,
            check=True,
            timeout=5,
        )
        subprocess.run(
            ["networksetup", "-setautoproxystate", self._service, "on"],
            capture_output=True,
            check=True,
            timeout=5,
        )

        self._active = True
        self._save_state()
        self._install_cleanup_hooks()

        logger.info(f"PAC proxy set: {pac_url} (domains: {self._domains})")

    def teardown(self) -> None:
        if not self._active:
            return
        self._active = False

        try:
            subprocess.run(
                ["networksetup", "-setautoproxystate", self._service, "off"],
                capture_output=True,
                timeout=5,
            )
            logger.info("System proxy restored")
        except Exception as e:
            logger.warning(f"Failed to restore proxy: {e}")

        if self._pac_server:
            self._pac_server.shutdown()
            self._pac_server = None

        if self._pac_dir and os.path.exists(self._pac_dir):
            import shutil

            shutil.rmtree(self._pac_dir, ignore_errors=True)

        STATE_FILE.unlink(missing_ok=True)

    def _save_state(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(
                {
                    "service": self._service,
                    "pid": os.getpid(),
                    "timestamp": time.time(),
                }
            )
        )

    def _install_cleanup_hooks(self) -> None:
        atexit.register(self.teardown)

        for sig in (signal.SIGINT, signal.SIGTERM):
            prev = signal.getsignal(sig)
            self._original_handlers[sig] = prev

            def handler(signum, frame, _prev=prev, _self=self):
                _self.teardown()
                if callable(_prev) and _prev not in (signal.SIG_DFL, signal.SIG_IGN):
                    _prev(signum, frame)
                else:
                    raise SystemExit(128 + signum)

            signal.signal(sig, handler)


def recover_stale_proxy() -> None:
    if not STATE_FILE.exists():
        return

    try:
        state = json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        STATE_FILE.unlink(missing_ok=True)
        return

    pid = state.get("pid", 0)
    try:
        os.kill(pid, 0)
        return
    except (ProcessLookupError, PermissionError):
        pass

    service = state.get("service", "Wi-Fi")
    logger.warning(f"Recovering stale proxy from crashed PID {pid}")

    try:
        subprocess.run(
            ["networksetup", "-setautoproxystate", service, "off"],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass

    STATE_FILE.unlink(missing_ok=True)
    logger.info("Stale proxy recovered")
