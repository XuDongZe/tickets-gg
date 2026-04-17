# Gugong Ticket Helper

故宫门票余票查询助手 - 查询故宫博物院指定日期的门票余票情况。

## 安装

### 一键安装 (macOS)

```bash
cd /path/to/gugong-ticket-helper
./install.sh
```

安装脚本会自动：
- 创建 Python 虚拟环境
- 安装所需依赖
- 生成示例配置文件

### 前置要求

1. **Python 3** - macOS 通常已预装

## 使用

### 查询余票

```bash
.venv/bin/python main.py check --token TOKEN --dates 2026-05-01
```

查询多个日期：

```bash
.venv/bin/python main.py check --token TOKEN --dates 2026-05-01 2026-05-02 2026-05-03
```

指定时段偏好：

```bash
.venv/bin/python main.py check --token TOKEN --dates 2026-05-01 --time-slot morning
```

### 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--token` | 是 | access_token，从微信小程序获取 |
| `--dates` | 是 | 目标日期，格式 YYYY-MM-DD，可指定多个 |
| `--time-slot` | 否 | 时段偏好：`any`（默认）/ `morning` / `afternoon` |

### 刷新 Token

```bash
.venv/bin/python main.py refresh-token
```

按提示在 Mac 微信中打开「故宫博物院」小程序，工具会自动捕获 Token。

### 命令一览

| 命令 | 说明 |
|------|------|
| `check` | 查询余票（需要 `--token` 和 `--dates`） |
| `refresh-token` | 获取新 Token 并保存到配置文件 |
| `status` | 查看 Token 状态和查询配置 |

## 注意事项

- Token 有效期约 **2 小时**，过期执行 `refresh-token` 重新获取
- API 请求有限流保护，间隔至少 1 秒
- 仅支持 **macOS**

## License

MIT
