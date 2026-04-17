# AGENTS.md

本项目是**故宫门票余票查询助手**，用于查询故宫博物院指定日期的门票余票情况。

## 项目技能

本项目根目录包含 `SKILL.md`，其中详细描述了：

- 可用命令（`check`、`refresh-token`、`status`）及参数说明
- Token 生命周期管理流程（获取、使用、过期刷新）
- 故宫 API 的关键知识与数据陷阱
- 正确的余票判断逻辑

**在执行任何故宫门票相关操作前，必须先阅读 `SKILL.md`。**

## 快速参考

```bash
# 项目根目录
cd /path/to/gugong-ticket-helper

# 安装依赖
./install.sh

# 获取 Token（需要人类在 Mac 微信中打开故宫小程序配合）
.venv/bin/python main.py refresh-token

# 从 config.yaml 读取已保存的 Token，查询余票
.venv/bin/python main.py check --token TOKEN --dates YYYY-MM-DD
```
