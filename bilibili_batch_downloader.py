#!/usr/bin/env python3
"""
B站视频批量下载器 V4
支持从ugc_season合集字段获取完整系列视频

用法:
  python bilibili_batch_downloader.py <BV号或URL>
"""

import subprocess
import json
import re
from pathlib import Path
import sys

# Cookie配置
COOKIE = "buvid4=D21E6012-4A38-B23C-2BC5-7961BE48BEDE62503-024092215-3n5xeHPj8bn9aScYIf2pzg%3D%3D; SESSDATA=02e002c7%2C1778164764%2C4ad7b%2Ab1CjAFHRTtmUbXSwancqb8IOrEITiLH-OCPDF8YgnZZoJyUC4S2hy63a6JiY0UlRuu-lMSVnlzWVBxRUFOOUx3bmZQZEF1RnlnRGxhNlprLXZLejQwTmtvMWdjNm9mTldXd3M0ZHVCWGJVdzVmb2FuOGpkalc5WHhycnJQdDlKMFNzZ0U2TkZkN19RIIEC; bili_jct=169d89ed657d4564dd1e190a04ec1acd"

def api_get(url):
    """API GET请求"""
    cmd = ['curl', '-s', url,
           '-H', 'User-Agent: Mozilla/5.0',
           '-H', f'Cookie: {COOKIE}',
           '--max-time', '20']
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(result.stdout)
    except:
        return {}

def get_video_info(bvid):
    """获取视频信息，包括ugc_season合集"""
    data = api_get(f'https://api.bilibili.com/x/web-interface/view?bvid={bvid}')
    if data.get('code') == 0:
        info = data['data']
        result = {
            'bvid': bvid,
            'aid': info['aid'],
            'title': info['title'],
            'owner': info['owner']['name'],
            'pages': [{'cid': p['cid'], 'title': p['part'], 'page': p['page']} for p in info['pages']],
            'videos': info['videos'],
            'ugc_season': info.get('ugc_season'),  # 合集信息
        }
        return result
    return None

def extract_series_videos(ugc_season):
    """从ugc_season提取所有视频"""
    videos = []
    sections = ugc_season.get('sections', [])
    for section in sections:
        episodes = section.get('episodes', [])
        for ep in episodes:
            videos.append({
                'bvid': ep.get('bvid'),
                'title': ep.get('title', ''),
                'cid': ep.get('cid'),
            })
    return videos

def get_download_url(aid, cid):
    """获取下载链接"""
    data = api_get(f'https://api.bilibili.com/x/player/playurl?avid={aid}&cid={cid}&qn=80&fnval=4048&fnver=0&fourk=1')
    if data.get('code') == 0:
        dash = data['data'].get('dash', {})
        video_list = dash.get('video', [])
        audio_list = dash.get('audio', [])
        if video_list:
            return {
                'video_url': video_list[0]['baseUrl'],
                'audio_url': audio_list[0]['baseUrl'] if audio_list else None,
            }
    return None

def download_file(url, output_path):
    """下载文件"""
    if not url:
        return False
    cmd = ['curl', '-L', '-C', '-', '-o', str(output_path),
           '-H', 'Referer: https://www.bilibili.com',
           '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
           '--max-time', '600', url]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0

def merge_video_audio(video_path, audio_path, output_path):
    """合并视频音频"""
    if not audio_path or not Path(audio_path).exists():
        if str(video_path) != str(output_path):
            Path(video_path).rename(output_path)
        return True
    cmd = ['ffmpeg', '-y', '-i', str(video_path), '-i', str(audio_path),
           '-c:v', 'copy', '-c:a', 'copy', '-shortest', str(output_path)]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0

