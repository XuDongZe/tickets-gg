import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from .gugong_api import GugongAPI

logger = logging.getLogger(__name__)


def _parse_remaining_desc(desc: str) -> int:
    if not desc:
        return 0
    match = re.search(r"余(\d+)张", desc)
    if match:
        return int(match.group(1))
    return 0


class TicketMonitor:
    def __init__(self, config: dict[str, Any], api: GugongAPI):
        self._ticket_cfg = config["ticket"]
        self._api = api
        self._target_dates = self._ticket_cfg["target_dates"]
        self._time_slot = self._ticket_cfg.get("time_slot", "any")
        self._max_buy_date: Optional[str] = None

    def _get_max_buy_date(self) -> Optional[str]:
        if self._max_buy_date:
            return self._max_buy_date
        buy_days_info = self._api.get_can_buy_days()
        if buy_days_info and "days" in buy_days_info:
            days = int(buy_days_info["days"])
            max_date = datetime.now() + timedelta(days=days)
            self._max_buy_date = max_date.strftime("%Y-%m-%d")
            logger.info(f"Max purchasable date: {self._max_buy_date} ({days} days out)")
        return self._max_buy_date

    def _is_on_sale(self, target_date: str) -> bool:
        max_date = self._get_max_buy_date()
        if not max_date:
            return True
        return target_date <= max_date

    def check_availability(self, target_date: str) -> Optional[dict]:
        try:
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            calendar = self._api.get_calendar(dt.year, dt.month)
            if not calendar:
                logger.warning(f"Empty calendar for {dt.year}-{dt.month}")
                return None

            for day in calendar:
                occ_date = day.get("occDate", "")
                if target_date not in str(occ_date):
                    continue

                sale_status = day.get("saleStatus", "")
                display_status = day.get("disPlayStatus", "")
                on_sale = self._is_on_sale(target_date)

                if not on_sale:
                    return {
                        "date": target_date,
                        "remaining": 0,
                        "remaining_desc": "",
                        "sale_status": "not_yet",
                        "display_status": display_status,
                        "bookable": False,
                        "slots": [],
                        "raw": day,
                    }

                remaining_desc = day.get("remainingDesc", "")
                stock = _parse_remaining_desc(remaining_desc)
                has_stock_flag = day.get("stockNum", 0) > 0

                slots = []
                for slot in day.get("parkFsyyDetailDTOS", []):
                    slot_remaining_desc = slot.get("remainingDesc", "")
                    slot_stock = _parse_remaining_desc(slot_remaining_desc)
                    slots.append(
                        {
                            "name": slot.get("fsTimeName", ""),
                            "total": slot.get("totalNum", 0),
                            "stock": slot_stock,
                            "stock_code": slot.get("fsStockCode", ""),
                            "date": slot.get("occDate", target_date),
                            "time_range": slot.get("fsTimeRange", ""),
                        }
                    )

                # Calendar remainingDesc has real counts (e.g. "余5956张").
                # When absent but stockNum=1, use time reserve API for
                # precise per-slot inventory.
                if stock == 0 and has_stock_flag:
                    time_slots = self.check_time_slots(target_date)
                    total_from_reserve = sum(ts["remaining"] for ts in time_slots)
                    if total_from_reserve > 0:
                        stock = total_from_reserve
                        slot_by_name = {s["name"]: s for s in slots}
                        for ts in time_slots:
                            if ts["name"] in slot_by_name:
                                slot_by_name[ts["name"]]["stock"] = ts["remaining"]

                bookable = stock > 0 and sale_status not in ("sold_out", "0")

                return {
                    "date": target_date,
                    "remaining": stock,
                    "remaining_desc": remaining_desc,
                    "sale_status": sale_status,
                    "display_status": display_status,
                    "bookable": bookable,
                    "slots": slots,
                    "raw": day,
                }

            return {
                "date": target_date,
                "remaining": 0,
                "bookable": False,
                "slots": [],
                "sale_status": "not_found",
                "display_status": "",
                "raw": {},
            }

        except Exception as e:
            logger.error(f"check_availability failed for {target_date}: {e}")
            return None

    def check_time_slots(self, target_date: str) -> list[dict]:
        try:
            reserves = self._api.get_time_reserve(target_date)
            results = []
            for item in reserves:
                model_code = item.get("modelCode", "")
                for fs in item.get("fsList", []):
                    remaining = fs.get("num", 0)
                    match = True
                    if self._time_slot == "morning" and "08:" not in fs.get(
                        "startTime", ""
                    ):
                        if "09:" not in fs.get("startTime", "") and "10:" not in fs.get(
                            "startTime", ""
                        ):
                            match = "08:" in fs.get(
                                "startTime", ""
                            ) or "上午" in fs.get("name", "")
                    elif self._time_slot == "afternoon":
                        match = (
                            "11:" in fs.get("startTime", "")
                            or "12:" in fs.get("startTime", "")
                            or "13:" in fs.get("startTime", "")
                            or "下午" in fs.get("name", "")
                        )

                    results.append(
                        {
                            "model_code": model_code,
                            "name": fs.get("name", ""),
                            "total": fs.get("totalNum", 0),
                            "remaining": remaining,
                            "start_time": fs.get("startTime", ""),
                            "end_time": fs.get("endTime", ""),
                            "date": fs.get("occDate", target_date),
                            "stock_code": fs.get("stockCode", ""),
                            "available": remaining >= 1,
                            "slot_match": match,
                        }
                    )
            return results
        except Exception as e:
            logger.error(f"check_time_slots failed for {target_date}: {e}")
            return []

    def find_best_slot(self, target_date: str) -> Optional[dict]:
        slots = self.check_time_slots(target_date)
        matching = [s for s in slots if s["available"] and s["slot_match"]]
        if matching:
            return max(matching, key=lambda s: s["remaining"])
        available = [s for s in slots if s["available"]]
        if available:
            return max(available, key=lambda s: s["remaining"])
        return None

    def monitor_loop(self, target_date: str, interval: float, callback) -> None:
        logger.info(f"Monitor loop: {target_date}, interval={interval}s")
        while True:
            result = self.check_availability(target_date)
            if result:
                logger.info(
                    f"[{target_date}] remaining={result['remaining']}, status={result['sale_status']}"
                )
                if result["bookable"]:
                    logger.info(
                        f"TICKETS FOUND for {target_date}! remaining={result['remaining']}"
                    )
                    callback(result)
                    return
            time.sleep(interval)

    def check_all_dates(self) -> list[dict]:
        results = []
        for date in self._target_dates:
            result = self.check_availability(date)
            if result:
                results.append(result)
        return results
