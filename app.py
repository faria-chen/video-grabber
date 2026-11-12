"""
视频抓取器后端 - 支持抖音/B站/小红书/YouTube/快手等主流平台
基于 FastAPI + yt-dlp

2026-11 修复:
  - 小红书分享短链被重定向到 rednote.com/login 时还原真实笔记地址
  - 透传 xsec_token / xsec_source 等访问凭证
  - 移除全局 Google Referer，改为按平台设置请求头
  - 支持 YTDLP_PROXY(住宅代理) 与 YTDLP_COOKIES_B64 / YTDLP_COOKIE_FILE(登录态)
  - YouTube 避开 SABR 客户端，改用 tv/mweb
  - 新增 /api/version 诊断端点，错误信息改为可直接执行的建议
"""

import os
import re
import sys
import base64
import asyncio
import tempfile
import threading
import time as _time
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs, urlencode
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx
import yt_dlp

STARTED_AT = _time.time()

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Mobile/15E148 Safari/604.1"
)

# Render 免费层是美国机房 IP，YouTube / 小红书 / 抖音会判定为机器人。
# 允许通过环境变量挂住宅代理与登录 Cookie。
# 只认显式配置，避免误继承宿主/系统里不可用的代理变量
PROXY = os.environ.get("YTDLP_PROXY") or None


def _prepare_cookiefile() -> Optional[str]:
    """从环境变量准备 Netscape 格式 cookie 文件"""
    raw = os.environ.get("YTDLP_COOKIES_B64")
    if raw:
        try:
            path = Path(tempfile.gettempdir()) / "video-grabber-cookies.txt"
            path.write_bytes(base64.b64decode(raw))
            return str(path)
        except Exception as exc:
            print(f"[cookies] YTDLP_COOKIES_B64 解析失败: {exc}")
    path = os.environ.get("YTDLP_COOKIE_FILE")
    if path and Path(path).is_file():
        return path
    return None


COOKIE_FILE = _prepare_cookiefile()

app = FastAPI(title="视频抓取器", docs_url=None, redoc_url=None)


# ─── 预热 + Keep-Alive ────────────────────────────────────────────────
def _warmup_sync():
    try:
        yt_dlp.YoutubeDL({"quiet": True}).params
    except Exception:
        pass
    # 每 10 分钟 ping 自己，防止 Render 免费层休眠
    while True:
        _time.sleep(600)
        try:
            import urllib.request
            port = os.environ.get("PORT", "8000")
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5)
        except Exception:
            pass


@app.on_event("startup")
async def _warmup():
    threading.Thread(target=_warmup_sync, daemon=True).start()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ─── 平台识别 ────────────────────────────────────────────────────────
PLATFORM_PATTERNS = {
    "douyin": [r"douyin\.com", r"v\.douyin\.com"],
    "bilibili": [r"bilibili\.com", r"b23\.tv"],
    "xiaohongshu": [r"xiaohongshu\.com", r"xhslink\.com", r"xhs\.cn", r"xhs\.link", r"rednote\.com"],
    "kuaishou": [r"kuaishou\.com", r"v\.kuaishou\.com"],
    "tiktok": [r"tiktok\.com"],
    "youtube": [r"youtube\.com", r"youtu\.be"],
    "twitter": [r"twitter\.com", r"x\.com"],
    "instagram": [r"instagram\.com"],
    "weibo": [r"weibo\.com", r"m\.weibo\.cn"],
    "vimeo": [r"vimeo\.com"],
    "facebook": [r"facebook\.com", r"fb\.watch"],
    "weixin": [r"weixin\.qq\.com", r"channels\.weixin\.qq\.com"],
}

PLATFORM_NAMES = {
    "douyin": "抖音",
    "bilibili": "B站",
    "xiaohongshu": "小红书",
    "kuaishou": "快手",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "twitter": "X/Twitter",
    "instagram": "Instagram",
    "weibo": "微博",
    "vimeo": "Vimeo",
    "facebook": "Facebook",
    "weixin": "微信视频号",
}

REFERERS = {
    "bilibili": "https://www.bilibili.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "douyin": "https://www.douyin.com/",
    "kuaishou": "https://www.kuaishou.com/",
    "weibo": "https://weibo.com/",
    "youtube": "https://www.youtube.com/",
    "tiktok": "https://www.tiktok.com/",
}


def detect_platform(url: str) -> Optional[str]:
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return platform
    return None