def download_single(bvid, cid, title, output_dir, index, total):
    """下载单个视频"""
    print(f"\n[{index}/{total}] {title[:50]}...")

    info = get_video_info(bvid)
    if not info:
        print(f"  ❌ 获取视频信息失败")
        return False

    urls = get_download_url(info['aid'], cid)
    if not urls:
        print(f"  ❌ 获取下载链接失败")
        return False

    safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)[:60]
    video_path = output_dir / f'{safe_title}_v.m4s'
    audio_path = output_dir / f'{safe_title}_a.m4s'
    final_path = output_dir / f'{safe_title}.mp4'

    print(f"  📥 下载视频...")
    if not download_file(urls['video_url'], video_path):
        print(f"  ❌ 视频下载失败")
        return False

    if urls.get('audio_url'):
        print(f"  📥 下载音频...")
        download_file(urls['audio_url'], audio_path)

    print(f"  🔄 合并...")
    if merge_video_audio(video_path, audio_path, final_path):
        for f in [video_path, audio_path]:
            if f.exists():
                f.unlink()
        size = final_path.stat().st_size / 1024 / 1024
        print(f"  ✅ ({size:.1f}MB)")
        return True
    return False

def download_video(bvid, output_dir='./downloads'):
    """下载视频（支持ugc_season合集）"""
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    print("=" * 60)
    print(f"B站视频下载: {bvid}")
    print("=" * 60)

    info = get_video_info(bvid)
    if not info:
        print("❌ 获取视频信息失败")
        return False

    print(f"\n标题: {info['title']}")
    print(f"UP主: {info['owner']}")

    # 检查是否有ugc_season合集
    ugc_season = info.get('ugc_season')
    if ugc_season:
        season_title = ugc_season.get('title', 'unknown')
        videos = extract_series_videos(ugc_season)
        print(f"\n📺 检测到合集: {season_title}")
        print(f"📚 合集包含 {len(videos)} 个视频")
        playlist_dir = output_dir / re.sub(r'[<>:"/\\|?*]', '_', season_title)[:50]
    else:
        videos = info['pages']
        print(f"\n📹 普通视频，共 {len(videos)} P")
        playlist_dir = output_dir / re.sub(r'[<>:"/\\|?*]', '_', info['title'])[:50]

    playlist_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n保存到: {playlist_dir}")
    print("-" * 60)

    success = 0
    for i, video in enumerate(videos, 1):
        if download_single(video['bvid'], video['cid'], video['title'], playlist_dir, i, len(videos)):
            success += 1

    print("-" * 60)
    print(f"✅ 完成: {success}/{len(videos)} 个视频")
    return success > 0

def extract_bvid(url_or_bvid):
    """从URL或纯BV号提取BV号"""
    if 'bilibili.com' in url_or_bvid:
        match = re.search(r'BV[a-zA-Z0-9]+', url_or_bvid)
        return match.group(0) if match else None
    elif url_or_bvid.startswith('BV'):
        return url_or_bvid
    elif url_or_bvid.startswith('bv', re.I):
        return 'BV' + url_or_bvid[2:].upper()
    return None

def main():
    if len(sys.argv) < 2:
        print("""
╔══════════════════════════════════════════════════════════════╗
║           B站视频批量下载器 V4                           ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  用法:                                                       ║
║    python bilibili_batch_downloader.py <BV号或URL>           ║
║                                                              ║
║  示例:                                                       ║
║    python bilibili_batch_downloader.py BV1xxx                ║
║    python bilibili_batch_downloader.py https://b23.tv/xxx      ║
║                                                              ║
║  特点:                                                       ║
║    • 自动检测ugc_season合集字段                             ║
║    • 自动下载完整合集系列                                   ║
║    • 支持普通多P视频下载                                     ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
        """)
        sys.exit(1)

    bvid = extract_bvid(sys.argv[1])
    if not bvid:
        print("❌ 无法识别BV号，请检查输入格式")
        sys.exit(1)

    output_dir = sys.argv[2] if len(sys.argv) > 2 else './downloads'
    download_video(bvid, output_dir)

if __name__ == '__main__':
    main()
