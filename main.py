#!/usr/bin/env python3
import argparse
import sys
import logging
import time

from src.config import load_config, save_token
from src.gugong_api import GugongAPI, _parse_jwt_expiry
from src.monitor import TicketMonitor
from src.scheduler import TicketScheduler

logger = logging.getLogger("gugong")

SLOT_MAP = {"any": "不限", "morning": "上午", "afternoon": "下午"}


def _get_token_remaining(token: str) -> float:
    if not token or not token.startswith("eyJ"):
        return -1
    jwt_exp = _parse_jwt_expiry(token)
    if jwt_exp:
        return jwt_exp - time.time()
    return -1


def _require_valid_token(token: str) -> str:
    if not token:
        print("\033[31m错误: 未提供 --token\033[0m")
        sys.exit(1)

    remaining = _get_token_remaining(token)
    if remaining <= 0:
        print(f"\033[31m错误: Token 已过期（{abs(remaining):.0f}秒前）\033[0m")
        sys.exit(1)

    if remaining < 300:
        print(f"\033[33m警告: Token 将在 {remaining:.0f}秒后过期\033[0m")

    return token


def _build_config(args, file_config: dict) -> dict:
    fc_ticket = file_config.get("ticket", {})

    token = getattr(args, "token", "") or ""
    dates = getattr(args, "dates", []) or []
    time_slot = getattr(args, "time_slot", None)

    return {
        "auth": {"access_token": token},
        "ticket": {
            "target_dates": dates,
            "time_slot": time_slot or fc_ticket.get("time_slot", "any"),
        },
        "scheduler": file_config.get(
            "scheduler",
            {
                "warmup_minutes_before": 2,
                "monitor_duration_minutes": 60,
                "check_interval_peak": 3,
                "check_interval_offpeak": 30,
            },
        ),
        "logging": file_config.get("logging", {}),
    }


def cmd_refresh_token(config: dict, config_path: str, force: bool = False) -> None:
    from src.interactive import prompt_manual_token
    from src.token_capture.capture_flow import TokenCaptureFlow

    existing_token = config.get("auth", {}).get("access_token", "")
    remaining = _get_token_remaining(existing_token)

    if not force and remaining > 600:
        api = GugongAPI(existing_token)
        contacts = api.get_contacts()
        if isinstance(contacts, list) and len(contacts) > 0:
            print(
                f"\n  \033[32m当前 Token 有效（剩余 {remaining / 60:.0f} 分钟）\033[0m"
            )
            print(f"  常用观众: {[c.get('name', '?') for c in contacts]}")
            print("  跳过 Token 获取。如需强制刷新请加 --force")
            return

    flow = TokenCaptureFlow(config)
    errors = flow.preflight_check()
    if errors:
        print("\n  预检失败:")
        for e in errors:
            print(f"    - {e}")
        print("\n  回退到手动输入 Token")
        token = prompt_manual_token()
    else:
        print("\n  正在获取 Token...")
        token = flow.run()

    if not token:
        token = prompt_manual_token()

    if not token:
        print("  未获取到 Token")
        sys.exit(1)

    save_token(config_path, token)

    api = GugongAPI(token)
    remaining = api.token_remaining
    masked = token[:10] + "..." + token[-6:]

    print()
    print("=" * 50)
    print("  \033[32mToken 刷新成功\033[0m")
    print(f"  Token:    {masked}")
    print(f"  有效期:   {remaining / 60:.0f} 分钟")
    print("=" * 50)


def cmd_watch(config: dict) -> None:
    token = _require_valid_token(config["auth"]["access_token"])
    api = GugongAPI(token)
    remaining = api.token_remaining
    dates = config["ticket"]["target_dates"]
    masked = token[:10] + "..." + token[-6:]

    print()
    print("=" * 50)
    print("  \033[32m开始监控\033[0m")
    print(f"  Token:  {masked}（剩余 {remaining / 60:.0f} 分钟）")
    print(f"  日期:   {', '.join(dates)}")
    print(f"  时段:   {SLOT_MAP.get(config['ticket'].get('time_slot', 'any'), 'any')}")
    print("=" * 50)
    print()

    scheduler = TicketScheduler(config)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.stop()
        print("\n已停止。")


