"""确定性内容去重。"""
import hashlib


def md5_text(text: str) -> str:
    text = (text or "").strip()
    return hashlib.md5(text.encode("utf-8")).hexdigest()
