# 苏银助手 —— 后端镜像（FastAPI / uvicorn）
#
# 运行方式见 docker-compose.yml：容器共享宿主机网络栈，
# 因为 MySQL / Redis / Qdrant 都只监听 127.0.0.1。
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# build-essential 只作兜底（个别包缺 wheel 时需要），装完即卸载，不进最终镜像
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential

COPY . .

# 运行期需要可写的数据目录（compose 里都会挂出来）
RUN mkdir -p /app/output /app/outputs /app/logs

EXPOSE 8001

# 必须单进程：
#   - SSE 任务队列在 app/utils/sse_utils.py，是进程内字典
#   - HITL 检查点是 AGENT_CHECKPOINT=sqlite:output/agent_state.db，单文件状态
# 加 --workers 会让 /stream 与 /query 落到不同进程，流式输出和断点恢复都会失效。
CMD ["python", "-m", "uvicorn", "app.api.query_server:app", \
     "--host", "127.0.0.1", "--port", "8001", "--workers", "1", \
     "--timeout-keep-alive", "75"]
