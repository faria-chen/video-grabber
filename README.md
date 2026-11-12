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
   - **Build Command**: `pip install -U -r requirements.txt && pip install -U yt-dlp`
   - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
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
pip install -U -r requirements.txt && pip install -U yt-dlp

# 启动服务
python app.py

# 访问 http://localhost:8000
```

## 平台风控与必需配置（2026-11）

后端跑在 Render 免费层，出口是**美国机房 IP**（如 74.220.48.0/24，AS16509）。
YouTube 会把机房 IP 判为机器人并要求登录验证；小红书 / 抖音会把海外无登录态的
分享短链 302 到 `rednote.com/login`，yt-dlp 因此报 `Unsupported URL`。

要让 YouTube / 小红书 / 抖音恢复可用，在 Render 的 Environment 里加：

| 变量 | 作用 |
| --- | --- |
| `YTDLP_PROXY` | 住宅代理地址（`http://user:pass@host:port`），绕开机房 IP 风控 |
| `YTDLP_COOKIES_B64` | Netscape 格式 cookies 文件的 base64（小红书 `web_session`、YouTube 登录态） |
| `YTDLP_COOKIE_FILE` | 或者给一个 cookies 文件路径 |

小红书还必须用 App「分享 → 复制链接」得到的**完整链接**（含 `xsec_token`），
且 token 会过期；图文笔记没有视频流，属于正常现象。

访问 `https://<你的后端>/api/version` 可以确认 yt-dlp 版本、代理与 Cookie 是否生效。
每次部署都会自动升级 yt-dlp；平台接口变动很频繁，建议至少每月重新部署一次。

### 抖音现状（2026-11，上游限制，非本项目 bug）

yt-dlp 2026.10.26 的 `DouyinIE` 只请求 `aweme/v1/web/aweme/detail/`，
不带 `a_bogus` 签名，被 Argus 风控 403，统一抛
`Fresh cookies (not necessarily logged in) are needed` —— **这句话是误导**：
源码里根本没检查 cookie，单纯配置 Cookie 通常无效。
旧的 `iesdouyin.com/web/api/v2/aweme/iteminfo` 已返回空，分享页 SSR 也不再渲染 `play_addr`。
用 yt-dlp 自带的官方抖音测试视频在本机住宅 IP 上复现同样的报错，可确认与机房 IP 无关。

B站 / TikTok 不受影响。要恢复抖音，需要换成带真实浏览器的方案
（Playwright 拦截视频请求）或接入第三方解析服务，属于另一次改造。

分享口令粘贴时，链接后常紧跟「复制此链接」等中文，旧版会把中文粘进 URL
导致 `Unsupported URL`；现已改为只截取 ASCII URL 字符，并支持短链展开后还原 `/video/<id>`。

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
