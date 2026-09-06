#!/usr/bin/env bash
# One-time server preparation for scripts/deploy/aliyun.sh (run ON the server).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
swapon --show | grep -q swapfile || { fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo "/swapfile none swap sw 0 0" >> /etc/fstab; }
apt-get install -y -qq python3-pip python3-venv rsync git
command -v node >/dev/null || { curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y -qq nodejs; }
command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code
[ -d /opt/workbench-venv ] || python3 -m venv /opt/workbench-venv
node --version; claude --version
