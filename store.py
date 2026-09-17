import json
import os
from typing import Optional

from config import config


def _path(name: str) -> str:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    return os.path.join(config.DATA_DIR, name)


class Channels:
    """Обязательные для подписки каналы: из env + добавленные /addchannel."""

    def __init__(self, initial: list):
        self.items: list = list(initial or [])
        self._file = _path("channels.json")
        try:
            with open(self._file, encoding="utf-8") as f:
                extra = json.load(f)
            if isinstance(extra, list):
                for c in extra:
                    if c and c not in self.items:
                        self.items.append(c)
        except (OSError, ValueError):
            pass
        self._save()

    def _save(self) -> None:
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def add(self, ch: str) -> None:
        ch = ch.strip()
        if ch and ch not in self.items:
            self.items.append(ch)
            self._save()

    def remove(self, ch: str) -> None:
        ch = ch.strip()
        norm = ch.lstrip("@").lower()
        self.items = [c for c in self.items
                      if str(c).lstrip("@").lstrip("-").lower() != norm]
        self._save()


class Users:
    """Сохранённые VK-профили юзеров (кнопка «Моя музыка»)."""

    def __init__(self):
        self._file = _path("users.json")
        self.data: dict = {}
        try:
            with open(self._file, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            pass

    def get(self, uid: int) -> Optional[str]:
        return self.data.get(str(uid))

    def set(self, uid: int, ref: str) -> None:
        self.data[str(uid)] = str(ref)
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
        except OSError:
            pass

    def count(self) -> int:
        return len(self.data)


channels = Channels(config.CHANNELS)
users = Users()
