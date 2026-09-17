import asyncio
import logging
from dataclasses import dataclass
from typing import Any, List, Optional

import aiohttp

log = logging.getLogger(__name__)


class VKError(Exception):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.msg = msg
        self.code = code


@dataclass
class Track:
    artist: str = ""
    title: str = ""
    duration: int = 0
    url: str = ""          # прямая mp3-ссылка
    artwork: str = ""
    owner_id: int = 0
    audio_id: int = 0
    date: int = 0          # дата добавления в профиль/плейлист (unixtime)

    @property
    def vk_link(self) -> str:
        if self.owner_id and self.audio_id:
            return f"https://vk.com/audio{self.owner_id}_{self.audio_id}"
        return ""


def _artwork(item: dict) -> str:
    album = item.get("album") or {}
    thumb = album.get("thumb") if isinstance(album, dict) else None
    if not isinstance(thumb, dict):
        thumb = album if isinstance(album, dict) else {}
    best, best_size = "", 0
    for k, v in thumb.items():
        if k.startswith("photo_") and isinstance(v, str) and v.startswith("http"):
            try:
                size = int(k.split("_")[1])
            except (ValueError, IndexError):
                continue
            if size > best_size:
                best, best_size = v, size
    return best


def track_from_vk(item: dict) -> Track:
    artist = str(item.get("artist") or "")
    if not artist:
        ma = item.get("main_artists") or []
        artist = ", ".join(str(a.get("name", "")) for a in ma if isinstance(a, dict))
    fa = item.get("featured_artists") or []
    if fa:
        artist += " ft. " + ", ".join(
            str(a.get("name", "")) for a in fa if isinstance(a, dict))
    return Track(
        artist=artist.strip() or "Unknown Artist",
        title=str(item.get("title") or "Без названия"),
        duration=int(item.get("duration") or 0),
        url=str(item.get("url") or ""),
        artwork=_artwork(item),
        owner_id=int(item.get("owner_id") or 0),
        audio_id=int(item.get("id") or 0),
        date=int(item.get("date") or 0),
    )


class VK:
    BASE = "https://api.vk.com/method/"

    def __init__(self, token: str, version: str = "5.199"):
        self.token = token
        self.version = version
        self._session: Optional[aiohttp.ClientSession] = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def call(self, method: str, **params) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        payload["access_token"] = self.token
        payload["v"] = self.version
        s = await self.session()
        last_err: Optional[VKError] = None
        for attempt in range(3):
            try:
                async with s.post(
                    self.BASE + method, data=payload,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    data = await resp.json(content_type=None)
                if "error" in data:
                    err = data["error"]
                    raise VKError(
                        str(err.get("error_msg", "VK API error")),
                        int(err.get("error_code") or 0))
                return data.get("response")
            except VKError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = VKError(f"VK недоступен ({type(e).__name__})")
                await asyncio.sleep(0.5 + attempt)
        raise last_err or VKError("VK недоступен")

    @staticmethod
    def _items(resp: Any) -> list:
        if isinstance(resp, dict):
            return resp.get("items") or []
        if isinstance(resp, list):
            return resp[1:] if resp and isinstance(resp[0], int) else resp
        return []

    async def whoami(self) -> dict:
        """Кто владелец токена (для /vktest)."""
        resp = await self.call("users.get", fields="photo_100")
        if not resp:
            raise VKError("Токен невалиден 😔")
        u = resp[0]
        name = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "VK"
        return {"id": u["id"], "name": name}

    async def resolve_user(self, ref: str) -> dict:
        resp = await self.call("users.get", user_ids=ref, fields="photo_100")
        if not resp:
            raise VKError("Пользователь не найден 😔")
        u = resp[0]
        name = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "VK"
        return {"id": u["id"], "name": name, "photo": u.get("photo_100")}

    async def search(self, q: str, count: int = 50, sort: int = 2) -> List[Track]:
        resp = await self.call("audio.search", q=q, count=count, sort=sort)
        return [track_from_vk(i) for i in self._items(resp) if isinstance(i, dict)]

    async def user_audio(self, owner_id: int, count: int = 300) -> List[Track]:
        resp = await self.call("audio.get", owner_id=owner_id, count=count)
        return [track_from_vk(i) for i in self._items(resp) if isinstance(i, dict)]

    async def playlists(self, owner_id: int, count: int = 30) -> List[dict]:
        resp = await self.call("audio.getPlaylists", owner_id=owner_id, count=count)
        return [p for p in self._items(resp) if isinstance(p, dict)]

    async def playlist_tracks(self, owner_id: int, playlist_id: int,
                              count: int = 300) -> List[Track]:
        try:
            resp = await self.call("audio.get", owner_id=owner_id,
                                   album_id=playlist_id, count=count)
        except VKError:
            resp = await self.call("audio.get", owner_id=owner_id,
                                   playlist_id=playlist_id, count=count)
        return [track_from_vk(i) for i in self._items(resp) if isinstance(i, dict)]
