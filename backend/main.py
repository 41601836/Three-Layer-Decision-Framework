# -*- coding: utf-8 -*-
"""
FastAPI 应用入口 (backend)
"""
import sys
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 确保 backend 目录在 sys.path 中
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# 日志基础配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="三层决策框架 API",
    description="专业投研终端 - 三层宏观/板块/个股决策引擎",
    version="2.0.0",
)

# CORS（开发阶段全放开，生产根据需要收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── 注册路由 ─────────────────────────────────────────────────────────────────
from app.api.v1 import layer1, layer2, monday, layer3, ai
app.include_router(layer1.router, prefix="/api/v1")
app.include_router(layer2.router, prefix="/api/v1")
app.include_router(layer3.router, prefix="/api/v1")
app.include_router(monday.router, prefix="/api/v1")
app.include_router(ai.router, prefix="/api/v1")

@app.get("/health", tags=["系统"])
async def health_check():
    return {"status": "ok", "version": "2.0.0"}


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化数据库表"""
    try:
        from app.core.database import init_tables
        init_tables()
        logger.info("✅ 数据库表初始化完成")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
