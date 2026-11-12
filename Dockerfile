FROM python:3.11-slim

# 安装系统依赖（yt-dlp 需要 ffmpeg）
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 每次构建强制升级 yt-dlp：平台接口变动很快，旧版会集体失效
COPY requirements.txt .
RUN pip install --no-cache-dir -U -r requirements.txt && \
    pip install --no-cache-dir -U yt-dlp

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
