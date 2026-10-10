#!/bin/bash
# 定时抓取入口（launchd: com.malog.wx-knowledge-base）。
#
# 配置与密钥统一由 ~/.wx-obsidian/ 提供（config.json + .env），
# 应用启动时会自行加载，这里不再从其它项目 source 环境变量。
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs
exec .venv/bin/python process_articles.py >> logs/run.log 2>&1
