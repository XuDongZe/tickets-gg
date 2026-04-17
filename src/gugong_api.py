import base64
import json
import logging
import threading
import time
from typing import Any, Optional

import requests

from .crypto_utils import decrypt_response

logger = logging.getLogger(__name__)

BASE_URL = "https://lotswap.dpm.org.cn"
MERCHANT_ID = "2655"
MERCHANT_INFO_ID = "2655"
PARK_ID_MAIN = "11324"
MODEL_CODE_ADULT = "MP2022070117025856157"
EXTERNAL_CODE_ADULT = "10005"
APP_NAME = "app_qqmap_tickets"

DEFAULT_HEADERS = {
    "app": APP_NAME,
    "Referer": "https://servicewechat.com/wx13169e68a3e63e55/185/page-frame.html",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/107.0.0.0 Safari/537.36 "
        "MicroMessenger/6.8.0(0x16080000) NetType/WIFI "
        "MiniProgramEnv/Mac MacWechat/WMPF MacWechat/3.8.8(0x13080812) XWEB/1227"
    ),
}

TOKEN_WARN_SECONDS = 600
MIN_REQUEST_INTERVAL = 1.0
_last_request_time = 0.0
_rate_limit_lock = threading.Lock()


def _parse_jwt_expiry(token: str) -> Optional[float]:
    if not token or not token.startswith("eyJ") or token.count(".") != 2:
        return None
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if exp:
            return float(exp)
    except Exception:
        pass
    return None