# ─── URL 归一化 ───────────────────────────────────────────────────────
# 小红书分享短链在海外 IP / 无登录态下会被 302 到
# https://www.rednote.com/login?redirectPath=...discovery%2Fitem%2F<id>...
# yt-dlp 跟随重定向后拿到登录页，直接报 "Unsupported URL"。
# 这里从重定向链里把真实笔记 ID 和 xsec_token 捞回来，重写成标准地址。
SHORT_HOSTS = (
    "xhslink.", "xhs.cn", "xhs.link", "v.douyin.com", "b23.tv",
    "youtu.be", "vm.tiktok.com", "vt.tiktok.com", "v.kuaishou.com",
)
NOTE_ID_RE = re.compile(r"/(?:discovery/item|explore|item)/([0-9a-f]{16,32})", re.I)
# 只吃 ASCII URL 字符。抖音/小红书分享口令里，链接后面常紧跟「复制此链接」等中文，
# 旧的 [^\s]+ 会把中文一起吞进 URL，yt-dlp 随后报 Unsupported URL。
URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]+")
BARE_SHORTLINK_RE = re.compile(
    r"(?<![\w/])((?:v\.douyin\.com|xhslink\.com|xhs\.cn|b23\.tv|youtu\.be|vm\.tiktok\.com)/[A-Za-z0-9_\-]+/?)",
    re.I,
)
DOUYIN_ID_RE = re.compile(
    r"(?:douyin\.com/(?:video|share/video)/|iesdouyin\.com/share/video/|modal_id=)(\d{15,25})",
    re.I,
)


def _extract_url(text: str) -> Optional[str]:
    m = URL_RE.search(text)
    if m:
        return m.group(0).rstrip(".,;:!?)]")
    m = BARE_SHORTLINK_RE.search(text)
    if m:
        return "https://" + m.group(1)
    return None


def _follow_redirects(url: str) -> list:
    """返回完整重定向链（含中间跳），便于捞取被 login 页吞掉的真实地址"""
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=10,
            proxy=PROXY,
            trust_env=bool(PROXY),
            headers={"User-Agent": MOBILE_UA, "Accept": "text/html,*/*"},
        ) as client:
            resp = client.get(url)
        return [str(h.url) for h in resp.history] + [str(resp.url)]
    except Exception as exc:
        print(f"[redirect] 展开短链失败: {exc}")
        return [url]


def _deep_unquote(value: str, rounds: int = 3) -> str:
    for _ in range(rounds):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def normalize_url(text: str) -> tuple:
    """返回 (规范化 URL, 强制指定的平台或 None)"""
    url = _extract_url(text)
    if not url:
        return text.strip(), None

    original = url
    host = (urlparse(url).hostname or "").lower()
    need_expand = (
        any(key in host for key in SHORT_HOSTS)
        or "/login" in url
        or "rednote.com" in host
    )
    chain = _follow_redirects(url) if need_expand else [url]

    # 收集 xsec_token / xsec_source（分享链接里的访问凭证，缺了就拿不到视频流）
    tokens = {}
    for item in chain + [original]:
        query = parse_qs(urlparse(_deep_unquote(item)).query)
        for key in ("xsec_token", "xsec_source"):
            if key in query and key not in tokens:
                tokens[key] = query[key][0]

    # 从重定向链（含 login 的 redirectPath 参数）里找笔记 ID
    note_id = None
    for item in chain:
        decoded = _deep_unquote(item)
        if "/login" in decoded:
            redirect_path = parse_qs(urlparse(decoded).query).get("redirectPath", [""])[0]
            decoded = _deep_unquote(redirect_path) or decoded
        match = NOTE_ID_RE.search(decoded)
        if match:
            note_id = match.group(1)
            break

    if note_id:
        query = urlencode(tokens)
        normalized = f"https://www.xiaohongshu.com/explore/{note_id}"
        if query:
            normalized += f"?{query}"
        return normalized, "xiaohongshu"

    # 抖音短链常被 302 到 /video/<id>、/share/video/<id> 或 ?modal_id=<id>。
    # 还原成标准视频页，比把重定向后的首页丢给 yt-dlp 强。
    if any("douyin" in (urlparse(item).hostname or "").lower() for item in chain):
        for item in chain:
            match = DOUYIN_ID_RE.search(_deep_unquote(item))
            if match:
                return f"https://www.douyin.com/video/{match.group(1)}", "douyin"

    # 普通短链：取重定向链最后一个非登录页、非首页地址
    if need_expand:
        for item in reversed(chain):
            if "/login" in item:
                continue
            if re.match(r"https?://(?:www\.)?(?:douyin|iesdouyin)\.com/?$", item, re.I):
                continue
            url = item
            break
    return url, None


