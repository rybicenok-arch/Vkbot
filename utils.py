import re
import time
from typing import Optional


def esc(s) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_dur(sec) -> str:
    try:
        sec = int(sec or 0)
    except (TypeError, ValueError):
        sec = 0
    if sec <= 0:
        return "—"
    m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_rel(ts) -> str:
    """Дата добавления: «сегодня», «5д», «12.03.25»…"""
    if not ts:
        return ""
    d = time.time() - int(ts)
    if d < 3600:
        return "только что"
    if d < 86400:
        return "сегодня"
    if d < 172800:
        return "вчера"
    if d < 2592000:
        return f"{int(d // 86400)}д"
    if d < 31536000:
        return f"{int(d // 2592000)}мес"
    return time.strftime("%d.%m.%y", time.localtime(int(ts)))


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", str(s or "track")).strip()[:60] or "track"


def fmt_window(sec: int) -> str:
    """«в минуту» / «за 3 мин» и т.п. для текстов лимита."""
    if sec < 120:
        return f"{sec // 60 or 1} мин" if sec >= 60 else f"{sec} сек"
    return f"{sec // 60} мин"


_VK_RE = re.compile(r"(?:https?://)?(?:m\.)?vk\.com/(?:id)?([A-Za-z0-9_.\-]+)",
                    re.IGNORECASE)


def parse_vk_ref(text) -> Optional[str]:
    if not text:
        return None
    t = str(text).strip()
    if t.lstrip("-").isdigit():
        return t.lstrip("-")
    m = _VK_RE.search(t)
    if not m:
        return None
    ref = re.sub(r"[^A-Za-z0-9_.\-]", "", m.group(1))
    return ref or None
