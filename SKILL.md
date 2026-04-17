# Gugong Ticket Helper Skill

故宫门票余票查询助手 - 查询故宫博物院指定日期的门票余票情况。

## 标准操作流程（SOP）

收到用户查票请求后，**严格按以下步骤执行**：

### Step 1: 确保 Token 可用

```bash
.venv/bin/python main.py refresh-token
```

该命令内置智能判断，**每次查询前都应执行**：
- Token 仍有效（剩余 >10 分钟）→ 秒返回，无需人工介入
- Token 过期或即将过期 → 启动代理捕获，提示用户在 Mac 微信中打开「故宫博物院」小程序（阻塞最多 120 秒）
- 捕获超时 → 命令会 fallback 到交互式输入要求粘贴 Token，**直接输入空行跳过**。然后告诉用户「刚才没抓到 Token，请重新打开微信小程序，我再试一次」，再次执行 `refresh-token`。**用户没有能力手动获取 Token，不要让用户粘贴。**

**SKILL 不需要自行判断 Token 是否有效，交给 `refresh-token` 处理。**

### Step 2: 读取 Token

从 `config.yaml` 读取 `auth.access_token` 字段（`refresh-token` 成功后会自动保存）。

### Step 3: 执行查询

```bash
.venv/bin/python main.py check --token TOKEN --dates YYYY-MM-DD [YYYY-MM-DD ...]
```

### Step 4: 解读结果并回复用户

`check` 命令的输出已封装好所有判断逻辑（放票范围、售罄、有票），直接按输出内容组织回复即可。

## 常见场景

### 场景 A：查询指定日期

用户说「查 5 月 1 日的票」→ 直接 `--dates 2026-05-01`。

### 场景 B：查询最近 N 天

用户说「看看最近 3 天」→ 计算今天起 3 天的日期，传入 `--dates`。

### 场景 C：查最近哪天有票

用户说「最近哪天有票」→ 分步查询：
1. 先查今天起 3~4 天
2. 如果全部售罄，扩展查询到可购范围上限（`check` 输出的日志会显示最远可购日期）
3. 找到第一个有票的日期，汇报给用户

### 场景 D：指定时段偏好

用户说「上午场」→ 加 `--time-slot morning`；「下午场」→ `--time-slot afternoon`。

## 可用命令

| 命令 | 说明 |
|------|------|
| `check` | 一次性查询余票（需要 `--token` 和 `--dates`） |
| `refresh-token` | 获取/刷新 Token 并保存到 config.yaml |
| `status` | 查看 Token 状态和查询配置（需要 `--token` 和 `--dates`） |

**不要使用 `watch`**（那是给用户交互式使用的）。如需持续监控，SKILL 应自行循环调用 `check`。

### 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--token` | 是 | access_token，从 config.yaml 读取 |
| `--dates` | 是 | 目标日期，格式 YYYY-MM-DD，可指定多个 |
| `--time-slot` | 否 | 时段偏好：`any`（默认）/ `morning` / `afternoon` |

## 安装

首次使用前需安装依赖（在项目根目录执行）：

```bash
./install.sh
```

## 注意事项

- Token 有效期约 2 小时
- API 请求有限流保护，间隔至少 1 秒
- 仅支持 macOS
- 所有用户可见输出使用中文
- 故宫门票提前约 6-7 天放票，具体天数动态变化，不要硬编码
- 每周一闭馆（法定节假日除外）

## 故宫 API 内部知识（仅修改代码时参考）

> 以下内容面向需要修改 `check` 命令内部逻辑的开发者。SKILL 调用 CLI 时无需了解这些细节，`check` 已封装好所有判断。

<details>
<summary>展开 API 细节</summary>

### API 端点与数据可信度

本项目涉及三个核心 API，它们对**未放票日期**均会返回占位数据，必须交叉校验：

| API | 文件位置 | 用途 | 数据可信度 |
|-----|---------|------|-----------|
| `canBuyDays` | `gugong_api.py` → `get_can_buy_days()` | 返回当前可购票的最大天数 | **唯一可靠的放票范围判据** |
| `calendar` | `gugong_api.py` → `get_calendar()` | 返回整月日历，含每日售票状态 | 部分可信（见下方陷阱） |
| `batchTimeReserveList` | `gugong_api.py` → `get_time_reserve()` | 返回指定日期的分时段预约库存 | 仅对已放票日期可信 |

### calendar API 数据陷阱

| 字段 | 含义 | 陷阱 |
|------|------|------|
| `stockNum` | **布尔标记**（0 或 1），不是真实库存 | 未放票日期返回 `1`，已售罄返回 `0`，有大量余票也返回 `1` |
| `remainingDesc` | 真实库存描述，如 `"余5956张"` | 仅在库存较多时出现；库存少或未放票时为 `None` |
| `saleStatus` | `"T"` 表示可售（含未放票），`"F"` 表示闭馆 | 未放票日期也返回 `"T"` |
| `disPlayStatus` | `2`=可预约/占位，`3`=已售罄 | 未放票日期也返回 `2` |

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

</details>
