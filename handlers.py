import logging
import math
import random
import time

import aiohttp
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton as Btn,
                           InlineKeyboardMarkup, Message)

from config import config
import limiter
from keyboards import (COLLECTIONS, back_kb, collections_kb, lucky_kb,
                       main_menu, track_card)
from middlewares import is_subscribed, send_subscribe, sub_cache
from store import channels, users
from texts import HELP, INTRO, NEED_VK, SEARCH_ASK, VK_ASK
from utils import esc, fmt_dur, fmt_rel, fmt_window, parse_vk_ref, safe_name
from vk_client import VKError

log = logging.getLogger(__name__)
router = Router()

PAGE = 5
TG_FILE_LIMIT = 45 * 1024 * 1024   # Telegram Bot API ~50 MB, с запасом

LUCKY_QUERIES = [
    "популярные хиты", "лучшие песни", "рок классика", "хип-хоп хиты",
    "электроника хиты", "русские хиты", "зарубежные хиты", "новинки музыки",
]

# сортировка и фильтр по дате — для выгрузок из профиля/плейлиста
SORT_KEYS = {
    "date_desc": lambda t: -(t.date or 0),
    "date_asc": lambda t: (t.date or 0),
    "alpha": lambda t: (t.title or "").lower(),
    "artist": lambda t: ((t.artist or "").lower(), (t.title or "").lower()),
}
SORT_LABELS = [("date_desc", "🆕 Новые"), ("date_asc", "🕰 Старые"),
               ("alpha", "🔤 А-Я"), ("artist", "👤 Артист")]
FILTER_DAYS = {"all": 0, "w": 7, "m": 30, "y": 365}
FILTER_LABELS = [("all", "♾ Всё"), ("w", "7 дней"),
                 ("m", "30 дней"), ("y", "Год")]

cache: dict = {}  # user_id -> контекст


class St(StatesGroup):
    search = State()
    vk_profile = State()


# ───────────────────────── helpers ─────────────────────────

def _get_ctx(user_id: int):
    return cache.get(user_id)


def _cid(c: CallbackQuery) -> int:
    return c.message.chat.id if c.message is not None else c.from_user.id


async def edit_or_answer(msg: Message, text: str, markup=None) -> None:
    try:
        await msg.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await msg.answer(text, reply_markup=markup)


async def edit_cb(c: CallbackQuery, text: str, markup=None) -> None:
    if c.message is not None:
        try:
            await c.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    await c.bot.send_message(_cid(c), text, reply_markup=markup)


async def stale(c: CallbackQuery) -> None:
    await c.answer("⏳ Данные устарели — нажми /start", show_alert=True)


def apply_view(ctx: dict) -> None:
    """Применяет текущие sort+filter к исходному списку."""
    items = ctx.get("all") or []
    days = FILTER_DAYS.get(ctx["filter"], 0)
    if days:
        cutoff = time.time() - days * 86400
        items = [t for t in items if (t.date or 0) >= cutoff]
    items = sorted(items, key=SORT_KEYS[ctx["sort"]])
    ctx["items"] = items
    ctx["page"] = 0


