#!/bin/bash
# 加载 DeepSeek API 配置
source ~/.zshrc 2>/dev/null || source ~/.bash_profile 2>/dev/null || source ~/.bashrc 2>/dev/null
# 加载 MalogBot 的 .env（包含 DEEPSEEK_API_KEY）
set -a; source ~/Downloads/pyProj/MalogBot/.env 2>/dev/null; set +a
# 覆盖模型为 deepseek-v4-pro
export MODEL_NAME=deepseek-v4-pro
cd "$(dirname "$0")"
/usr/bin/python3 process_articles.py >> logs/run.log 2>&1
