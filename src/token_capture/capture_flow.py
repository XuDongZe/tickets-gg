import logging
import os
import platform
import sys
import time
from typing import Any, Optional

from .cert_manager import CertManager
from .mitm_proxy import TokenCaptureProxy
from .proxy_config import SystemProxyManager, recover_stale_proxy

logger = logging.getLogger(__name__)

PROXY_PORT = 9090
PAC_SERVER_PORT = 19090
CAPTURE_TIMEOUT = 120


class TokenCaptureFlow:
    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._cert_mgr = CertManager()
        self._proxy: TokenCaptureProxy | None = None
        self._sys_proxy: SystemProxyManager | None = None

    def preflight_check(self) -> list[str]:
        errors = []

        if platform.system() != "Darwin":
            errors.append("Token capture only supported on macOS")
            return errors

        if not self._cert_mgr.check_charles_ca_trusted():
            errors.append(
                "Charles Proxy CA not found in System Keychain.\n"
                "  Install Charles, enable SSL Proxying, and trust its Root Certificate."
            )

        import shutil

        if not shutil.which("openssl"):
            errors.append("openssl CLI not found on PATH")

        return errors

    def run(self, timeout: float = CAPTURE_TIMEOUT) -> Optional[str]:
        recover_stale_proxy()

        print("\n  Preparing certificates...")
        self._cert_mgr.ensure_ready()
        print("  Certificates ready")

        self._proxy = TokenCaptureProxy(
            cert_file=self._cert_mgr.cert_path,
            key_file=self._cert_mgr.key_path,
            listen_port=PROXY_PORT,
        )

        # Silence stderr during proxy operation
        devnull = open(os.devnull, "w")
        old_stderr = sys.stderr
        sys.stderr = devnull

        try:
            self._proxy.start()

            self._sys_proxy = SystemProxyManager(
                proxy_port=self._proxy.listen_port,
                pac_server_port=PAC_SERVER_PORT,
            )
            self._sys_proxy.setup()

            print(f"  Proxy ready (port {self._proxy.listen_port})")
            print()
            print(
                "  \033[33m>>> Please open the Palace Museum mini-program in Mac WeChat <<<\033[0m"
            )
            print(
                "  \033[33m>>> 请在 Mac 微信中打开「故宫博物院」小程序，随便点几下 <<<\033[0m"
            )
            print()

            start_time = time.time()
            try:
                token = self._wait_with_progress(timeout)
            finally:
                self._sys_proxy.teardown()
                self._proxy.stop()
        finally:
            sys.stderr = old_stderr
            devnull.close()

        if not token:
            return None

        elapsed = time.time() - start_time
        print(f"\r  \033[32mToken captured in {elapsed:.1f}s\033[0m" + " " * 30)

        masked = token[:10] + "..." + token[-6:]
        source = self._proxy.token_source
        print(f"  Token:  {masked}")
        print(f"  Source: {source}")

        import base64 as _b64
        import json as _json

        try:
            parts = token.split(".")
            payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = _json.loads(_b64.urlsafe_b64decode(payload_b64))
            exp_ts = payload.get("exp", 0)
            import datetime

            exp_dt = datetime.datetime.utcfromtimestamp(exp_ts) + datetime.timedelta(
                hours=8
            )
            remaining = exp_ts - time.time()
            print(
                f"  JWT exp: {exp_dt.strftime('%Y-%m-%d %H:%M:%S')} ({remaining:.0f}s remaining)"
            )
        except Exception:
            pass

        if self._verify_token(token):
            print(f"  \033[32mToken verified OK\033[0m")
        else:
            print(f"  \033[33mToken may be expired (verification failed)\033[0m")

        return token

    def _wait_with_progress(self, timeout: float) -> Optional[str]:
        interval = 0.5
        elapsed = 0.0
        bar_width = 30

        proxy = self._proxy
        assert proxy is not None

        while elapsed < timeout:
            if proxy.captured_token:
                return proxy.captured_token

            pct = elapsed / timeout
            filled = int(bar_width * pct)
            bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
            print(
                f"\r  Waiting for token... [{bar}] {int(elapsed)}s / {int(timeout)}s",
                end="",
                flush=True,
            )
            time.sleep(interval)
            elapsed += interval

        print(
            f"\r  \033[31mTimeout ({int(timeout)}s) — no token captured\033[0m"
            + " " * 20
        )
        return None

    def _verify_token(self, token: str) -> bool:
        from ..gugong_api import GugongAPI

        api = GugongAPI(token)
        contacts = api.get_contacts()
        # 必须是非空列表，空列表可能是 401 错误返回的
        return isinstance(contacts, list) and len(contacts) > 0