class GugongAPI:
    def __init__(
        self, access_token: str, token_obtained_at: float = 0, expires_in: int = 7200
    ):
        self._token = access_token
        jwt_exp = _parse_jwt_expiry(access_token)
        if jwt_exp:
            self._expires_at = jwt_exp
        else:
            self._expires_at = time.time() + expires_in
        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)

    @property
    def token_remaining(self) -> float:
        return self._expires_at - time.time()

    @property
    def token_valid(self) -> bool:
        return self.token_remaining > 0

    def _check_token(self) -> bool:
        remaining = self.token_remaining
        if remaining <= 0:
            logger.error("Access token expired. Run: python main.py refresh-token")
            return False
        elif remaining < TOKEN_WARN_SECONDS:
            logger.warning(f"Access token expires in {remaining:.0f}s")
        return True

    def _rate_limit(self) -> None:
        global _last_request_time
        with _rate_limit_lock:
            now = time.time()
            wait_time = _last_request_time + MIN_REQUEST_INTERVAL - now
            if wait_time > 0:
                time.sleep(wait_time)
            _last_request_time = time.time()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self._check_token():
            return {"code": -1, "msg": "token_expired"}

        self._rate_limit()

        url = BASE_URL + path
        headers = {"access-token": self._token, "ts": str(int(time.time()))}
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        resp = None
        try:
            resp = self._session.request(
                method, url, headers=headers, timeout=15, **kwargs
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            logger.error(f"Request timeout: {method} {path}")
            return {"code": -1, "msg": "timeout"}
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error: {method} {path}: {e}")
            return {"code": -1, "msg": "connection_error"}
        except requests.exceptions.HTTPError as e:
            status = resp.status_code if resp is not None else -1
            logger.error(f"HTTP error: {method} {path}: {e}")
            return {"code": status, "msg": str(e)}
        except ValueError:
            text = resp.text[:500] if resp else "no response"
            logger.error(f"Invalid JSON response: {method} {path}: {text}")
            return {"code": -1, "msg": "invalid_json", "raw": text}

        code = data.get("code")
        if code is not None and code not in (200, "200"):
            msg = data.get("msg", data.get("message", "unknown error"))
            logger.warning(f"API error {code}: {msg} ({method} {path})")
            logger.debug(f"Response data: {json.dumps(data, ensure_ascii=False)[:500]}")

        return data

    def get_can_buy_days(self) -> Optional[dict]:
        data = self._request("GET", "/dubboApi/product-core/ruleRpcService/canBuyDays")
        if data.get("code") in (200, "200"):
            inner = data.get("data", "{}")
            if isinstance(inner, str):
                try:
                    return json.loads(inner)
                except (json.JSONDecodeError, ValueError):
                    return None
            return inner
        return None

    def get_calendar(self, year: int, month: int) -> list[dict]:
        data = self._request(
            "GET",
            "/lotsapi/merchant/api/fsyy/calendar",
            params={
                "parkId": PARK_ID_MAIN,
                "year": str(year),
                "month": str(month),
                "merchantId": MERCHANT_ID,
                "merchantInfoId": MERCHANT_INFO_ID,
            },
        )
        if "privateKey" in data and "data" in data:
            decrypted = decrypt_response(data)
            if isinstance(decrypted, list):
                return decrypted
            if isinstance(decrypted, dict):
                return decrypted.get("data", []) if "data" in decrypted else [decrypted]
        return []

    def get_time_reserve(self, date: str) -> list[dict]:
        query_items = [
            {
                "modelCode": MODEL_CODE_ADULT,
                "externalCode": EXTERNAL_CODE_ADULT,
                "startTime": date,
                "endTime": date,
            }
        ]

        query_param = json.dumps(query_items, separators=(",", ":"))
        data = self._request(
            "POST",
            "/lotsapi/order/api/batchTimeReserveList",
            data={
                "queryParam": query_param,
                "merchantId": MERCHANT_ID,
                "merchantInfoId": MERCHANT_INFO_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if "privateKey" in data and "data" in data:
            decrypted = decrypt_response(data)
            if isinstance(decrypted, list):
                return decrypted
            if isinstance(decrypted, dict):
                return decrypted.get("data", []) if "data" in decrypted else [decrypted]
        return []

    def get_contacts(self) -> list[dict]:
        data = self._request("GET", "/lotsapi/up/api/user/contacts/list")
        if data.get("code") in (200, "200"):
            return data.get("data", [])
        return []

    def verify_ticket(
        self,
        date: str,
        visitors: list[dict],
        model_code: str,
        parent_model_code: str = "",
    ) -> dict:
        cert_list = []
        for v in visitors:
            cert_list.append(
                {
                    "cardType": 0,
                    "certNo": v["id_number"],
                    "name": v["name"],
                }
            )

        body = {
            "ticketVerificationDTOS": [
                {
                    "certAuthDTOS": cert_list,
                    "modelCodesDTOS": [
                        {
                            "modelCode": model_code,
                            "parentModelCode": parent_model_code or model_code,
                        }
                    ],
                }
            ],
            "merchantId": MERCHANT_ID,
            "merchantInfoId": MERCHANT_INFO_ID,
            "startDate": date,
            "orderType": "park",
            "chooseRuleProcessors": "",
        }
        data = self._request(
            "POST",
            "/dubboApi/trade-core/tradeCreateService/ticketVerificationCheck",
            json=body,
        )
        if data.get("code") in (200, "200"):
            return data.get("data", {})
        return {"checkStatus": False, "passInfo": data.get("msg", "verify failed")}

    def create_order(
        self,
        date: str,
        visitors: list[dict],
        model_code: str,
        stock_code: str,
        time_range: str = "",
        account_id: str = "",
    ) -> Optional[str]:
        save_orders = []
        free_list = []
        for v in visitors:
            save_orders.append(
                {
                    "modelCode": model_code,
                    "num": 1,
                    "unitPrice": 0,
                    "timeRange": time_range,
                }
            )
            free_list.append(
                {
                    "certNo": v["id_number"],
                    "name": v["name"],
                    "cardType": 0,
                    "modelCode": model_code,
                    "stockCode": stock_code,
                }
            )

        body = {
            "orderType": "park",
            "startDate": date,
            "endDate": date,
            "wayType": "6",
            "merchantInfoId": MERCHANT_INFO_ID,
            "saveOrders": save_orders,
            "freeList": free_list,
            "buyer": {},
        }

        data = self._request(
            "POST",
            f"/dubboApi/trade-core/tradeCreateService/create?ACCESS_TOKEN={self._token}",
            json=body,
        )
        if data.get("code") in (200, "200") and data.get("success"):
            order_code = data.get("data", {}).get("orderCode")
            logger.info(f"Order created: {order_code}")
            return order_code

        msg = data.get("msg", data.get("message", "create failed"))
        logger.error(f"Order creation failed: {msg}")
        return None

    def get_payment_info(self, order_code: str, account_id: str = "") -> Optional[dict]:
        data = self._request(
            "GET",
            "/lotsapi/order/orderPay/toPay",
            params={
                "accountId": account_id or MERCHANT_ID,
                "openId": "",
                "payType": "2003",
                "userType": "C",
                "orderCode": order_code,
            },
        )
        if data.get("code") in (200, "200"):
            return data.get("data")
        return None
