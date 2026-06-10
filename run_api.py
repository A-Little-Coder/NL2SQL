# ============================================================================
# NL2SQL API 服务启动入口（决策 49）
# ============================================================================
# 用法：
#   python run_api.py                              # 启动服务，所有 db 按需懒加载
#   python run_api.py --db_id california_schools   # 启动并预加载指定 db（warm-up）
#   python run_api.py --port 8080                  # 指定端口
#
# 前置条件：
#   1. 已构建索引：
#        python src/preprocessing/build_lsh_index.py --db_id <db_id>
#        python src/preprocessing/build_schema_index.py --db_id <db_id>
#   2. .env 中已配置 QWEN_API_KEY / BGE_M3_MODEL_PATH（可选 DB_POOL_MAX_SIZE）
#
# 启动后：
#   - 健康检查：GET http://localhost:8000/api/v1/health
#   - Swagger： http://localhost:8000/docs
#   - 查询：    POST http://localhost:8000/api/v1/query (SSE，body 需含 db_id)
#   - 列库：    GET  http://localhost:8000/api/v1/databases
#   - 列表：    GET  http://localhost:8000/api/v1/databases/{db_id}/tables
#
# 注意：当前仅支持单 worker。如需扩展多 worker，需引入 Redis 替换文件存储。
# ============================================================================

import argparse
import os
import sys
from pathlib import Path

# Windows 控制台 UTF-8
if sys.platform == "win32":
    os.system("chcp 65001 > nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from loguru import logger


def main():
    parser = argparse.ArgumentParser(description="NL2SQL API 服务")
    parser.add_argument("--db_id", type=str, default=None,
                        help="可选：启动时预加载（warm-up）的数据库 id")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--data_dir", type=str, default=None,
                        help="可选：data/ 根目录绝对路径（默认项目根的 data/）")
    args = parser.parse_args()

    # 将启动参数通过 app.state 透传给 lifespan
    from src.api.app import app
    if args.data_dir:
        app.state.data_dir = args.data_dir
    if args.db_id:
        app.state.warmup_db_id = args.db_id
        logger.info(f"将在 startup 预加载: {args.db_id}")

    import uvicorn
    logger.info(f"启动 HTTP 服务: http://{args.host}:{args.port}")
    logger.info(f"Swagger UI:    http://{args.host}:{args.port}/docs")
    logger.info(f"健康检查:      http://{args.host}:{args.port}/api/v1/health")

    # 决策 49：始终单 worker
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
