#!/bin/bash

echo "视频抓取器启动中..."

# 每次启动都把 yt-dlp 升到最新：平台接口变动频繁，旧版会集体失效
pip install -U -r requirements.txt
pip install -U yt-dlp

# 启动服务
python app.py
