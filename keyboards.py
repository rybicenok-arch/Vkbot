from aiogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup

from store import users

COLLECTIONS = {
    "hits":   ("🔥 Популярное", "популярная музыка"),
    "new":    ("🆕 Новинки", "новинки музыки"),
    "rock":   ("🎸 Рок", "рок хиты"),
    "hiphop": ("🎤 Рап / хип-хоп", "хип-хоп"),
    "edm":    ("🎹 Электроника", "electronic dance hits"),
    "pop":    ("✨ Поп-хиты", "pop hits"),
    "rus":    ("🇷🇺 Русские хиты", "русские хиты"),
    "world":  ("🌍 Зарубежные хиты", "зарубежные хиты"),
}


def main_menu(user_id: int) -> InlineKeyboardMarkup:
    rows = []
    if users.get(user_id):
        rows.append([Btn(text="⚡️ Моя музыка", callback_data="m:my")])
    rows.append([Btn(text="🎵 Музыка из профиля ВК", callback_data="m:profile")])
    rows.append([
        Btn(text="🔎 Найти музыку", callback_data="m:search"),
        Btn(text="🔥 Подборки", callback_data="m:coll"),
    ])
    rows.append([
        Btn(text="🎲 Мне повезёт", callback_data="m:lucky"),
        Btn(text="ℹ️ Помощь", callback_data="m:help"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb(callback: str, text: str = "⬅️ В меню") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[Btn(text=text, callback_data=callback)]])


def collections_kb() -> InlineKeyboardMarkup:
    keys = list(COLLECTIONS.keys())
    rows = []
    for i in range(0, len(keys), 2):
        rows.append([Btn(text=COLLECTIONS[k][0], callback_data=f"coll:{k}")
                     for k in keys[i:i + 2]])
    rows.append([Btn(text="⬅️ В меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lucky_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [Btn(text="🎲 Ещё раз", callback_data="m:lucky")],
        [Btn(text="⬅️ В меню", callback_data="menu")],
    ])


def track_card(t) -> InlineKeyboardMarkup:
    """Фолбэк, если mp3-файл недоступен (трек закрыт правообладателем)."""
    rows = []
    if t.vk_link:
        rows.append([Btn(text="🎧 Открыть в VK", url=t.vk_link)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