# ─── yt-dlp 配置 ──────────────────────────────────────────────────────
def get_ydl_opts(url: str, platform: Optional[str] = None) -> dict:
    headers = {
        "User-Agent": DESKTOP_UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if platform in REFERERS:
        headers["Referer"] = REFERERS[platform]
    if platform in ("xiaohongshu", "douyin", "kuaishou"):
        headers["User-Agent"] = MOBILE_UA

    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "http_headers": headers,
        "skip_download": True,
    }
    if PROXY:
        opts["proxy"] = PROXY
    if COOKIE_FILE:
        opts["cookiefile"] = COOKIE_FILE

    if platform == "youtube":
        # 后端是单 URL 代理下载，只能直接给浏览器一条 progressive 流，不能合并分离流。
        # 不强制 player_client：强制 tv/mweb 会触发 DRM / PO Token 限制，交给新版 yt-dlp 自选。
        opts["format"] = "best[height<=1080][ext=mp4]/best[height<=1080]/best"

    return opts


def friendly_error(message: str, platform: Optional[str], url: str) -> str:
    low = message.lower()
    if "unsupported url" in low and ("login" in low or "rednote" in low):
        return (
            "小红书分享短链被平台强制跳到登录页了。请在 App 里「分享 → 复制链接」"
            "拿到带 xsec_token 的完整链接，并给后端配置登录 Cookie。"
        )
    if "no video formats found" in low:
        if platform == "xiaohongshu":
            return (
                "小红书没返回视频流：链接缺 xsec_token、token 已过期、该笔记是图文，"
                "或缺少 web_session 登录 Cookie。请复制最新的完整分享链接再试。"
            )
        return "平台没返回可用视频流，可能是链接失效、图文内容或需要登录。"
    if "fresh cookies" in low or (platform == "douyin" and "cookies" in low):
        return (
            "抖音当前抓不了：接口已强制 a_bogus 签名，yt-dlp 的抖音解析器取不到数据。"
            "报错里的「Fresh cookies」是误导，单纯配 Cookie 通常没用；"
            "要恢复得换浏览器拦截或第三方解析通道。B站 / TikTok 不受影响。"
        )
    if "unsupported url" in low and "douyin.com" in low:
        return (
            "抖音把链接重定向到了首页（短链失效或海外机房 IP 被拦）。"
            "请重新复制最新分享链接，或给后端配住宅代理 YTDLP_PROXY。"
        )
    if "sign in" in low or "需要登录" in message or "private video" in low:
        return (
            "平台要求登录验证。后端出口是 Render 机房 IP，容易被判为机器人，"
            "请配置住宅代理 YTDLP_PROXY 并注入登录 Cookie。"
        )
    if "geo-restricted" in low or "may be deleted" in low:
        return "该视频已被删除或存在地区限制，换一个链接试试。"
    if "unable to connect to proxy" in low or "proxyerror" in low or "tunnel connection failed" in low:
        return "网络代理连不上（可能是运行环境自带的 HTTPS_PROXY，或 YTDLP_PROXY 配置有误），请检查代理设置。"
    if "unable to extract" in low or "unsupported url" in low:
        return (
            f"链接没被识别（后端实际拿到：{url[:90]}）。"
            "请确认粘贴的是完整链接；平台接口变动时可重新部署升级 yt-dlp。"
        )
    return f"抓取失败: {message[:180]}"


# ─── API 路由 ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = static_dir / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>视频抓取器</h1><p>请将 index.html 放到 static/ 目录</p>")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "video-grabber"}


@app.get("/api/version")
async def version():
    """诊断端点：一眼看清引擎版本与代理/Cookie 是否生效"""
    return {
        "yt_dlp": yt_dlp.version.__version__,
        "python": sys.version.split()[0],
        "proxy": bool(PROXY),
        "cookies": bool(COOKIE_FILE),
        "uptime_seconds": int(_time.time() - STARTED_AT),
    }


@app.get("/api/warm")
async def warm():
    return {"status": "warmed", "engine": "yt-dlp", "version": yt_dlp.version.__version__}


