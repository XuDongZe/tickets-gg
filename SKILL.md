# Gugong Ticket Helper Skill

故宫门票余票查询助手 - 查询故宫博物院指定日期的门票余票情况。

## SKILL 可用命令

SKILL 只使用以下命令，不要使用 `watch`（那是给用户交互式使用的）。

| 命令 | 说明 |
|------|------|
| `check` | 一次性查询余票（需要 `--token` 和 `--dates`） |
| `refresh-token` | 获取新 Token 并保存到 config.yaml |
| `status` | 查看 Token 状态和查询配置（需要 `--token` 和 `--dates`） |

如需持续监控，SKILL 应自行循环调用 `check`，而不是调用 `watch`。

## Token 生命周期

SKILL 负责维护 Token：
1. 调用 `refresh-token` 获取 Token（自动保存到 config.yaml）
2. 从 config.yaml 读取 Token，传入 `check --token TOKEN ...`
3. Token 有效期约 2 小时，过期后重新执行 `refresh-token`

**重要：`refresh-token` 需要人类介入。** 该命令会启动本地代理并等待用户在 Mac 微信中打开故宫小程序，整个过程会阻塞最多 120 秒。SKILL 调用时应提示用户操作，并耐心等待命令返回。如果超时未捕获到 Token，会回退到要求手动粘贴 Token 的交互式输入。

## 使用示例

```bash
# 获取 Token
.venv/bin/python main.py refresh-token

# 查询余票
.venv/bin/python main.py check --token TOKEN --dates 2026-05-01
.venv/bin/python main.py check --token TOKEN --dates 2026-05-01 2026-05-02
.venv/bin/python main.py check --token TOKEN --dates 2026-05-01 --time-slot morning

# 查看状态
.venv/bin/python main.py status --token TOKEN --dates 2026-05-01
```

## 安装

```bash
cd /Users/happyelements/Projects/tmp1/gugong-ticket-helper
./install.sh
```

## 注意事项

- Token 有效期约 2 小时，过期需通过 `refresh-token` 重新获取
- API 请求有限流保护，间隔至少 1 秒
- 仅支持 macOS
- 所有用户可见输出使用中文
- `--time-slot` 可选，默认 `any`（输出会分别显示上午/下午余票）

## 故宫 API 关键知识

### 放票规则

故宫门票提前若干天放票（当前约 6-7 天），具体天数由 `canBuyDays` API 动态返回，不要硬编码。

### API 端点与数据可信度

本项目涉及三个核心 API，它们对**未放票日期**均会返回占位数据，必须交叉校验：

| API | 文件位置 | 用途 | 数据可信度 |
|-----|---------|------|-----------|
| `canBuyDays` | `gugong_api.py` → `get_can_buy_days()` | 返回当前可购票的最大天数 | **唯一可靠的放票范围判据** |
| `calendar` | `gugong_api.py` → `get_calendar()` | 返回整月日历，含每日售票状态 | 部分可信（见下方陷阱） |
| `batchTimeReserveList` | `gugong_api.py` → `get_time_reserve()` | 返回指定日期的分时段预约库存 | 仅对已放票日期可信 |

### calendar API 数据陷阱

`get_calendar()` 返回的每日数据中：

| 字段 | 含义 | 陷阱 |
|------|------|------|
| `stockNum` | **布尔标记**（0 或 1），不是真实库存 | 未放票日期返回 `1`，已售罄返回 `0`，有大量余票也返回 `1` |
| `remainingDesc` | 真实库存描述，如 `"余5956张"` | 仅在库存较多时出现；库存少或未放票时为 `None` |
| `saleStatus` | `"T"` 表示可售（含未放票），`"F"` 表示闭馆 | 未放票日期也返回 `"T"`，不能单独用来判断是否已放票 |
| `disPlayStatus` | `2`=可预约/占位，`3`=已售罄 | 未放票日期也返回 `2`，与真正有票的日期相同 |

**关键结论**：当 `stockNum=1` 但 `remainingDesc` 缺失时，无法仅凭 calendar 数据区分「真有少量票」和「未放票占位」。

### time reserve API 数据陷阱

`get_time_reserve()` 返回的 `num` 字段：
- 对**已放票日期**：返回精确的真实库存
- 对**未放票日期**：返回占位值（如每个时段 `5`），**不是真实库存**

### 正确的余票判断逻辑

```
1. 调用 canBuyDays 获取可购票最大天数，算出最远可购日期
2. 目标日期超出范围 → 直接判定「尚未放票」，不查询库存
3. 目标日期在范围内：
   a. remainingDesc 有值 → 解析为真实库存（如 "余5956张" → 5956）
   b. remainingDesc 无值但 stockNum=1 → 调用 time reserve API 获取精确库存
   c. stockNum=0 → 售罄
```

### 三种日期状态的 API 返回值对照

| 状态 | `stockNum` | `remainingDesc` | time reserve `num` |
|------|-----------|-----------------|-------------------|
| 已售罄 | `0` | `None` | `0` |
| 有票（余票多） | `1` | `"余5956张"` | 真实数量 |
| 有票（余票少） | `1` | `None` | 真实数量 |
| 未放票 | `1` | `None` | 占位值（如 `5`） |

注意「有票（余票少）」和「未放票」的 calendar 返回值完全相同，必须用 `canBuyDays` 区分。
