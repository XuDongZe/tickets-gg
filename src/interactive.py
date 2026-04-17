import sys
from typing import Any


def confirm_config(config: dict[str, Any]) -> dict[str, Any]:
    ticket_type_map = {"adult": "成人票", "student": "学生票", "senior": "老人票"}
    slot_map = {"any": "不限", "morning": "上午", "afternoon": "下午"}

    print("=" * 50)
    print("  故宫门票助手 — 配置确认")
    print("=" * 50)

    visitors = config.get("visitors", [])
    ticket = config["ticket"]

    print(f"\n  目标日期: {', '.join(ticket['target_dates'])}")
    print(
        f"  时段:     {slot_map.get(ticket.get('time_slot', 'any'), ticket.get('time_slot', 'any'))}"
    )
    print(
        f"  票种:     {ticket_type_map.get(ticket.get('ticket_type', 'adult'), ticket.get('ticket_type', 'adult'))}"
    )
    print(f"  数量:     {ticket.get('quantity', 1)}")

    print(f"\n  访客（{len(visitors)}人）:")
    for i, v in enumerate(visitors):
        id_num = v.get("id_number", "")
        masked = id_num[:6] + "****" + id_num[-4:] if len(id_num) > 10 else id_num
        print(f"    [{i + 1}] {v['name']} ({masked})")

    quantity = ticket.get("quantity", 1)
    if len(visitors) < quantity:
        print(
            f"\n  \033[31m警告: 需要 {quantity} 位访客，但仅配置了 {len(visitors)} 位\033[0m"
        )

    print()
    answer = input("  确认继续？[Y/n] ").strip().lower()
    if answer and answer != "y":
        print("  已取消。")
        sys.exit(0)

    return config


def prompt_manual_token() -> str | None:
    print("\n  Token 获取失败或超时。")
    print("  可手动粘贴 Token，或按回车放弃。\n")
    token = input("  access-token: ").strip()
    if token and token.startswith("eyJ") and "." in token:
        return token
    return None


def show_ready_banner(
    config: dict[str, Any], token: str, remaining_seconds: float
) -> None:
    slot_map = {"any": "不限", "morning": "上午", "afternoon": "下午"}

    ticket = config["ticket"]
    visitors = config.get("visitors", [])
    quantity = ticket.get("quantity", 1)
    names = [v["name"] for v in visitors[:quantity]]

    masked = token[:10] + "..." + token[-6:]
    mins = remaining_seconds / 60

    print()
    print("=" * 50)
    print("  \033[32m准备就绪\033[0m")
    print(f"  Token:  {masked}（剩余 {mins:.0f} 分钟）")
    print(f"  日期:   {', '.join(ticket['target_dates'])}")
    print(
        f"  时段:   {slot_map.get(ticket.get('time_slot', 'any'), ticket.get('time_slot', 'any'))}"
    )
    print(f"  访客:   {', '.join(names)}")
    print("=" * 50)
    print()
