import os
from dataclasses import dataclass, field
from typing import List

try:  # локальный запуск из .env
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _parse_list(raw: str, cast=str) -> list:
    out = []
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(cast(item))
        except (TypeError, ValueError):
            continue
    return out


@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    VK_TOKEN: str = os.getenv("VK_TOKEN", "")
    VK_VERSION: str = os.getenv("VK_VERSION", "5.199")
    CHANNELS: List[str] = field(default_factory=lambda: _parse_list(os.getenv("CHANNELS", "")))
    ADMIN_IDS: List[int] = field(default_factory=lambda: _parse_list(os.getenv("ADMIN_IDS", ""), int))
    DATA_DIR: str = os.getenv("DATA_DIR", "data")
    # лимит: 10 треков за 60 секунд на юзера
    DL_MAX: int = int(os.getenv("DL_MAX", "10"))
    DL_WINDOW: int = int(os.getenv("DL_WINDOW", "60"))


config = Config()
