"""Антифлуд: не больше N треков за окно в M секунд на одного юзера."""
import time
from typing import List, Tuple

from config import config


_history: dict = {}


def check(user_id: int) -> Tuple[bool, int]:
    """(можно ли, сек до освобождения слота)"""
    now = time.time()
    hist: List[float] = [t for t in _history.get(user_id, [])
                         if now - t < config.DL_WINDOW]
    _history[user_id] = hist
    if len(hist) >= config.DL_MAX:
        wait = int(config.DL_WINDOW - (now - hist[0])) + 1
        return False, max(wait, 1)
    return True, 0


def record(user_id: int) -> None:
    _history.setdefault(user_id, []).append(time.time())
