"""Agent 附件存储。

为什么单独一份而不复用知识库的 /upload：
知识库上传会触发「导入流水线」（MinerU → 切分 → 向量化 → 入 Qdrant），
而 Agent 会话里的附件只是一次性的理解对象，不该污染知识库。
两份目录互不影响，删掉 Agent 附件也不会动到知识库。

两级地址：
    path        本地绝对路径（output/agent_files/{file_id}/{文件名}），本地解析用
    public_url  公网可访问 URL（传腾讯云 COS），给"只收图片链接"的外部服务用
                （例如 OCR MCP 的 pictureUrl 入参，见 app/agent/mcp/builtin.py）
"""
import json
import mimetypes
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.conf.agent_config import agent_config
from app.core.logger import logger

# 附件在 COS 上的存放前缀（与知识库图片分开，便于整体清理）
COS_KEY_PREFIX = "agent-files"
# 对象存储桶：优先读环境变量，方便不同环境指向不同桶
COS_BUCKET = os.getenv("COS_BUCKET") or "suyin-assistant-1384284140"
# 公网地址有效期（秒）。桶默认私有，必须用签名 URL；过期后重新签名即可，不用重传
COS_URL_EXPIRE_SECONDS = int(os.getenv("COS_URL_EXPIRE_SECONDS") or 600)
# 提前这么久就续签，给外部服务拉图留余量
COS_URL_REFRESH_MARGIN = 60


class PublicUrlUnavailable(RuntimeError):
    """附件传不上公网（COS 未配置 / 上传失败）——需要公网 URL 的下游用不了。"""


@dataclass
class UploadedFile:
    """一次会话Attachment的元数据。"""

    file_id: str
    filename: str
    mime_type: str
    path: str
    size: int

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size": self.size,
        }


def save_upload(content: bytes, filename: str, mime_type: Optional[str] = None) -> UploadedFile:
    """把上传的字节流落到 output/agent_files/{file_id}/ 下。"""
    file_id = f"file_{uuid.uuid4().hex}"
    safe_name = Path(filename or "attachment").name or "attachment"
    target_dir = agent_config.files_dir / file_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    target.write_bytes(content)

    resolved_mime = mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    meta = {
        "file_id": file_id,
        "filename": safe_name,
        "mime_type": resolved_mime,
        "size": len(content),
    }
    (target_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"[file_store] 已保存附件 {safe_name} → {target}（{len(content)} bytes）")
    return UploadedFile(path=str(target), **meta)


def resolve(file_id: str) -> Optional[UploadedFile]:
    """按 file_id 找到附件（进程重启后依然可用）。"""
    if not file_id:
        return None
    target_dir = agent_config.files_dir / file_id
    if not target_dir.is_dir():
        return None

    meta_path = target_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return UploadedFile(path=str(target_dir / meta["filename"]), **meta)
        except Exception as exc:
            logger.warning(f"[file_store] meta.json 解析失败，改用目录扫描：{exc}")

    for child in target_dir.iterdir():
        if child.is_file():
            return UploadedFile(
                file_id=file_id,
                filename=child.name,
                mime_type=mimetypes.guess_type(child.name)[0] or "application/octet-stream",
                path=str(child),
                size=child.stat().st_size,
            )
    return None


def resolve_path(path: str) -> Optional[UploadedFile]:
    """按本地路径反查（供测试与脚本使用）。"""
    file_path = Path(path)
    if not file_path.exists():
        return None
    return UploadedFile(
        file_id=file_path.parent.name,
        filename=file_path.name,
        mime_type=mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
        path=str(file_path),
        size=file_path.stat().st_size,
    )


