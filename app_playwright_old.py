"""
视频抓取器 - 无水印视频下载工具
支持: 微信视频号 | 抖音 | 小红书
"""

import os
import re
import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

import uvicorn
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
from playwright.async_api import async_playwright

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 创建FastAPI应用
app = FastAPI(title="视频抓取器", version="1.0.0")

# CORS - 允许前端跨域调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 创建目录
UPLOAD_DIR = Path("downloads")
UPLOAD_DIR.mkdir(exist_ok=True)

# 模板和静态文件
templates = Jinja2Templates(directory="templates")

# 浏览器池
browser_pool = None


class VideoGrabber:
    """视频抓取器核心类"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
    
    async def grab_video(self, url: str, platform: str = None) -> Dict[str, Any]:
        """
        抓取视频
        
        Args:
            url: 视频链接
            platform: 平台类型 (wechat/douyin/xiaohongshu/auto)
            
        Returns:
            包含视频信息的字典
        """
        # 自动识别平台
        if platform == 'auto' or not platform:
            platform = self._detect_platform(url)
        
        logger.info(f"检测到平台: {platform}, URL: {url}")
        
        try:
            if platform == 'wechat':
                return await self._grab_wechat(url)
            elif platform == 'douyin':
                return await self._grab_douyin(url)
            elif platform == 'xiaohongshu':
                return await self._grab_xiaohongshu(url)
            else:
                return {'success': False, 'error': f'不支持的平台: {platform}'}
        except Exception as e:
            logger.error(f"抓取失败: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def _detect_platform(self, url: str) -> str:
        """自动检测平台"""
        url_lower = url.lower()
        
        if 'weixin.qq.com' in url_lower or 'channels' in url_lower:
            return 'wechat'
        elif 'douyin.com' in url_lower or 'iesdouyin.com' in url_lower:
            return 'douyin'
        elif 'xiaohongshu.com' in url_lower or 'xhslink.com' in url_lower:
            return 'xiaohongshu'
        else:
            return 'unknown'
    
    async def _grab_wechat(self, url: str) -> Dict[str, Any]:
        """抓取微信视频号"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=self.headers['User-Agent'],
                    viewport={'width': 1920, 'height': 1080}
                )
                page = await context.new_page()
                
                # 存储视频URL
                video_urls = []
                
                # 监听网络请求
                async def handle_response(response):
                    content_type = response.headers.get('content-type', '')
                    if 'video' in content_type or response.url.endswith('.mp4'):
                        video_urls.append(response.url)
                
                page.on('response', handle_response)
                
                # 访问页面
                await page.goto(url, wait_until='networkidle', timeout=30000)
                await asyncio.sleep(3)
                
                # 获取标题
                title = await page.title()
                
                # 尝试点击播放
                try:
                    play_btn = await page.query_selector('[class*="play"], .video-play-btn')
                    if play_btn:
                        await play_btn.click()
                        await asyncio.sleep(2)
                except:
                    pass
                
                # 从页面提取视频URL
                page_content = await page.content()
                
                # 正则匹配视频URL
                patterns = [
                    r'"url"\s*:\s*"(https?://[^"]*\.mp4[^"]*)"',
                    r'"video_url"\s*:\s*"(https?://[^"]*)"',
                    r'https?://wxvideo[^"]*\.mp4[^\s"]*',
                    r'https?://[^"]*video[^"]*\.mp4[^\s"]*',
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, page_content)
                    for match in matches:
                        if isinstance(match, tuple):
                            match = match[0]
                        if match.startswith('http') and match not in video_urls:
                            video_urls.append(match)
                
                await browser.close()
                
                if video_urls:
                    # 选择最佳视频URL
                    video_url = self._select_best_video(video_urls)
                    return {
                        'success': True,
                        'title': title or f'微信视频_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
                        'url': url,
                        'video_url': video_url,
                        'platform': 'wechat',
                        'watermark': False
                    }
                else:
                    return {'success': False, 'error': '未找到视频资源'}
                    
        except Exception as e:
            return {'success': False, 'error': f'微信视频号抓取失败: {str(e)}'}
    
    async def _grab_douyin(self, url: str) -> Dict[str, Any]:
        """抓取抖音视频（去除水印）"""
        try:
            # 抖音分享链接通常需要先解析重定向
            async with httpx.AsyncClient() as client:
                # 处理短链接
                if 'v.douyin.com' in url:
                    resp = await client.get(url, follow_redirects=True, headers=self.headers)
                    url = str(resp.url)
                
                # 提取视频ID
                video_id = self._extract_douyin_id(url)
                
                if not video_id:
                    return {'success': False, 'error': '无法提取视频ID'}
                
                # 构造无水印URL
                # 抖音无水印视频API
                api_url = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={video_id}"
                
                resp = await client.get(api_url, headers=self.headers)
                data = resp.json()
                
                if data.get('item_list'):
                    item = data['item_list'][0]
                    video_info = item.get('video', {})
                    
                    # 获取无水印URL
                    play_addr = video_info.get('play_addr', {})
                    video_url = play_addr.get('url_list', [None])[0]
                    
                    # 替换水印域名
                    if video_url:
                        video_url = video_url.replace('playwm', 'play')
                    
                    return {
                        'success': True,
                        'title': item.get('desc', f'抖音视频_{datetime.now().strftime("%Y%m%d_%H%M%S")}'),
                        'url': url,
                        'video_url': video_url,
                        'platform': 'douyin',
                        'watermark': False,
                        'author': item.get('author', {}).get('nickname', ''),
                        'duration': video_info.get('duration', 0)
                    }
                
                return {'success': False, 'error': '未找到视频信息'}
                
        except Exception as e:
            return {'success': False, 'error': f'抖音视频抓取失败: {str(e)}'}
    
    def _extract_douyin_id(self, url: str) -> Optional[str]:
        """提取抖音视频ID"""
        patterns = [
            r'/video/(\d+)',
            r'/note/(\d+)',
            r'item_ids=(\d+)',
            r'modal_id=(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    async def _grab_xiaohongshu(self, url: str) -> Dict[str, Any]:
        """抓取小红书视频（去除水印）"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=self.headers['User-Agent']
                )
                page = await context.new_page()
                
                # 存储视频URL
                video_urls = []
                
                # 监听网络请求
                async def handle_response(response):
                    content_type = response.headers.get('content-type', '')
                    url = response.url
                    if ('video' in content_type or 
                        '.mp4' in url or 
                        'sns-video' in url or
                        'xhscdn' in url):
                        if url.startswith('http'):
                            video_urls.append(url)
                
                page.on('response', handle_response)
                
                # 处理短链接
                if 'xhslink.com' in url:
                    resp = await page.goto(url, wait_until='networkidle', timeout=30000)
                    url = page.url
                
                # 访问页面
                await page.goto(url, wait_until='networkidle', timeout=30000)
                await asyncio.sleep(3)
                
                # 获取标题
                title = ''
                try:
                    title_elem = await page.query_selector('.title, [class*="title"], #detail-title')
                    if title_elem:
                        title = await title_elem.inner_text()
                except:
                    pass
                
                if not title:
                    title = await page.title()
                
                # 尝试点击播放
                try:
                    play_btn = await page.query_selector('[class*="play"], .play-btn')
                    if play_btn:
                        await play_btn.click()
                        await asyncio.sleep(2)
                except:
                    pass
                
                # 从页面源码提取
                page_content = await page.content()
                
                # 正则匹配
                patterns = [
                    r'"originVideoKey"\s*:\s*"([^"]+)"',
                    r'"videoUrl"\s*:\s*"([^"]+)"',
                    r'https?://sns-video[^"]*\.mp4[^\s"]*',
                    r'https?://[^"]*xhscdn[^"]*video[^"]*\.mp4[^\s"]*',
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, page_content)
                    for match in matches:
                        if match.startswith('http') and match not in video_urls:
                            video_urls.append(match)
                        elif not match.startswith('http'):
                            # 可能是key，构造URL
                            video_url = f"https://sns-video/{match}"
                            if video_url not in video_urls:
                                video_urls.append(video_url)
                
                await browser.close()
                
                if video_urls:
                    # 选择最佳视频
                    video_url = self._select_best_video(video_urls)
                    
                    # 清理URL中的水印参数
                    video_url = self._remove_watermark(video_url)
                    
                    return {
                        'success': True,
                        'title': title or f'小红书视频_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
                        'url': url,
                        'video_url': video_url,
                        'platform': 'xiaohongshu',
                        'watermark': False
                    }
                else:
                    return {'success': False, 'error': '未找到视频资源'}
                    
        except Exception as e:
            return {'success': False, 'error': f'小红书视频抓取失败: {str(e)}'}
    
    def _select_best_video(self, urls: list) -> str:
        """选择最佳视频URL"""
        if not urls:
            return ''
        
        # 优先选择无水印的URL
        for url in urls:
            if 'playwm' not in url and 'watermark' not in url.lower():
                return url
        
        # 否则返回第一个
        return urls[0]
    
    def _remove_watermark(self, url: str) -> str:
        """移除URL中的水印参数"""
        # 移除常见的水印参数
        url = re.sub(r'[?&]watermark=[^&]*', '', url)
        url = re.sub(r'[?&]wm=[^&]*', '', url)
        url = url.replace('playwm', 'play')
        return url


# 全局抓取器实例
grabber = VideoGrabber()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """主页"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/grab")
async def grab_video(
    url: str = Form(...),
    platform: str = Form("auto")
):
    """
    抓取视频API
    
    Args:
        url: 视频链接
        platform: 平台类型 (auto/wechat/douyin/xiaohongshu)
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL不能为空")
    
    # 验证URL
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    result = await grabber.grab_video(url, platform)
    
    if result['success']:
        return JSONResponse(content=result)
    else:
        raise HTTPException(status_code=400, detail=result['error'])


@app.get("/api/download")
async def download_video(video_url: str, title: str = "video"):
    """
    下载视频
    
    Args:
        video_url: 视频URL
        title: 视频标题
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(video_url, headers=grabber.headers)
            
            if resp.status_code == 200:
                # 保存文件
                filename = f"{title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
                filepath = UPLOAD_DIR / filename
                
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                
                return FileResponse(
                    path=filepath,
                    filename=filename,
                    media_type="video/mp4"
                )
            else:
                raise HTTPException(status_code=400, detail="下载失败")
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    print("视频抓取器启动中...")
    print("访问 http://localhost:8089 使用")
    uvicorn.run(app, host="0.0.0.0", port=8089)
