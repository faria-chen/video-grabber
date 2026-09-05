"""
视频抓取器后端 - 支持抖音/B站/小红书/YouTube/快手等主流平台
基于 FastAPI + yt-dlp
"""

import os
import re
import json
import asyncio
import tempfile
from pathlib import Path
from urllib.parse import urlparse, unquote
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx
import yt_dlp

app = FastAPI(title="视频抓取器", docs_url=None, redoc_url=None)

# ─── 预热 + Keep-Alive ────────────────────────────────────────────────
import threading, time as _time

def _warmup_sync():
    """后台线程：预热 yt-dlp + 周期性 ping 防休眠"""
    try:
        import yt_dlp
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

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ─── 平台识别 ────────────────────────────────────────────────────────
PLATFORM_PATTERNS = {
    "douyin": [r"douyin\.com", r"v\.douyin\.com"],
    "bilibili": [r"bilibili\.com", r"b23\.tv"],
    "xiaohongshu": [r"xiaohongshu\.com", r"xhslink\.com", r"xhs\.com"],
    "kuaishou": [r"kuaishou\.com", r"v\.kuaishou\.com"],
    "tiktok": [r"tiktok\.com"],
    "youtube": [r"youtube\.com", r"youtu\.be"],
    "twitter": [r"twitter\.com", r"x\.com"],
    "instagram": [r"instagram\.com"],
    "weibo": [r"weibo\.com", r"m\.weibo\.cn"],
    "vimeo": [r"vimeo\.com"],
    "facebook": [r"facebook\.com", r"fb\.watch"],
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
}


def detect_platform(url: str) -> Optional[str]:
    """自动识别视频平台"""
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return platform
    return None


# ─── yt-dlp 配置 ──────────────────────────────────────────────────────

def get_ydl_opts(url: str, platform: Optional[str] = None) -> dict:
    """根据平台返回 yt-dlp 配置"""
    base_opts = {
        "quiet": True,
        "no_warnings": True,
                "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.google.com/",
        },
    }

    # 平台特定配置
    if platform == "douyin":
        base_opts["http_headers"]["Cookie"] = ""
    elif platform == "bilibili":
        base_opts["http_headers"]["Referer"] = "https://www.bilibili.com/"
    elif platform == "xiaohongshu":
        base_opts["http_headers"]["Referer"] = "https://www.xiaohongshu.com/"
    elif platform == "youtube":
        base_opts["format"] = "best[height<=1080]/best"
    elif platform == "kuaishou":
        base_opts["http_headers"]["Referer"] = "https://www.kuaishou.com/"

    return base_opts


# ─── API 路由 ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """前端页面"""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>视频抓取器</h1><p>请将 index.html 放到 static/ 目录</p>")


@app.get("/api/health")
async def health():
    """健康检查"""
    return {"status": "ok", "service": "video-grabber"}


@app.get("/api/warm")
async def warm():
    """预热端点：确保 yt-dlp 已加载到内存"""
    import yt_dlp as _ydl  # noqa: F401
    return {"status": "warmed", "engine": "yt-dlp"}


