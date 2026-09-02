#!/usr/bin/env bash
# Math Tutor 启动脚本
# 后端 FastAPI + 前端静态文件，单端口部署

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
PORT="${NOVA_MATH_PORT:-8000}"

echo "[数学导师] 开始启动..."

# 如果前端产物不存在，先构建
if [ ! -d "$FRONTEND_DIR/dist" ]; then
    echo "[数学导师] 前端产物不存在，正在构建..."
    cd "$FRONTEND_DIR"
    npm install
    npm run build
fi

cd "$BACKEND_DIR"
echo "[数学导师] 启动后端服务: http://localhost:$PORT"
PYTHONPATH=src exec pixi run -e dev uvicorn math_tutor.main:app --host 0.0.0.0 --port "$PORT"
