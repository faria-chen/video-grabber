# 🎬 视频抓取器

免水印视频下载工具，支持抖音、B站、小红书、快手、YouTube、TikTok 等主流平台。

## 功能特性

- ✅ **多平台支持**：抖音、B站、小红书、快手、YouTube、TikTok、微博、Twitter/X、Instagram、Vimeo、Facebook
- ✅ **自动识别**：粘贴链接自动识别平台
- ✅ **免水印下载**：获取无水印视频源
- ✅ **代理下载**：解决浏览器跨域限制
- ✅ **在线预览**：支持网页端直接播放
- ✅ **自动冷启动**：Render 免费版首次访问自动唤醒

## 技术栈

- **后端**：Python FastAPI + yt-dlp
- **前端**：纯 HTML/CSS/JS（无框架依赖）
- **部署**：Render.com 免费版 / Docker

## 部署到 Render.com

### 方法一：Fork + 自动部署

1. Fork 本仓库到你的 GitHub
2. 登录 [Render.com](https://render.com)
3. 点击 **New** → **Web Service**
4. 连接你的 GitHub 仓库
5. 配置：
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. 点击 **Create Web Service**

### 方法二：Docker 部署

```bash
# 本地运行
docker build -t video-grabber .
docker run -p 8000:8000 video-grabber

# 访问 http://localhost:8000
```

### 方法三：本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python main.py

# 访问 http://localhost:8000
```

## 解决 Render 免费版冷启动问题

Render 免费版服务不活跃时会休眠，首次请求需等待 30-50 秒唤醒。解决方案：

1. **UptimeRobot 监控**（推荐）：免费注册 [UptimeRobot](https://uptimerobot.com)，每 5 分钟 ping 你的服务 `/api/health` 端点
2. **健康检查**：已内置 `/api/health` 端点，可直接用作监控 URL

## API 接口

### `POST /api/grab`

抓取视频信息

**请求**：
- Content-Type: `multipart/form-data` 或 `application/json`
- 参数: `url` = 视频链接

**响应**：
```json
{
  "title": "视频标题",
  "video_url": "https://...",
  "thumbnail": "https://...",
  "platform": "抖音",
  "duration": 15,
  "format_info": "1080p"
}
```

### `GET /api/download?video_url=...&title=...`

代理下载视频（解决跨域）

### `GET /api/health`

健康检查

## 文件结构

```
video-grabber/
├── main.py              # 后端主程序
├── requirements.txt     # Python 依赖
├── Dockerfile           # Docker 部署配置
├── render.yaml          # Render.com 部署配置
├── README.md           # 说明文档
└── static/
    └── index.html       # 前端页面
```

## License

MIT