def render_results(ctx: dict):
    items = ctx["items"]
    total = len(items)

    if total == 0:
        text = f"{ctx['header']}\n\n😕 <b>Ничего не найдено</b>"
        if ctx.get("filterable") and ctx["filter"] != "all":
            text += "\n\n<i>За выбранный период треков нет — смени фильтр 📅</i>"
        return text, back_kb("menu")

    pages = max(1, math.ceil(total / PAGE))
    ctx["page"] = max(0, min(ctx["page"], pages - 1))
    start = ctx["page"] * PAGE
    chunk = items[start:start + PAGE]

    rows = []
    for gi in range(start, start + len(chunk)):
        t = items[gi]
        label = f"▶️ {t.artist} — {t.title}"
        if ctx.get("filterable") and t.date:
            label += f" · {fmt_rel(t.date)}"
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append([Btn(text=label, callback_data=f"t:{gi}")])

    if pages > 1:
        nav = []
        if ctx["page"] > 0:
            nav.append(Btn(text="⬅️", callback_data="pg:prev"))
        nav.append(Btn(text=f"📄 {ctx['page'] + 1}/{pages}", callback_data="noop"))
        if ctx["page"] < pages - 1:
            nav.append(Btn(text="➡️", callback_data="pg:next"))
        rows.append(nav)

    # сортировка + фильтр по дате (профиль/плейлист)
    if ctx.get("filterable"):
        rows.append([Btn(
            text=("● " if ctx["sort"] == k else "") + lbl,
            callback_data=f"s:{k}") for k, lbl in SORT_LABELS])
        rows.append([Btn(
            text=("● " if ctx["filter"] == k else "") + lbl,
            callback_data=f"f:{k}") for k, lbl in FILTER_LABELS])

    for row in ctx.get("extra", []):
        rows.append(row)
    rows.append([Btn(text="⬅️ В меню", callback_data="menu")])

    text = (
        f"{ctx['header']}\n\n"
        f"👇 <b>Тапни по треку</b> — пришлю mp3 в чат\n"
        f"🎼 Треков: <b>{total}</b> · стр. <b>{ctx['page'] + 1}</b>/<b>{pages}</b>"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# ──────────── доставка трека файлом ────────────

async def _download(http: aiohttp.ClientSession, url: str):
    try:
        async with http.get(url, timeout=aiohttp.ClientTimeout(total=60)) as r:
            if r.status != 200:
                return None
            raw = await r.read()
            if 100_000 < len(raw) < TG_FILE_LIMIT:
                return raw
    except Exception as e:
        log.warning("download failed: %r", e)
    return None


async def _thumb(http: aiohttp.ClientSession, url: str):
    if not url:
        return None
    try:
        async with http.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                raw = await r.read()
                if raw:
                    return BufferedInputFile(raw, filename="cover.jpg")
    except Exception:
        pass
    return None


async def deliver_track(bot: Bot, chat_id: int, user_id: int, track,
                        http: aiohttp.ClientSession) -> None:
    """Скачивает mp3 из VK и отправляет файлом в чат."""
    ok, wait = limiter.check(user_id)
    if not ok:
        await bot.send_message(
            chat_id,
            f"⏳ <b>Лимит:</b> {config.DL_MAX} треков "
            f"{fmt_window(config.DL_WINDOW)}.\n"
            f"Подожди ~{wait} сек. и попробуй снова 🙂")
        return

    if not track.url:
        await bot.send_message(
            chat_id,
            f"🔒 <b>{esc(track.artist)}</b> — <b>{esc(track.title)}</b>\n"
            f"⏱ {fmt_dur(track.duration)}\n\n"
            "Трек закрыт правообладателем — файлом не достать 😢\n"
            "Открыть в VK 👇",
            reply_markup=track_card(track))
        return

    status = await bot.send_message(
        chat_id, f"⏬ Скачиваю: <b>{esc(track.title)}</b>…")

    raw = await _download(http, track.url)
    if not raw:
        try:
            await status.delete()
        except TelegramBadRequest:
            pass
        await bot.send_message(
            chat_id,
            f"😢 <b>Файл достать не удалось</b>\n\n"
            f"🎧 <b>{esc(track.artist)}</b> — <b>{esc(track.title)}</b>\n"
            f"⏱ {fmt_dur(track.duration)}\n\n"
            "Открыть в VK 👇",
            reply_markup=track_card(track))
        return

    limiter.record(user_id)

    caption = (f"🎵 <b>{esc(track.artist)}</b> — <b>{esc(track.title)}</b>\n"
               f"⏱ {fmt_dur(track.duration)} · 🎧 VK")
    if track.date:
        caption += f"\n📅 Добавлено: {fmt_rel(track.date)}"

    audio = BufferedInputFile(
        raw, filename=f"{safe_name(f'{track.artist} - {track.title}')}.mp3")
    thumb = await _thumb(http, track.artwork)

    try:
        await bot.send_audio(
            chat_id, audio, caption=caption,
            title=track.title[:64], performer=track.artist[:64],
            duration=track.duration or None, thumbnail=thumb)
    except TelegramBadRequest:
        if thumb is not None:  # повтор без обложки
            await bot.send_audio(
                chat_id, audio, caption=caption,
                title=track.title[:64], performer=track.artist[:64],
                duration=track.duration or None)

    try:
        await status.delete()
    except TelegramBadRequest:
        pass


async def present_tracks(msg: Message, user_id: int, info: dict, tracks: list) -> None:
    name = esc(info["name"])
    if not tracks:
        await edit_or_answer(
            msg,
            f"🫥 У <b>{name}</b> нет музыки — или аудио скрыты приватностью.",
            back_kb("menu"))
        return
    users.set(user_id, str(info["id"]))
    ctx = {
        "all": tracks, "items": [], "page": 0,
        "header": f"🎧 Музыка <b>{name}</b> · {len(tracks)} треков",
        "extra": [[Btn(text="📄 Плейлисты профиля", callback_data="pl:list")]],
        "filterable": True, "sort": "date_desc", "filter": "all",
        "owner_id": info["id"], "owner_name": name,
    }
    apply_view(ctx)
    cache[user_id] = ctx
    text, markup = render_results(ctx)
    await edit_or_answer(msg, text, markup)


# ───────────────────── команды и меню ─────────────────────

@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    if channels.items and not await is_subscribed(bot, m.from_user.id):
        await send_subscribe(bot, m.chat.id, m.from_user.id)
        return
    await m.answer(INTRO, reply_markup=main_menu(m.from_user.id))


@router.message(Command("menu"))
async def cmd_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(INTRO, reply_markup=main_menu(m.from_user.id))


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


@router.message(Command("vktest"))
async def cmd_vktest(m: Message, vk):
    """Диагностика токена для админа: живой ли, и пускает ли аудио."""
    if not _is_admin(m.from_user.id):
        return await m.answer("⛔️ Только для админа")
    if vk is None:
        return await m.answer("❌ VK_TOKEN не задан в переменных окружения")

    try:
        me = await vk.whoami()
    except VKError as e:
        return await m.answer(
            f"❌ <b>Токен мёртвый</b>: {esc(str(e))}\n\n"
            "Получи новый на vkhost.github.io и обнови VK_TOKEN.")

    try:
        tracks = await vk.user_audio(me["id"])
        n = len(tracks)
        with_files = sum(1 for t in tracks if t.url)
        if n == 0:
            return await m.answer(
                f"✅ Токен живой: <b>{esc(me['name'])}</b>\n\n"
                "⚠️ Но аудио вернуло 0 треков — либо у тебя пусто в музыке, "
                "либо аудио-методы закрыты для этого приложения.\n"
                "Попробуй получить токен через другое приложение на vkhost.")
        return await m.answer(
            f"✅ <b>Всё работает!</b>\n"
            f"👤 Токен от: <b>{esc(me['name'])}</b>\n"
            f"🎵 Твоих треков: <b>{n}</b>\n"
            f"⬇️ С прямой mp3-ссылкой: <b>{with_files}</b>")
    except VKError as e:
        return await m.answer(
            f"⚠️ Токен живой: <b>{esc(me['name'])}</b>\n\n"
            f"❌ Аудио закрыто: {esc(str(e))}\n\n"
            "Это значит, что приложение, через которое выдан токен, "
            "не имеет доступа к аудио. Попробуй другое приложение "
            "на vkhost.github.io.")


@router.message(Command("addchannel"))
async def cmd_addchannel(m: Message):
    if not _is_admin(m.from_user.id):
        return await m.answer("⛔️ Только для админа")
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return await m.answer(
            "Формат: <code>/addchannel @username</code> или "
            "<code>/addchannel -100xxxxxxxxxx</code>")
    ch = parts[1].strip()
    channels.add(ch)
    sub_cache.clear()
    await m.answer(f"✅ Канал <b>{esc(ch)}</b> добавлен в обязательные.\n"
                   "Не забудь сделать бота админом этого канала!")


@router.message(Command("removechannel"))
async def cmd_removechannel(m: Message):
    if not _is_admin(m.from_user.id):
        return await m.answer("⛔️ Только для админа")
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await m.answer("Формат: <code>/removechannel @username</code>")
    channels.remove(parts[1].strip())
    sub_cache.clear()
    await m.answer("🗑 Канал удалён (если был).")


@router.message(Command("channels"))
async def cmd_channels(m: Message):
    if not _is_admin(m.from_user.id):
        return await m.answer("⛔️ Только для админа")
    if not channels.items:
        return await m.answer("Обязательных каналов нет — подписка не требуется.")
    await m.answer("📢 Обязательные каналы:\n" + "\n".join(
        f"• <code>{esc(c)}</code>" for c in channels.items))


@router.message(Command("stats"))
async def cmd_stats(m: Message):
    if not _is_admin(m.from_user.id):
        return await m.answer("⛔️ Только для админа")
    await m.answer("📊 Статистика:\n"
                   f"👥 Пользователей: <b>{users.count()}</b>\n"
                   f"📢 Каналов: <b>{len(channels.items)}</b>")


@router.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await edit_cb(c, INTRO, main_menu(c.from_user.id))


@router.callback_query(F.data == "sub:check")
async def cb_sub_check(c: CallbackQuery, bot: Bot):
    sub_cache.pop(c.from_user.id, None)
    if await is_subscribed(bot, c.from_user.id):
        await c.answer("🎉 Спасибо за подписку!")
        await edit_cb(c, INTRO, main_menu(c.from_user.id))
    else:
        await c.answer("❌ Подписки не видно. Подпишись на все каналы и попробуй снова!",
                       show_alert=True)


@router.callback_query(F.data == "m:help")
async def cb_help(c: CallbackQuery):
    await c.answer()
    await edit_cb(c, HELP, back_kb("menu"))


# ───────────────────────── поиск ─────────────────────────

@router.callback_query(F.data == "m:search")
async def cb_search(c: CallbackQuery, state: FSMContext, vk):
    if vk is None:
        return await c.answer("VK не подключён (VK_TOKEN)", show_alert=True)
    await c.answer()
    await state.set_state(St.search)
    await edit_cb(c, SEARCH_ASK, back_kb("menu"))


@router.message(St.search)
async def h_search(m: Message, state: FSMContext, vk):
    q = (m.text or "").strip()
    if q.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        return await m.answer(INTRO, reply_markup=main_menu(m.from_user.id))
    if q.startswith("/"):
        return await m.answer("Сначала выйди из поиска: /start")
    if len(q) < 2:
        return await m.answer("🤏 Слишком коротко. Напиши название или исполнителя:")
    if vk is None:
        await state.clear()
        return await m.answer(NEED_VK)
    await state.clear()
    status = await m.answer(f"🔎 Ищу «{esc(q)}»…")
    try:
        tracks = await vk.search(q, count=50)
    except VKError as e:
        return await edit_or_answer(status, f"❌ {esc(str(e))}", back_kb("menu"))
    ctx = {
        "all": [], "items": tracks, "page": 0,
        "header": f"🔎 Результаты: «{esc(q)}»",
        "extra": [], "filterable": False,
        "sort": "date_desc", "filter": "all",
    }
    cache[m.from_user.id] = ctx
    text, markup = render_results(ctx)
    await edit_or_answer(status, text, markup)


# ─────────────── музыка из профиля VK ───────────────

@router.callback_query(F.data == "m:profile")
async def cb_profile(c: CallbackQuery, state: FSMContext, vk):
    if vk is None:
        return await c.answer("VK не подключён (VK_TOKEN)", show_alert=True)
    await c.answer()
    await state.set_state(St.vk_profile)
    await edit_cb(c, VK_ASK, back_kb("menu"))


@router.message(St.vk_profile)
async def h_profile(m: Message, state: FSMContext, vk):
    q = (m.text or "").strip()
    if q.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        return await m.answer(INTRO, reply_markup=main_menu(m.from_user.id))
    if q.startswith("/"):
        return await m.answer("Сначала выйди из режима: /start")
    if vk is None:
        await state.clear()
        return await m.answer(NEED_VK)
    ref = parse_vk_ref(q)
    if not ref:
        return await m.answer(
            "🤔 Не понял ссылку. Пришли, например:\n"
            "<code>https://vk.com/durov</code> или <code>1</code>")
    await state.clear()
    status = await m.answer("⏳ Загружаю профиль…")
    try:
        info = await vk.resolve_user(ref)
        tracks = await vk.user_audio(info["id"])
    except VKError as e:
        return await edit_or_answer(status, f"❌ {esc(str(e))}", back_kb("menu"))
    await present_tracks(status, m.from_user.id, info, tracks)


@router.callback_query(F.data == "m:my")
async def cb_my(c: CallbackQuery, vk):
    ref = users.get(c.from_user.id)
    if not ref:
        return await c.answer(
            "Сначала введи профиль через «🎵 Музыка из профиля ВК» 😉",
            show_alert=True)
    if vk is None:
        return await c.answer("VK не подключён (VK_TOKEN)", show_alert=True)
    await c.answer("⏳ Загружаю…")
    status = await c.message.answer("⏳ Загружаю твою музыку…")
    try:
        info = await vk.resolve_user(ref)
        tracks = await vk.user_audio(info["id"])
    except VKError as e:
        return await edit_or_answer(status, f"❌ {esc(str(e))}", back_kb("menu"))
    await present_tracks(status, c.from_user.id, info, tracks)


# ─────────────────── плейлисты профиля ───────────────────

@router.callback_query(F.data == "pl:list")
async def cb_playlists(c: CallbackQuery, vk):
    ctx = _get_ctx(c.from_user.id)
    if not ctx or "owner_id" not in ctx or vk is None:
        return await stale(c)
    try:
        pls = await vk.playlists(ctx["owner_id"])
    except VKError as e:
        return await c.answer(f"❌ {str(e)[:180]}", show_alert=True)
    if not pls:
        return await c.answer("У профиля нет плейлистов 😔", show_alert=True)
    ctx["playlists"] = pls
    rows = []
    for i, p in enumerate(pls):
        label = f"🎼 {p.get('title') or 'Плейлист'} · {p.get('count', '?')}"
        if len(label) > 58:
            label = label[:55] + "…"
        rows.append([Btn(text=label, callback_data=f"pl:{i}")])
    rows.append([Btn(text="⬅️ К трекам", callback_data="pl:back")])
    await c.answer()
    await edit_cb(c, f"📄 Плейлисты <b>{ctx['owner_name']}</b>\n\n👇 Выбери плейлист",
                  InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "pl:back")
async def cb_pl_back(c: CallbackQuery):
    ctx = _get_ctx(c.from_user.id)
    if not ctx:
        return await stale(c)
    await c.answer()
    text, markup = render_results(ctx)
    await edit_cb(c, text, markup)


@router.callback_query(F.data.regexp(r"^pl:\d+$"))
async def cb_playlist_open(c: CallbackQuery, vk):
    ctx = _get_ctx(c.from_user.id)
    pls = (ctx or {}).get("playlists") or []
    if not ctx or not pls or vk is None:
        return await stale(c)
    try:
        pl = pls[int(c.data.split(":")[1])]
    except (ValueError, IndexError):
        return await stale(c)
    await c.answer("⏳ Загружаю плейлист…")
    try:
        tracks = await vk.playlist_tracks(ctx["owner_id"], int(pl.get("id") or 0))
    except VKError as e:
        return await c.answer(f"❌ {str(e)[:180]}", show_alert=True)
    ctx["all"] = tracks
    ctx["header"] = (f"🎼 Плейлист: <b>{esc(pl.get('title') or '')}</b> · "
                     f"{len(tracks)} треков")
    ctx["extra"] = [[Btn(text="⬅️ К плейлистам", callback_data="pl:list")]]
    ctx["filterable"] = True
    apply_view(ctx)
    text, markup = render_results(ctx)
    await edit_cb(c, text, markup)


# ─────────── сортировка и фильтры по дате ───────────

@router.callback_query(F.data.regexp(r"^s:"))
async def cb_sort(c: CallbackQuery):
    ctx = _get_ctx(c.from_user.id)
    if not ctx or not ctx.get("filterable"):
        return await c.answer()
    key = c.data.split(":", 1)[1]
    if key not in SORT_KEYS:
        return await c.answer()
    ctx["sort"] = key
    apply_view(ctx)
    await c.answer()
    text, markup = render_results(ctx)
    await edit_cb(c, text, markup)


@router.callback_query(F.data.regexp(r"^f:"))
async def cb_filter(c: CallbackQuery):
    ctx = _get_ctx(c.from_user.id)
    if not ctx or not ctx.get("filterable"):
        return await c.answer()
    key = c.data.split(":", 1)[1]
    if key not in FILTER_DAYS:
        return await c.answer()
    ctx["filter"] = key
    apply_view(ctx)
    await c.answer()
    text, markup = render_results(ctx)
    await edit_cb(c, text, markup)


# ─────────────────────── подборки ───────────────────────

@router.callback_query(F.data == "m:coll")
async def cb_collections(c: CallbackQuery, vk):
    if vk is None:
        return await c.answer("VK не подключён (VK_TOKEN)", show_alert=True)
    await c.answer()
    await edit_cb(c, "🔥 <b>Подборки</b>\n\nВыбирай жанр — соберу треки 👇",
                  collections_kb())


@router.callback_query(F.data.regexp(r"^coll:"))
async def cb_collection_open(c: CallbackQuery, vk):
    if vk is None:
        return await c.answer("VK не подключён (VK_TOKEN)", show_alert=True)
    key = c.data.split(":", 1)[1]
    name, query = COLLECTIONS.get(key, ("🎵 Подборка", "музыка"))
    await c.answer(f"Собираю: {name}…")
    try:
        tracks = await vk.search(query, count=50)
    except VKError as e:
        return await c.answer(f"❌ {str(e)[:180]}", show_alert=True)
    ctx = {
        "all": [], "items": tracks, "page": 0, "header": name,
        "extra": [], "filterable": False,
        "sort": "date_desc", "filter": "all",
    }
    cache[c.from_user.id] = ctx
    text, markup = render_results(ctx)
    await edit_cb(c, text, markup)


# ─────────────────── мне повезёт ───────────────────

@router.callback_query(F.data == "m:lucky")
async def cb_lucky(c: CallbackQuery, vk, http):
    if vk is None:
        return await c.answer("VK не подключён (VK_TOKEN)", show_alert=True)
    await c.answer("🎲 Кручу барабан…")
    q = random.choice(LUCKY_QUERIES)
    track = None
    try:
        tracks = await vk.search(q, count=100)
        if tracks:
            track = random.choice(tracks[:60])
    except VKError:
        pass
    if track is None:
        return await c.message.answer("🎲 Сегодня не повезло — попробуй ещё!")
    text = ("🎲 <b>Мне повезёт</b>\n\n"
            f"Выпало: <b>{esc(track.artist)} — {esc(track.title)}</b>\n"
            f"⏱ {fmt_dur(track.duration)}\n\n⏳ Скачиваю…")
    await edit_cb(c, text, lucky_kb())
    await deliver_track(c.bot, _cid(c), c.from_user.id, track, http)


# ───────────── пагинация / треки ─────────────

@router.callback_query(F.data == "noop")
async def cb_noop(c: CallbackQuery):
    await c.answer()


@router.callback_query(F.data.in_({"pg:next", "pg:prev"}))
async def cb_page(c: CallbackQuery):
    ctx = _get_ctx(c.from_user.id)
    if not ctx:
        return await stale(c)
    ctx["page"] += 1 if c.data == "pg:next" else -1
    text, markup = render_results(ctx)
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            raise
    await c.answer()


@router.callback_query(F.data.regexp(r"^t:\d+$"))
async def cb_track(c: CallbackQuery, http):
    ctx = _get_ctx(c.from_user.id)
    if not ctx:
        return await stale(c)
    try:
        track = ctx["items"][int(c.data.split(":")[1])]
    except (ValueError, IndexError):
        return await stale(c)

    # мгновенная проверка лимита, чтобы callback не висел
    ok, wait = limiter.check(c.from_user.id)
    if not ok:
        return await c.answer(
            f"⏳ Лимит: {config.DL_MAX} треков {fmt_window(config.DL_WINDOW)}. "
            f"Подожди ~{wait} сек.", show_alert=True)

    await c.answer("⏳ Скачиваю файл…")
    await deliver_track(c.bot, _cid(c), c.from_user.id, track, http)


# ─────────────────── фолбэки (в конце!) ───────────────────

@router.callback_query()
async def cb_unknown(c: CallbackQuery):
    await c.answer()


@router.message()
async def fallback(m: Message):
    await m.answer("🤔 Не понял команду. Открой меню: /start")
