#!/bin/bash
# 安装脚本

set -e

echo "🚀 Telegram 消息托管机器人 - 安装脚本"
echo "========================================"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 请先安装 Python 3.10+"
    exit 1
fi

# 创建虚拟环境
echo "📦 创建虚拟环境..."
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
echo "📥 安装依赖..."
pip install --upgrade pip

if [ -f requirements.lock ]; then
    pip install -r requirements.lock
    pip install -e .
else
python3 - <<'PY'
import re
import subprocess
import sys

with open("pyproject.toml", "r", encoding="utf-8") as f:
    text = f.read()

match = re.search(r"(?ms)^dependencies\\s*=\\s*\\[(.*?)\\]\\s*", text)
if not match:
    raise SystemExit("未找到 dependencies 配置，请检查 pyproject.toml")

block = match.group(1)
deps = []
for line in block.splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if line.endswith(","):
        line = line[:-1].strip()
    if line and line[0] in {"\"", "'"}:
        deps.append(line.strip("\"'"))

if not deps:
    raise SystemExit("未解析到依赖，请检查 pyproject.toml")

subprocess.check_call([sys.executable, "-m", "pip", "install", *deps])
PY
fi

# 创建数据目录
mkdir -p data sessions

# 复制配置文件
if [ ! -f .env ]; then
    cp .env.example .env
    echo "📝 已创建 .env 文件，请编辑填入你的配置"
fi

echo ""
echo "✅ 安装完成！"
echo ""
echo "下一步："
echo "1. 编辑 .env 文件，填入 BOT_TOKEN 和 OPENAI_API_KEY"
echo "2. 运行: source .venv/bin/activate && python main.py"
