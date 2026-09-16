"""Memos API 客户端

与 Memos 实例通信（服务地址在配置页填写），拉取 / 增量获取 memo 数据。
使用 requests.Session() + verify=False 处理 SSL 自签名证书。

默认跳过纯图片/附件 memo（content 仅由 Markdown 图片语法或文件链接组成）。
"""

import logging
import re
import time
import urllib3
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── 北京时间时区 ──
_BEIJING_TZ = timezone(timedelta(hours=8))

# ── 附件/图片检测 ──
_ATTACHMENT_PATTERNS = [
    r'!\[.*?\]\(.*?\)',          # Markdown 图片 ![](...)
    r'\[.*?\]\(file://.*?\)',    # 文件链接
    r'<img\b[^>]*>',            # HTML img 标签
    r'<video\b[^>]*>.*?</video>',  # HTML video
]
_ATTACHMENT_RE = re.compile('|'.join(_ATTACHMENT_PATTERNS), re.IGNORECASE | re.DOTALL)


def _is_attachment_only(content: str) -> bool:
    """判断 memo 内容是否仅为图片/附件（无实质文本）

    智能判断（决策 3）：
    - 包含中文：阈值 10 字符（中文信息密度高）
    - 纯英文/数字：阈值 20 字符（英文信息密度低）

    Args:
        content: memo 原始内容

    Returns:
        True 表示该 memo 仅含附件，应跳过
    """
    if not content or not content.strip():
        return True
    stripped = _ATTACHMENT_RE.sub('', content).strip()
    
    # 检查是否包含中文字符（Unicode 范围 U+4E00 到 U+9FFF）
    has_chinese = any('\u4e00' <= char <= '\u9fff' for char in stripped)
    
    if has_chinese:
        # 包含中文：阈值 10 字符
        return len(stripped) < 10
    else:
        # 纯英文/数字：阈值 20 字符
        return len(stripped) < 20


def _filter_attachments(memos: list[dict]) -> tuple[list[dict], int]:
    """过滤掉纯附件 memo

    Returns:
        (filtered_memos, skipped_count)
    """
    filtered = []
    skipped = 0
    for m in memos:
        content = m.get('content', '') or ''
        if _is_attachment_only(content):
            skipped += 1
            continue
        filtered.append(m)
    return filtered, skipped


def _utc_to_beijing(utc_str: str) -> datetime:
    """将 UTC ISO 时间字符串转换为北京时间的 datetime 对象"""
    dt = datetime.fromisoformat(utc_str.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BEIJING_TZ)


def list_memos(
    base_url: str,
    access_token: str = '',
    page_size: int = 50,
    page_token: str = '',
    filter_str: str = '',
) -> dict:
    """单页拉取 Memos API

    Args:
        base_url: Memos 服务地址
        access_token: 访问令牌
        page_size: 每页条数
        page_token: 分页 token
        filter_str: 过滤条件

    Returns:
        {'memos': [...], 'nextPageToken': str}
    """
    session = requests.Session()
    session.verify = False

    headers = {'Content-Type': 'application/json'}
    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'

    params = {'pageSize': page_size}
    if page_token:
        params['pageToken'] = page_token
    if filter_str:
        params['filter'] = filter_str

    url = f'{base_url.rstrip("/")}/api/v1/memos'
    resp = session.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    session.close()
    return data


def fetch_all_memos(
    base_url: str,
    access_token: str = '',
    page_size: int = 50,
    max_pages: int = 50,
    skip_attachments: bool = True,
) -> list[dict]:
    """全量拉取所有 memos

    Args:
        base_url: Memos 服务地址
        access_token: 访问令牌
        page_size: 每页条数
        max_pages: 最大翻页数（安全上限）
        skip_attachments: 是否跳过纯图片/附件 memo（默认 True）

    Returns:
        所有 memo 的列表，每条为 dict
    """
    all_memos = []
    page_token = ''
    page_count = 0
    skipped_total = 0

    while page_count < max_pages:
        data = list_memos(
            base_url=base_url,
            access_token=access_token,
            page_size=page_size,
            page_token=page_token,
        )
        memos = data.get('memos', [])
        if not memos:
            break
        if skip_attachments:
            memos, skipped = _filter_attachments(memos)
            skipped_total += skipped
        all_memos.extend(memos)
        page_token = data.get('nextPageToken', '')
        page_count += 1
        if not page_token:
            break
        time.sleep(0.3)  # 礼貌节流

    if skip_attachments and skipped_total:
        logger.info(
            f"[MemosClient] 拉取 {len(all_memos)} 条 memo，跳过 {skipped_total} 条纯附件"
        )

    return all_memos


def fetch_updated_memos(
    base_url: str,
    access_token: str = '',
    since: Optional[datetime] = None,
    page_size: int = 50,
    skip_attachments: bool = True,
) -> list[dict]:
    """增量拉取：获取自某个时间点以来有更新的 memos

    Args:
        base_url: Memos 服务地址
        access_token: 访问令牌
        since: 增量起始时间（北京时间），None 则全量
        page_size: 每页条数
        skip_attachments: 是否跳过纯图片/附件 memo（默认 True）

    Returns:
        所有有更新的 memo 列表
    """
    filter_str = ''
    if since:
        # Memos API filter 使用 ISO 格式
        since_str = since.strftime('%Y-%m-%dT%H:%M:%S+08:00')
        filter_str = f'updateTime >= "{since_str}"'

    return fetch_all_memos(
        base_url=base_url,
        access_token=access_token,
        page_size=page_size,
        skip_attachments=skip_attachments,
    ) if not filter_str else _fetch_with_filter(
        base_url=base_url,
        access_token=access_token,
        filter_str=filter_str,
        page_size=page_size,
        skip_attachments=skip_attachments,
    )


def _fetch_with_filter(
    base_url: str,
    access_token: str,
    filter_str: str,
    page_size: int = 50,
    max_pages: int = 50,
    skip_attachments: bool = True,
) -> list[dict]:
    """带 filter 的全量拉取"""
    all_memos = []
    page_token = ''
    page_count = 0

    while page_count < max_pages:
        data = list_memos(
            base_url=base_url,
            access_token=access_token,
            page_size=page_size,
            page_token=page_token,
            filter_str=filter_str,
        )
        memos = data.get('memos', [])
        if not memos:
            break
        if skip_attachments:
            memos, _ = _filter_attachments(memos)
        all_memos.extend(memos)
        page_token = data.get('nextPageToken', '')
        page_count += 1
        if not page_token:
            break
        time.sleep(0.3)

    return all_memos
