FROM python:3.12-slim

WORKDIR /app

# 安装 curl 用于健康检查
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.lock pyproject.toml ./

# 安装依赖（排除 -e . 行）
RUN grep -v "^-e \." requirements.lock > requirements.txt && \
    pip install --no-cache-dir -r requirements.txt && \
    rm requirements.txt

# 复制项目文件并安装
COPY . .
RUN pip install --no-cache-dir .

# 创建数据目录
RUN mkdir -p /app/data

# 健康检查（依赖 ENABLE_HTTP_HEALTHCHECK=1）
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:8080/healthz || exit 1

CMD ["python", "main.py"]