@app.post("/api/grab")
async def grab_video(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        raw = body.get("url", "").strip()
    else:
        form = await request.form()
        raw = form.get("url", "").strip()

    if not raw:
        raise HTTPException(status_code=400, detail="请提供视频链接")

    loop = asyncio.get_event_loop()
    url, forced_platform = await loop.run_in_executor(None, normalize_url, raw)
    platform = forced_platform or detect_platform(url)
    print(f"[grab] raw={raw[:120]} -> normalized={url[:160]} platform={platform}")

    if platform == "weixin":
        raise HTTPException(
            status_code=400,
            detail="微信视频号暂不支持抓取（需微信登录态）。请使用抖音/小红书/B站/快手/YouTube 等平台链接。",
        )
    if not platform:
        raise HTTPException(status_code=400, detail="无法识别该链接所属平台，请检查链接是否完整")

    try:
        t0 = _time.time()
        ydl_opts = get_ydl_opts(url, platform)
        info = await loop.run_in_executor(None, _extract_info, url, ydl_opts)
        print(f"[grab] {platform} extract took {round(_time.time() - t0, 2)}s")

        if info is None:
            raise HTTPException(status_code=404, detail="无法获取视频信息，请检查链接是否正确")

        formats = info.get("formats", [])
        best_url = None
        best_format = None

        # 优先选自带音视频的 MP4 格式（后端是单 URL 代理下载，无法合并分离流）
        progressive = [
            f for f in formats
            if f.get("vcodec") != "none"
            and f.get("acodec") != "none"
            and f.get("url")
        ]
        if progressive:
            progressive.sort(key=lambda f: f.get("height", 0) or 0, reverse=True)
            for fmt in progressive:
                if (fmt.get("height", 0) or 0) <= 1080:
                    best_url, best_format = fmt["url"], fmt
                    break
            if not best_url:
                best_url, best_format = progressive[-1]["url"], progressive[-1]

        if not best_url:
            video_only = [
                f for f in formats
                if f.get("vcodec") != "none" and f.get("acodec") == "none" and f.get("url")
            ]
            if video_only:
                video_only.sort(key=lambda f: f.get("height", 0) or 0, reverse=True)
                best_url, best_format = video_only[0]["url"], video_only[0]

        if not best_url:
            for fmt in formats:
                if fmt.get("url"):
                    best_url, best_format = fmt["url"], fmt
                    break

        if not best_url:
            raise HTTPException(
                status_code=404,
                detail="未找到可下载的视频格式，可能是图文笔记、私密视频或平台限制"
            )

        return {
            "title": info.get("title", "视频抓取成功"),
            "video_url": best_url,
            "thumbnail": info.get("thumbnail", ""),
            "platform": PLATFORM_NAMES.get(platform, "未知平台"),
            "duration": info.get("duration"),
            "format_info": f"{best_format.get('format_note', '')} {best_format.get('resolution', '')}".strip(),
        }

    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(status_code=400, detail=friendly_error(str(exc), platform, url))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(exc)[:200]}")


def _extract_info(url: str, opts: dict):
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


@app.get("/api/download")
async def download_video(video_url: str, title: str = "video", inline: bool = False, referer: str = ""):
    if not video_url:
        raise HTTPException(status_code=400, detail="缺少视频链接")

    safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)[:100]
    headers = {"User-Agent": DESKTOP_UA}
    if referer:
        headers["Referer"] = referer

    try:
        async with httpx.AsyncClient(
            timeout=60,
            follow_redirects=True,
            proxy=PROXY,
            trust_env=bool(PROXY),
            headers=headers,
        ) as client:
            response = await client.get(video_url)

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"视频源返回错误: {response.status_code}"
                )

            content_type = response.headers.get("content-type", "video/mp4")
            content_length = response.headers.get("content-length")

            from urllib.parse import quote
            disp_type = "inline" if inline else "attachment"
            filename_star = quote(f"{safe_title}.mp4")
            out_headers = {
                "Content-Disposition": f'{disp_type}; filename="video.mp4"; filename*=UTF-8\'\'{filename_star}',
            }
            if content_length:
                out_headers["Content-Length"] = content_length

            return HTMLResponse(
                content=response.content,
                media_type=content_type,
                headers=out_headers,
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="视频下载超时，请稍后重试")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"下载失败: {str(exc)[:200]}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