def public_url(file: UploadedFile, refresh: bool = False) -> str:
    """把附件换成公网可访问 URL（腾讯云 COS 签名地址），供外部服务自己去拉。

    为什么需要它：有些 OCR MCP 的工具入参是"图片链接"而不是文件内容本身
    （builtin.py 里 adapter.input_mode=url）。外部服务拉不到图时，
    报错往往不是 403 而是"请求超时"，很难查——所以这里做三件事：

      1. 桶默认私有，裸对象地址会 403，必须用签名 URL（带有效期）；
      2. 上传结果与签名 URL 一起缓存进 meta.json，快过期只重新签名、不重传；
      3. 生成后自己先拉一次（`_url_reachable`），拉不动就直接报错，
         不让外部服务那边表现成一句含糊的"请求超时"。
    """
    meta_path = Path(file.path).parent / "meta.json"
    meta = _read_meta(meta_path)

    cos_client = _cos_client()
    key = str(meta.get("cos_key") or f"{COS_KEY_PREFIX}/{file.file_id}/{file.filename}")
    cached_valid = bool(meta.get("public_url")) and _url_still_valid(meta)
    if not refresh and cached_valid:
        # 缓存里的签名还没过期，直接用；上传与签名都省掉
        url = str(meta["public_url"])
        uploaded = True
    else:
        uploaded = meta.get("cos_key") == key
        url = _upload_and_sign(cos_client, key, file.path, upload=not uploaded)

    # 每次都验一次可达性：对象被删、桶权限被改，只有真去拉一次才知道。
    # 外部服务遇到 403 往往报成"请求超时"，等它反馈就太晚了。
    reachable, detail = _url_reachable(url)
    if not reachable:
        logger.warning(f"[file_store] 外链拉不通（{detail}），重传一次：{key}")
        url = _upload_and_sign(cos_client, key, file.path, upload=True)
        reachable, detail = _url_reachable(url)
    if not reachable:
        raise PublicUrlUnavailable(
            f"生成的公网地址自己都拉不通（{detail}）：{url.split('?')[0]}"
        )

    _write_meta(
        meta_path,
        {
            "cos_key": key,
            "public_url": url,
            "public_url_expires_at": time.time() + COS_URL_EXPIRE_SECONDS,
        },
    )
    logger.info(
        f"[file_store] 附件 {file.filename} 公网地址就绪（{COS_URL_EXPIRE_SECONDS}s 有效）："
        f"{url.split('?')[0]}"
    )
    return url


def _upload_and_sign(cos_client, key: str, local_path: str, upload: bool) -> str:
    """上传（可选）+ 生成签名 URL。签名是本地计算，不需要网络。"""
    try:
        if upload:
            cos_client.upload_file(
                Bucket=COS_BUCKET,
                Key=key,
                LocalFilePath=local_path,
                EnableMD5=False,
            )
        return cos_client.get_presigned_url(
            Bucket=COS_BUCKET, Key=key, Method="GET", Expired=COS_URL_EXPIRE_SECONDS
        )
    except Exception as exc:
        raise PublicUrlUnavailable(f"上传 COS / 生成签名地址失败：{exc}") from exc


def _cos_client():
    """延迟导入 COS 客户端：没装 qcloud_cos 或没配密钥时只影响这一条通路。"""
    try:
        from app.conf.cos_config import client
    except Exception as exc:
        raise PublicUrlUnavailable(
            f"COS 客户端不可用（检查 COS_SECRET_ID / COS_SECRET_KEY）：{exc}"
        ) from exc
    return client


def _read_meta(meta_path: Path) -> dict:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_meta(meta_path: Path, updates: dict) -> None:
    """把 COS 相关字段记进 meta.json；写不进去也不影响本次调用。"""
    try:
        meta = _read_meta(meta_path)
        meta.update(updates)
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning(f"[file_store] 公网地址写缓存失败（不影响本次调用）：{exc}")


def _url_still_valid(meta: dict) -> bool:
    """签名是否还在有效期内（留出续签余量）。"""
    try:
        expires_at = float(meta.get("public_url_expires_at") or 0)
    except (TypeError, ValueError):
        return False
    return expires_at - time.time() > COS_URL_REFRESH_MARGIN


def _url_reachable(url: str) -> tuple[bool, str]:
    """自己先拉一次，确认外部服务也能拿到图（拉不到就没必要调 OCR 了）。"""
    try:
        import requests

        response = requests.get(url, timeout=10, stream=True)
        with response:
            if response.status_code == 200:
                next(response.iter_content(1024), b"")
                return True, "200"
            return False, f"HTTP {response.status_code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