def cmd_check(config: dict) -> None:
    token = _require_valid_token(config["auth"]["access_token"])
    api = GugongAPI(token)
    monitor = TicketMonitor(config, api)
    dates = config["ticket"]["target_dates"]

    print(f"正在查询 {len(dates)} 个目标日期...\n")

    for date in dates:
        result = monitor.check_availability(date)
        if not result:
            print(f"  {date}: 查询失败（API 错误或 Token 无效）")
            continue

        if result.get("sale_status") == "not_yet":
            status_str = "尚未放票"
        elif result["bookable"]:
            status_str = "有票"
        else:
            status_str = "售罄"
        remaining = result.get("remaining", "?")
        remaining_desc = result.get("remaining_desc", "")
        print(f"  {date}: {status_str}（余票: {remaining}）")
        if remaining_desc:
            print(f"           {remaining_desc}")

        if result.get("slots"):
            for slot in result["slots"]:
                stock_str = f"{slot['stock']}"
                if slot["stock"] > 100:
                    stock_str = f"{slot['stock']}+"
                print(f"    {slot['name']}: {stock_str} ({slot['time_range']})")

    print()
    print("正在查询预约时段...")
    for date in dates:
        if not monitor._is_on_sale(date):
            print(f"  {date}: 尚未放票")
            continue
        slots = monitor.check_time_slots(date)
        if not slots:
            print(f"  {date}: 无时段数据")
            continue
        for s in slots:
            avail = "可约" if s["available"] else "--"
            match = "*" if s["slot_match"] else " "
            stock_str = f"{s['remaining']}"
            if s["remaining"] > 100:
                stock_str = f"{s['remaining']}+"
            print(
                f"  {date} [{avail}]{match} {s['name']}: {stock_str} ({s['start_time']}-{s['end_time']})"
            )


def cmd_status(config: dict) -> None:
    token_str = config.get("auth", {}).get("access_token", "")

    print("=== Token 状态 ===")
    if token_str:
        masked = (
            token_str[:10] + "..." + token_str[-6:]
            if len(token_str) > 20
            else token_str
        )
        print(f"  Token: {masked}")
        remaining = _get_token_remaining(token_str)
        if remaining > 0:
            print(f"  有效期: 剩余 {remaining:.0f}秒（{remaining / 60:.1f}分钟）")
        else:
            print(f"  状态: \033[31m已过期\033[0m（{abs(remaining):.0f}秒前）")
    else:
        print("  Token: 未提供")

    print()
    print("=== 目标日期 ===")
    for d in config["ticket"]["target_dates"]:
        print(f"  {d}")

    print()
    print("=== 查询配置 ===")
    tc = config["ticket"]
    print(f"  时段: {SLOT_MAP.get(tc.get('time_slot', 'any'), tc.get('time_slot'))}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="故宫门票助手 - 余票查询工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令说明:
  check          一次性查询余票（需要 --token 和 --dates）
  watch          持续监控余票，有票时声音提醒（需要 --token 和 --dates）
  refresh-token  获取新 Token 并保存到配置文件
  status         查看当前参数和 Token 状态

示例:
  %(prog)s check --token TOKEN --dates 2026-05-01
  %(prog)s watch --token TOKEN --dates 2026-05-01 2026-05-02
  %(prog)s refresh-token
        """,
    )
    parser.add_argument(
        "command",
        choices=["check", "watch", "refresh-token", "status"],
        help="要执行的命令",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="access_token（从微信小程序获取，check/status 必填）",
    )
    parser.add_argument(
        "--dates",
        nargs="+",
        default=None,
        metavar="DATE",
        help="目标日期，格式 YYYY-MM-DD（check 必填，可指定多个）",
    )
    parser.add_argument(
        "--time-slot",
        choices=["any", "morning", "afternoon"],
        default=None,
        help="时段偏好（默认: any）",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="配置文件路径（默认: config.yaml）",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="强制刷新 Token（即使当前 Token 有效）",
    )

    args = parser.parse_args()

    if args.command in ("check", "watch", "status"):
        if not args.token:
            parser.error(f"{args.command} 命令需要 --token 参数")
        if not args.dates:
            parser.error(f"{args.command} 命令需要 --dates 参数")

    from src.token_capture.proxy_config import recover_stale_proxy

    recover_stale_proxy()

    file_config = {}
    try:
        file_config = load_config(args.config)
    except FileNotFoundError:
        pass

    config = _build_config(args, file_config)

    if args.command == "refresh-token":
        cmd_refresh_token(config, args.config, args.force)
    elif args.command == "check":
        cmd_check(config)
    elif args.command == "watch":
        cmd_watch(config)
    elif args.command == "status":
        cmd_status(config)


if __name__ == "__main__":
    main()
