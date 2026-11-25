# 构建上下文是仓库根目录：
#   docker build -f services/api/Dockerfile -t one-a-day-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/api/requirements.txt ./services/api/requirements.txt
RUN pip install -r services/api/requirements.txt

# 保持仓库的相对结构，config.py 靠 parents[3] 定位 packages/persona
COPY services/api/app ./services/api/app
COPY packages ./packages

ENV OAD_DB_PATH=/data/oneadday.db \
    PYTHONPATH=/app/services/api

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

WORKDIR /app/services/api

# 单 worker：默认限流是进程内内存态，多 worker 会让限流失效
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