@app.post("/api/grab")
async def grab_video(request: Request):
    """
    抓取视频信息
    兼容旧前端的 FormData 格式，也支持 JSON
    """
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body = await request.json()
        url = body.get("url", "").strip()
    else:
        form = await request.form()
        url = form.get("url", "").strip()

    if not url:
        raise HTTPException(status_code=400, detail="请提供视频链接")

    # 尝试提取 URL（用户可能粘贴了带文字的内容）
    url_match = re.search(r'https?://[^\s<>"]+', url)
    if url_match:
        url = url_match.group(0)

    platform = detect_platform(url)

    try:
        import time
        t0 = time.time()
        ydl_opts = get_ydl_opts(url, platform)
        ydl_opts["skip_download"] = True

        # 在线程池中执行 yt-dlp（避免阻塞事件循环）
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, _extract_info, url, ydl_opts)
        elapsed = round(time.time() - t0, 2)
        print(f"[grab] {platform or 'unknown'} extract took {elapsed}s")

        if info is None:
            raise HTTPException(status_code=404, detail="无法获取视频信息，请检查链接是否正确")

        # 选择最佳格式
        formats = info.get("formats", [])
        best_url = None
        best_format = None

        # 优先选 MP4 视频
        video_formats = [
            f for f in formats
            if f.get("vcodec") != "none"
            and f.get("acodec") != "none"
            and f.get("url")
        ]

        if video_formats:
            # 按分辨率排序，选最高
            video_formats.sort(
                key=lambda f: f.get("height", 0) or 0, reverse=True
            )
            # 限制最高 1080p
            for vf in video_formats:
                h = vf.get("height", 0) or 0
                if h <= 1080:
                    best_url = vf["url"]
                    best_format = vf
                    break
            if not best_url and video_formats:
                best_url = video_formats[-1]["url"]
                best_format = video_formats[-1]

        # 如果没有合并格式，尝试合并视频+音频
        if not best_url:
            # 纯视频
            vid_fmts = [
                f for f in formats
                if f.get("vcodec") != "none" and f.get("acodec") == "none" and f.get("url")
            ]
            # 纯音频
            aud_fmts = [
                f for f in formats
                if f.get("vcodec") == "none" and f.get("acodec") != "none" and f.get("url")
            ]
            if vid_fmts:
                vid_fmts.sort(key=lambda f: f.get("height", 0) or 0, reverse=True)
                best_url = vid_fmts[0]["url"]
                best_format = vid_fmts[0]

        if not best_url:
            # 最后手段：任何有 URL 的格式
            for f in formats:
                if f.get("url"):
                    best_url = f["url"]
                    best_format = f
                    break

        if not best_url:
            raise HTTPException(
                status_code=404,
                detail="未找到可下载的视频格式，可能是私密视频或平台限制"
            )

        title = info.get("title", "视频抓取成功")
        thumbnail = info.get("thumbnail", "")
        duration = info.get("duration")
        platform_name = PLATFORM_NAMES.get(platform, "未知平台")

        return {
            "title": title,
            "video_url": best_url,
            "thumbnail": thumbnail,
            "platform": platform_name,
            "duration": duration,
            "format_info": f"{best_format.get('format_note', '')} {best_format.get('resolution', '')}".strip(),
        }

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "Unable to extract" in error_msg:
            detail = "无法解析该链接，可能平台暂不支持或链接已失效"
        elif "Private video" in error_msg or "私密" in error_msg:
            detail = "该视频为私密视频，无法抓取"
        elif "Sign in" in error_msg:
            detail = "该视频需要登录才能访问"
        else:
            detail = f"抓取失败: {error_msg[:200]}"
        raise HTTPException(status_code=400, detail=detail)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)[:200]}")


def _extract_info(url: str, opts: dict):
    """同步提取视频信息"""
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


@app.get("/api/download")
async def download_video(video_url: str, title: str = "video"):
    """
    代理下载视频（解决浏览器跨域问题）
    """
    if not video_url:
        raise HTTPException(status_code=400, detail="缺少视频链接")

    safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)[:100]

    try:
        async with httpx.AsyncClient(
            timeout=60,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            response = await client.get(video_url)

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"视频源返回错误: {response.status_code}"
                )

            content_type = response.headers.get("content-type", "video/mp4")
            content_length = response.headers.get("content-length")

            headers = {
                "Content-Disposition": f'attachment; filename="{safe_title}.mp4"',
            }
            if content_length:
                headers["Content-Length"] = content_length

            return HTMLResponse(
                content=response.content,
                media_type=content_type,
                headers=headers,
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="视频下载超时，请稍后重试")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载失败: {str(e)[:200]}")


# ─── 入口 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
