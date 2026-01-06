FROM python:3.12-slim

WORKDIR /app

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

CMD ["python", "main.py"]
