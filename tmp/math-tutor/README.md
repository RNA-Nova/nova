# 数学导师 (Math Tutor)

基于 **nova_ai** 与 **nova_agent** 构建的数学教学智能体，提供简单问答与一键出题两大核心功能。

## 项目结构

```
math-tutor/
├── backend/                 # FastAPI 后端
│   ├── src/math_tutor/      # 业务代码
│   │   ├── main.py          # FastAPI 入口
│   │   ├── agent_service.py # nova_ai/nova_agent 集成
│   │   └── config.py        # 配置
│   └── pyproject.toml       # pixi + Python 依赖
├── frontend/                # React + TypeScript + Vite 前端
│   ├── src/
│   │   ├── App.tsx          # 主页面
│   │   ├── api.ts           # 后端 API 封装
│   │   └── index.css        # 样式
│   └── package.json
└── start.sh                 # 一键启动脚本
```

## 功能

- **简单问答**：向智能体提出数学问题，获得逐字流式的解答。
- **一键出题**：输入知识点、题目数量和难度，生成带答案与解析的数学练习题。
- **单端口部署**：FastAPI 同时挂载前端构建产物，访问 `http://localhost:8000` 即可使用。

## 环境要求

- Python >=3.12, <3.14
- pixi
- Node.js >=18
- API Key（默认使用 Volcengine，需设置 `VOLCENGINE_API_KEY` 环境变量）

## 快速启动

### 1. 一键启动（推荐）

```bash
cd /Users/liujinming/agent/nova/tmp/math-tutor
./start.sh
```

打开浏览器访问 http://localhost:8000 即可。

### 2. 分步启动

构建并启动后端：

```bash
cd /Users/liujinming/agent/nova/tmp/math-tutor/backend
pixi install -e dev
PYTHONPATH=src pixi run -e dev uvicorn math_tutor.main:app --host 0.0.0.0 --port 8000
```

前端开发模式（可选，用于前端调试）：

```bash
cd /Users/liujinming/agent/nova/tmp/math-tutor/frontend
npm install
npm run dev
```

## 配置

可通过环境变量调整：

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `VOLCENGINE_API_KEY` | Volcengine API Key | - |
| `NOVA_MATH_API_KEY` | 直接传入的 API Key（优先） | - |
| `NOVA_MATH_MODEL_PROVIDER` | 模型 provider | `volcengine` |
| `NOVA_MATH_MODEL_ID` | 模型 ID | `deepseek-v3-2-251201` |
| `NOVA_MATH_BASE_URL` | 自定义 base_url（非内置 provider 必填） | - |
| `NOVA_MATH_PORT` | 服务端口 | `8000` |
| `NOVA_MATH_HOST` | 服务绑定地址 | `0.0.0.0` |

## API 接口

- `GET /api/health` - 健康检查
- `POST /api/chat` - 问答（SSE 流式返回）
- `POST /api/generate` - 一键出题（SSE 流式返回）
- `POST /api/reset` - 清空对话

## 技术栈

- 后端：Python、FastAPI、nova_ai、nova_agent
- 前端：React、TypeScript、Vite
- 环境：pixi、npm
