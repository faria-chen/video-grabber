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
YouTube 会把机房 IP 判为机器人并要求登录验证；抖音的限制见下一节。

在 Render 的 Environment 里可配置：

| 变量 | 作用 |
| --- | --- |
| `YTDLP_PROXY` | 住宅代理地址（`http://user:pass@host:port`），绕开机房 IP 风控 |
| `YTDLP_COOKIES_B64` | Netscape 格式 cookies 文件的 base64（YouTube 登录态等，给 yt-dlp 用） |
| `YTDLP_COOKIE_FILE` | 或者给一个 cookies 文件路径 |
| `XHS_COOKIE` | 小红书网页版原始 Cookie 字符串（需含 `web_session`），仅自定义解析器使用 |

### 小红书已改用自定义解析器（2026-11-15）

之前线上一直报「缺 xsec_token / 缺 Cookie」，实测**根因不是 token 过期，而是请求头**：
同一条 `xhslink.cn` 分享短链，在同一个出口 IP 上：

| 请求头 | 结果 |
| --- | --- |
| 桌面 UA，无 `Accept-Language`（旧 `/api/download` 的写法） | 302 到 `/login`，236KB 空壳页，零视频字段 |
| 移动端 UA（哪怕只有 UA） | 200 完整笔记页，带 `og:video` / `__INITIAL_STATE__.masterUrl` |

因此 `/api/grab` 对小红书优先走自定义 HTML 解析器：移动端 UA 跟随短链 →
从 `og:video` / `masterUrl` / `backupUrls` 取 CDN 地址 → 直接返回，不再交给 yt-dlp。
同时 `/api/download` 对 `rednotecdn.com` 自动补移动端 UA + `Referer`，
并透传 `Range` 返回 206（预览不必每次拉 20MB+ 全量）。

若某些账号/地区仍被跳登录页，配 `XHS_COOKIE`（浏览器登录后复制完整 Cookie，含
`web_session`）或 `YTDLP_PROXY`。`GET /api/version` 的 `xhs_last` 会显示每次解析的
页面长度、是否登录页、找到几条视频流、命中哪条路径，方便定位。
图文笔记没有视频流，属于正常现象，会明确提示「这是图文笔记」。

访问 `https://<你的后端>/api/version` 可以确认 yt-dlp 版本、代理与 Cookie 是否生效。
每次部署都会自动升级 yt-dlp；平台接口变动很频繁，建议至少每月重新部署一次。

### 抖音现状（2026-11，上游限制，非本项目 bug）

yt-dlp 2026.10.26 的 `DouyinIE` 只请求 `aweme/v1/web/aweme/detail/`，
不带 `a_bogus` 签名，被 Argus 风控 403，统一抛
`Fresh cookies (not necessarily logged in) are needed` —— **这句话是误导**：
源码里根本没检查 cookie，单纯配置 Cookie 通常无效。
旧的 `iesdouyin.com/web/api/v2/aweme/iteminfo` 已返回空，分享页 SSR 也不再渲染 `play_addr`。
用 yt-dlp 自带的官方抖音测试视频在本机住宅 IP 上复现同样的报错，可确认与机房 IP 无关。

B站 / TikTok 不受影响。

2026-11-15 用一条**有效**分享口令（`v.douyin.com/14MDCJTec28/` → 视频 `7705370129063808283`）实测：
短链能正常展开到 `iesdouyin.com/share/video/...`，但页面返回 `country=kh / isLogin=false`，
只给引导下载 App 的壳 —— 没有 `<video>`、没有详情接口响应。本机住宅 IP、Render 机房 IP、
无头 Chrome（桌面 / 移动 UA）表现一致。yt-dlp、老接口、分享页 SSR、TikWM、cobalt 公共实例、
douyin.wtf / tenapi 及多个国内免费解析接口，全部拿不到播放地址。

=> 必要条件不只是 `a_bogus` 签名，还需要**中国大陆出口 IP + 抖音登录态**，
或接入持续维护签名的付费 / 自建解析服务（TikHub、Douyin_TikTok_Download_API）。
单纯配 Cookie 或换 UA 都无效，别在这上面耗时间。

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
├── app.py               # 后端主程序（FastAPI + yt-dlp + 小红书自定义解析器）
├── requirements.txt     # Python 依赖
├── Dockerfile           # Docker 部署配置
├── render.yaml          # Render.com 部署配置
├── start.sh             # 脚本启动（自动升级 yt-dlp）
├── README.md            # 说明文档
├── docs/
│   └── index.html       # GitHub Pages 前端
└── static/
    └── index.html       # 后端根路径返回的前端
```

## License

MIT
