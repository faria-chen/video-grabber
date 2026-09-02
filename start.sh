#!/bin/bash

echo "视频抓取器启动中..."

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium

# 启动服务
python app.py
