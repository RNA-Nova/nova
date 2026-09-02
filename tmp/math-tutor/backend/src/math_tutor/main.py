"""
math-tutor FastAPI 后端入口

提供两个核心接口：
- POST /api/chat        简单问答
- POST /api/generate    一键出题

并通过 StaticFiles 挂载前端构建产物，实现单端口部署。
"""

from __future__ import annotations

import asyncio
import os
import sys

# 将 backend/src 加入 Python 路径，方便直接运行
backend_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_src not in sys.path:
    sys.path.insert(0, backend_src)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from math_tutor.agent_service import (
    MathTutorAgent,
    stream_chat_response,
    stream_question_response,
)
from math_tutor.config import DEFAULT_HOST, DEFAULT_PORT, FRONTEND_DIST

app = FastAPI(
    title="Math Tutor",
    description="基于 nova_ai 与 nova_agent 的数学教学智能体",
    version="0.1.0",
)

# CORS：允许前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局 Agent 实例
_agent = MathTutorAgent()


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户的数学问题")


class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=1, description="出题知识点")
    count: int = Field(3, ge=1, le=10, description="题目数量")
    difficulty: str = Field("小学高年级", description="难度/年级")


class ResetRequest(BaseModel):
    pass


# ---------------------------------------------------------------------------
# API 路由
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "math-tutor"}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """简单问答：接收用户问题，以 SSE 流式返回答复。"""

    async def event_stream():
        async for chunk in stream_chat_response(_agent, request.message):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/generate")
async def generate(request: GenerateRequest) -> StreamingResponse:
    """一键出题：根据知识点、数量、难度以 SSE 流式返回题目。"""

    async def event_stream():
        async for chunk in stream_question_response(
            _agent,
            topic=request.topic,
            count=request.count,
            difficulty=request.difficulty,
        ):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/reset")
async def reset(request: ResetRequest) -> dict:
    """清空当前对话历史。"""
    _agent.reset()
    return {"status": "ok", "message": "对话已清空"}


# ---------------------------------------------------------------------------
# 前端静态文件服务（单端口部署）
# ---------------------------------------------------------------------------
frontend_dist = os.path.abspath(FRONTEND_DIST)
if os.path.isdir(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(request: Request, full_path: str):
        # API 路径不走前端
        if full_path.startswith("api/"):
            return {"detail": "Not Found"}
        index_file = os.path.join(frontend_dist, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"detail": "Frontend not built"}


# ---------------------------------------------------------------------------
# 直接运行入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT)
