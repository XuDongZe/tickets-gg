#!/bin/bash

set -e

GUGONG_VERSION="1.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  故宫门票助手 v${GUGONG_VERSION}${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}! $1${NC}"
}

print_info() {
    echo -e "${BLUE}→ $1${NC}"
}

check_macos() {
    if [[ "$(uname)" != "Darwin" ]]; then
        print_error "本脚本仅支持 macOS"
        exit 1
    fi
    print_success "macOS 环境确认"
}

check_python() {
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
        print_success "Python $PYTHON_VERSION 已安装"
        return 0
    else
        print_error "未找到 Python 3，请先安装"
        print_info "执行: brew install python3"
        exit 1
    fi
}

check_openssl() {
    if command -v openssl &> /dev/null; then
        print_success "OpenSSL 已安装"
    else
        print_error "未找到 OpenSSL，请先安装"
        print_info "执行: brew install openssl"
        exit 1
    fi
}

create_venv() {
    print_info "正在创建虚拟环境..."

    cd "$SCRIPT_DIR"

    if [[ -d ".venv" ]]; then
        print_info "虚拟环境已存在，正在重建..."
        rm -rf .venv
    fi

    python3 -m venv .venv
    print_success "虚拟环境创建完成"
}

install_dependencies() {
    print_info "正在安装依赖..."

    cd "$SCRIPT_DIR"

    .venv/bin/pip install --upgrade pip -q
    .venv/bin/pip install -r requirements.txt -q

    print_success "依赖安装完成"
}

create_config() {
    print_info "正在检查配置文件..."

    cd "$SCRIPT_DIR"

    if [[ ! -f "config.yaml" ]]; then
        print_warning "未找到 config.yaml，正在生成示例配置..."

        cat > config.yaml << 'EOF'
ticket:
  time_slot: any

scheduler:
  warmup_minutes_before: 2
  monitor_duration_minutes: 60
  check_interval_peak: 3
  check_interval_offpeak: 30

logging:
  level: INFO
  file: gugong_helper.log
  console: true
EOF

        print_success "示例 config.yaml 已生成"
    else
        print_success "config.yaml 已存在"
    fi
}

print_usage() {
    echo ""
    echo -e "${GREEN}安装完成！${NC}"
    echo ""
    echo "快速开始:"
    echo ""
    echo "  1. 获取 Token（从微信小程序捕获）:"
    echo "     .venv/bin/python main.py refresh-token"
    echo ""
    echo "  2. 查询余票:"
    echo "     .venv/bin/python main.py check --token TOKEN --dates 2026-05-01"
    echo ""
    echo "  3. 持续监控余票:"
    echo "     .venv/bin/python main.py watch --token TOKEN --dates 2026-05-01"
    echo ""
    echo "命令说明:"
    echo ""
    echo "  check          一次性查询余票情况"
    echo "  watch          持续监控余票，有票时声音提醒"
    echo "  refresh-token  获取新 Token 并保存到配置文件"
    echo "  status         查看 Token 状态和配置信息"
    echo ""
}

main() {
    print_header

    check_macos
    check_python
    check_openssl
    create_venv
    install_dependencies
    create_config
    print_usage
}

main "$@"
