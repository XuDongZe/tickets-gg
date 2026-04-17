import logging
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .gugong_api import GugongAPI
from .monitor import TicketMonitor

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))
RELEASE_HOUR = 20
RELEASE_MINUTE = 0


def _now() -> datetime:
    return datetime.now(tz=BEIJING_TZ)


def _sleep_until(target: datetime) -> None:
    while True:
        remaining = (target - _now()).total_seconds()
        if remaining <= 0:
            return
        if remaining > 2:
            time.sleep(remaining - 1.5)
        elif remaining > 0.05:
            time.sleep(0.01)


def play_alert(title: str, message: str, repeat: int = 3) -> None:
    try:
        for _ in range(repeat):
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{message}" with title "{title}"',
                ],
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["afplay", "/System/Library/Sounds/Glass.aiff"],
                capture_output=True,
                timeout=5,
            )
    except Exception as e:
        logger.debug(f"Alert failed: {e}")


class TicketScheduler:
    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._sched_cfg = config["scheduler"]
        self._ticket_cfg = config["ticket"]
        token = config.get("auth", {}).get("access_token", "")
        self._api = GugongAPI(token)
        self._monitor = TicketMonitor(config, self._api)
        self._running = False
        self._contacts: list[dict] = []

    @property
    def api(self) -> GugongAPI:
        return self._api

    @property
    def monitor(self) -> TicketMonitor:
        return self._monitor

    def start(self) -> None:
        self._running = True
        logger.info("Scheduler starting...")

        self._phase_warmup()

        now = _now()
        target_dates = self._ticket_cfg["target_dates"]
        logger.info(f"Target dates: {target_dates}")

        for date_str in target_dates:
            release_date = datetime.strptime(date_str, "%Y-%m-%d").date() - timedelta(
                days=7
            )
            release_dt = datetime(
                release_date.year,
                release_date.month,
                release_date.day,
                RELEASE_HOUR,
                RELEASE_MINUTE,
                0,
                tzinfo=BEIJING_TZ,
            )

            if now.date() == release_date and now < release_dt:
                self._schedule_release_night(date_str, release_dt)
            elif now.date() > release_date or (
                now.date() == release_date and now >= release_dt
            ):
                logger.info(f"{date_str}: release passed, entering pickup mode")
                self._start_pickup_monitor(date_str)
            else:
                days_until = (release_date - now.date()).days
                logger.info(
                    f"{date_str}: releases in {days_until} days ({release_date} 20:00)"
                )

        self._run_daily_pickup_loop()

    def _phase_warmup(self) -> None:
        logger.info("=== PHASE 1: WARMUP ===")
        logger.info(f"Beijing time: {_now().strftime('%Y-%m-%d %H:%M:%S')}")

        if not self._api.token_valid:
            logger.error("Token expired or missing! Run: python main.py refresh-token")
        else:
            logger.info(f"Token valid, expires in {self._api.token_remaining:.0f}s")

        try:
            self._contacts = self._api.get_contacts()
            if self._contacts:
                logger.info(f"Pre-fetched {len(self._contacts)} contacts")
            else:
                logger.warning("No contacts fetched (token may be invalid)")
        except Exception as e:
            logger.warning(f"Contact fetch failed: {e}")

        buy_days = self._api.get_can_buy_days()
        if buy_days:
            logger.info(f"Can buy {buy_days.get('days', '?')} days ahead")

    def _schedule_release_night(self, target_date: str, release_dt: datetime) -> None:
        warmup_minutes = self._sched_cfg.get("warmup_minutes_before", 2)
        warmup_dt = release_dt - timedelta(minutes=warmup_minutes)

        logger.info(f"[{target_date}] Release at {release_dt.strftime('%H:%M:%S')}")
        logger.info(f"[{target_date}] Warmup at {warmup_dt.strftime('%H:%M:%S')}")

        now = _now()
        if now < warmup_dt:
            wait_secs = (warmup_dt - now).total_seconds()
            logger.info(f"Waiting {wait_secs:.0f}s until warmup...")
            _sleep_until(warmup_dt)

        play_alert(
            "Warmup", f"Ticket release in ~{warmup_minutes} min for {target_date}!"
        )

        logger.info(f"Precise-waiting for {release_dt.strftime('%H:%M:%S')}...")
        _sleep_until(release_dt)

        play_alert("RELEASE TIME!", f"20:00 - {target_date} tickets now releasing!")

        self._phase_monitor(target_date)

    def _phase_monitor(self, target_date: str) -> None:
        logger.info("=== PHASE 2: MONITOR ===")
        duration = self._sched_cfg.get("monitor_duration_minutes", 60)
        interval = self._sched_cfg.get("check_interval_peak", 3)
        end_time = _now() + timedelta(minutes=duration)

        check_count = 0
        while self._running and _now() < end_time:
            result = self._monitor.check_availability(target_date)
            check_count += 1

            if result and result["bookable"]:
                remaining = result.get("remaining", "?")
                logger.info(
                    f"[{target_date}] TICKETS AVAILABLE! Remaining: {remaining}"
                )
                play_alert(
                    "TICKETS AVAILABLE!", f"{target_date}: {remaining} remaining!"
                )
                return

            now = _now()
            minutes_since = (
                now - now.replace(hour=RELEASE_HOUR, minute=RELEASE_MINUTE, second=0)
            ).total_seconds() / 60
            if minutes_since > 10:
                interval = self._sched_cfg.get("check_interval_offpeak", 30)

            if check_count % 20 == 0:
                logger.info(
                    f"Monitor: {check_count} checks, {minutes_since:.0f}min elapsed, interval={interval}s"
                )

            time.sleep(interval)

        logger.info(f"Monitor ended after {check_count} checks, no tickets found")

    def _start_pickup_monitor(self, target_date: str) -> None:
        thread = threading.Thread(
            target=self._pickup_loop, args=(target_date,), daemon=True
        )
        thread.start()

    def _pickup_loop(self, target_date: str) -> None:
        interval = self._sched_cfg.get("check_interval_offpeak", 30)
        logger.info(f"[{target_date}] Pickup monitor started, interval={interval}s")

        while self._running:
            result = self._monitor.check_availability(target_date)
            if result and result["bookable"]:
                remaining = result.get("remaining", "?")
                logger.info(
                    f"[{target_date}] TICKETS AVAILABLE! Remaining: {remaining}"
                )
                play_alert(
                    "TICKETS AVAILABLE!", f"{target_date}: {remaining} remaining!"
                )
                return
            time.sleep(interval)

    def _run_daily_pickup_loop(self) -> None:
        logger.info("Entering daily pickup loop (Ctrl+C to stop)")
        try:
            while self._running:
                time.sleep(60)
                now = _now()
                if now.hour == 19 and now.minute == 55:
                    for date_str in self._ticket_cfg["target_dates"]:
                        release_date = datetime.strptime(
                            date_str, "%Y-%m-%d"
                        ).date() - timedelta(days=7)
                        if now.date() == release_date:
                            release_dt = datetime(
                                release_date.year,
                                release_date.month,
                                release_date.day,
                                RELEASE_HOUR,
                                RELEASE_MINUTE,
                                0,
                                tzinfo=BEIJING_TZ,
                            )
                            self._schedule_release_night(date_str, release_dt)
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user")
            self._running = False

    def stop(self) -> None:
        self._running = False
        logger.info("Scheduler stopping...")
