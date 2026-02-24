# 基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖 (PyMuPDF 需要)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 先拷贝依赖文件，利用 Docker 缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝项目文件
COPY . .

# 创建日志目录
RUN mkdir -p /app/logs

# 环境变量 (敏感数据通过 .env 文件传入，不在此定义)
ENV LOG_DIR="/app/logs"

# 暴露端口
EXPOSE 19991

# 启动命令
CMD ["python", "main.py"]
