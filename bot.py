import asyncio
import json
import logging
import os
import io
import textwrap
from datetime import date, timedelta
from pathlib import Path

import anthropic
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
import numpy as np

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─── Конфигурация ─────────────────────────────────────────────────────────────

BOT_TOKEN     = os.getenv("BOT_TOKEN", "YOUR_TOKEN_HERE")
ADMIN_IDS     = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]
CHANNEL_ID    = int(os.getenv("CHANNEL_ID", "-1003755821511"))
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

SUBSCRIBERS_FILE  = Path("subscribers.json")
HISTORY_FILE      = Path("history.json")
PENDING_FILE      = Path("pending_approvals.json")  # очередь на согласование

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Цвета бренда
C_BG     = "#0d1117"
C_CARD   = "#161b22"
C_ACCENT = "#c9a96e"
C_GREEN  = "#2ecc71"
C_RED    = "#e74c3c"
C_YELLOW = "#f39c12"
C_WHITE  = "#e6edf3"
C_GRAY   = "#8b949e"

# ─── JSON helpers ─────────────────────────────────────────────────────────────

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default

def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def load_subscribers(): return set(load_json(SUBSCRIBERS_FILE, []))
def save_subscribers(s): save_json(SUBSCRIBERS_FILE, list(s))
def load_history(): return load_json(HISTORY_FILE, [])

def save_to_history(name, days, text):
    h = load_history()
    h.append({"date": date.today().isoformat(), "event_name": name, "days_left": days, "text": text})
    save_json(HISTORY_FILE, h)

# ─── Сезонные данные ──────────────────────────────────────────────────────────

SEASONAL_FLOWERS = {
    1:  {
        "peak":      ["Хризантема", "Гербера", "Альстромерия", "Антуриум"],
        "available": ["Роза", "Тюльпан (теплица)", "Гипсофила", "Лизиантус"],
        "avoid":     ["Сирень", "Пион", "Полевые цветы"],
        "demand":    55,
        "trend":     -1,
        "tip":       "Посленовогодний спад. Фокус на горшечные растения и корпоративное оформление офисов.",
    },
    2:  {
        "peak":      ["Роза красная", "Тюльпан", "Мимоза", "Ранункуль"],
        "available": ["Гербера", "Альстромерия", "Эустома"],
        "avoid":     ["Пион", "Гладиолус"],
        "demand":    130,
        "trend":     1,
        "tip":       "Двойной пик: 14 февраля + 23 февраля. Закупайте красную розу и тюльпан с запасом +70%.",
    },
    3:  {
        "peak":      ["Тюльпан", "Мимоза", "Роза", "Нарцисс", "Гиацинт"],
        "available": ["Хризантема", "Фрезия", "Гипсофила"],
        "avoid":     ["Летние полевые"],
        "demand":    200,
        "trend":     1,
        "tip":       "Главный месяц года. 8 марта — увеличьте закупку тюльпана на 80%. Готовьте упаковку заранее.",
    },
    4:  {
        "peak":      ["Тюльпан", "Нарцисс", "Пион (первые)", "Фрезия"],
        "available": ["Роза", "Лилия", "Альстромерия"],
        "avoid":     ["Хризантема (снижение спроса)"],
        "demand":    75,
        "trend":     -1,
        "tip":       "Послемартовский спад. Хорошее время для акций и работы с базой клиентов.",
    },
    5:  {
        "peak":      ["Пион", "Ирис", "Лилия", "Гладиолус (первые)"],
        "available": ["Роза", "Альстромерия", "Астра"],
        "avoid":     ["Тюльпан (конец сезона)"],
        "demand":    110,
        "trend":     1,
        "tip":       "День Победы + последний звонок. Пион — король мая. Закупайте активно с начала месяца.",
    },
    6:  {
        "peak":      ["Роза (летняя)", "Гладиолус", "Лилия", "Гербера"],
        "available": ["Альстромерия", "Хризантема", "Эустома"],
        "avoid":     ["Тюльпан", "Нарцисс"],
        "demand":    60,
        "trend":     -1,
        "tip":       "Летний спад. Развивайте доставку и работу с корпоративными клиентами.",
    },
    7:  {
        "peak":      ["Роза", "Гладиолус", "Лилия", "Подсолнух"],
        "available": ["Гербера", "Альстромерия", "Хризантема"],
        "avoid":     ["Пион (конец)", "Тюльпан"],
        "demand":    45,
        "trend":     -1,
        "tip":       "Самый тихий месяц. Минимизируйте закупки, работайте по предзаказу.",
    },
    8:  {
        "peak":      ["Подсолнух", "Роза", "Гладиолус", "Георгин"],
        "available": ["Лилия", "Гербера", "Альстромерия"],
        "avoid":     [],
        "demand":    55,
        "trend":     1,
        "tip":       "Оживление. День строителя, подготовка к 1 сентября. Запасайте астру и гладиолус.",
    },
    9:  {
        "peak":      ["Астра", "Гладиолус", "Хризантема", "Георгин"],
        "available": ["Роза", "Гербера", "Подсолнух"],
        "avoid":     ["Тюльпан", "Пион"],
        "demand":    120,
        "trend":     1,
        "tip":       "1 сентября + День учителя. Астра и гладиолус — в дефиците. Закупайте за 2 недели.",
    },
    10: {
        "peak":      ["Хризантема", "Гербера", "Роза", "Эустома"],
        "available": ["Альстромерия", "Лилия", "Гипсофила"],
        "avoid":     ["Гладиолус (конец)", "Георгин"],
        "demand":    70,
        "trend":     -1,
        "tip":       "Осенний спад. Хризантема — основной объём. Работайте над базой постоянных клиентов.",
    },
    11: {
        "peak":      ["Хризантема", "Роза", "Эустома", "Гербера"],
        "available": ["Альстромерия", "Лилия", "Гипсофила"],
        "avoid":     ["Летние цветы"],
        "demand":    95,
        "trend":     1,
        "tip":       "День матери + корпоративный сезон. Нежные оттенки хризантемы и роз — в приоритете.",
    },
    12: {
        "peak":      ["Роза", "Пуансеттия", "Хризантема", "Гербера"],
        "available": ["Эустома", "Альстромерия", "Гипсофила"],
        "avoid":     [],
        "demand":    90,
        "trend":     1,
        "tip":       "Новогодний сезон. Красные и белые розы + пуансеттия. Корпоративные заказы — готовьте заранее.",
    },
}

MONTH_NAMES = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
               "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

SEASON_DEMAND = [s["demand"] for s in SEASONAL_FLOWERS.values()]

# ─── Праздники ────────────────────────────────────────────────────────────────

def easter_date(year):
    a = year % 4; b = year % 7; c = year % 19
    d = (19 * c + 15) % 30; e = (2 * a + 4 * b - d + 34) % 7
    m = (d + e + 114) // 31; day = ((d + e + 114) % 31) + 1
    return date(year, m, day)

def nth_weekday(year, month, weekday, n):
    first = date(year, month, 1)
    da = weekday - first.weekday()
    if da < 0: da += 7
    fo = first + timedelta(days=da)
    if n == -1:
        last = fo
        while (last + timedelta(7)).month == month:
            last += timedelta(7)
        return last
    return fo + timedelta(weeks=n - 1)

def get_events(year):
    e = easter_date(year)
    return [
        {"name": "Татьянин день",                "date": date(year, 1, 25)},
        {"name": "День святого Валентина",        "date": date(year, 2, 14)},
        {"name": "День защитника Отечества",      "date": date(year, 2, 23)},
        {"name": "8 марта",                       "date": date(year, 3, 8)},
        {"name": "День Победы",                   "date": date(year, 5, 9)},
        {"name": "День семьи (15 мая)",           "date": date(year, 5, 15)},
        {"name": "Последний звонок",              "date": date(year, 5, 25)},
        {"name": "День защиты детей",             "date": date(year, 6, 1)},
        {"name": "День России",                   "date": date(year, 6, 12)},
        {"name": "День семьи, любви и верности",  "date": date(year, 7, 8)},
        {"name": "1 сентября",                    "date": date(year, 9, 1)},
        {"name": "День учителя",                  "date": date(year, 10, 5)},
        {"name": "День народного единства",       "date": date(year, 11, 4)},
        {"name": "День бухгалтера",               "date": date(year, 11, 21)},
        {"name": "Новогодний сезон",              "date": date(year, 12, 20)},
        {"name": "День медика",                   "date": nth_weekday(year, 6, 6, 3)},
        {"name": "День строителя",                "date": nth_weekday(year, 8, 6, 2)},
        {"name": "День матери",                   "date": nth_weekday(year, 11, 6, -1)},
        {"name": "Мясопустная суббота",           "date": e - timedelta(57)},
        {"name": "Родительская суббота I",        "date": e - timedelta(36)},
        {"name": "Радоница",                      "date": e + timedelta(9)},
        {"name": "Троицкая суббота",              "date": e + timedelta(49)},
        {"name": "Димитриевская суббота",         "date": nth_weekday(year, 11, 5, 1)},
    ]

NOTIFY_DAYS = [21, 14, 7, 3]
EMOJI_MAP   = {21: "📅", 14: "⏰", 7: "🔔", 3: "🚨"}
URGENCY_MAP = {
    21: "Начинайте планировать закупку",
    14: "Скорректируйте объёмы, проверьте остатки",
    7:  "Готовьтесь к пику — оформляйте заявки",
    3:  "СРОЧНО — последний шанс пополнить запасы",
}

ASSORTMENT_TIPS = {
    "8 марта":               "💐 Основной спрос: тюльпан, мимоза, роза. Увеличьте закупку на 60–80%.",
    "день святого валентина":"💐 Основной спрос: красная роза, коробочные композиции.",
    "1 сентября":            "💐 Основной спрос: астра, гладиолус, подсолнух. Пик 2–3 дня.",
    "день матери":           "💐 Основной спрос: хризантема, эустома, роза пастельных оттенков.",
    "DEFAULT":               "💐 Уточните актуальный ассортимент у вашего менеджера по закупкам.",
}

def get_assortment_tip(name):
    for k in ASSORTMENT_TIPS:
        if k in name.lower():
            return ASSORTMENT_TIPS[k]
    return ASSORTMENT_TIPS["DEFAULT"]

def build_notification(name, ev_date, days):
    today_str = date.today().strftime("%d.%m.%Y")
    return (
        f"{EMOJI_MAP.get(days, '🌷')} *До «{name}» осталось {days} дней*\n"
        f"📆 Дата: {ev_date.strftime('%d.%m.%Y')} | Сегодня: {today_str}\n\n"
        f"💡 {URGENCY_MAP.get(days, '')}\n\n"
        f"{get_assortment_tip(name)}\n\n"
        f"─────────────────\n"
        f"🌷 Календарь цветочного бизнеса"
    )

# ─── Контент: утренние и вечерние посты ──────────────────────────────────────

MORNING_TIPS = [
    {
        "text": "💼 *Как увеличить выручку без новых клиентов*\n\n"
                "Самый быстрый рост — из существующей базы.\n\n"
                "• Позвоните 5 клиентам, которых давно не было\n"
                "• Предложите «цветочную подписку» — букет раз в неделю\n"
                "• Добавляйте маленький комплимент к каждому заказу\n"
                "• Собирайте отзывы на Яндекс.Картах\n\n"
                "Лояльный клиент стоит в 5 раз дешевле нового.\n\n"
                "─────────────────\n🌷 Календарь цветочного бизнеса"
    },
    {
        "text": "🎯 *Как привлечь корпоративных клиентов*\n\n"
                "Корпоративы — стабильный ежемесячный доход.\n\n"
                "• Обойдите 5–10 офисов рядом с магазином\n"
                "• Предложите оформление переговорных и ресепшн\n"
                "• Заключайте договоры на 8 марта и НГ заранее\n"
                "• Один корпоратив = 10–20 букетов в месяц\n\n"
                "─────────────────\n🌷 Календарь цветочного бизнеса"
    },
    {
        "text": "📊 *3 ошибки, которые съедают прибыль*\n\n"
                "• ❌ Закупать «на глаз» без учёта статистики\n"
                "• ❌ Не считать списания — они бьют по марже\n"
                "• ❌ Не знать себестоимость каждого букета\n\n"
                "Начните с простого: таблица в Google Sheets.\n"
                "Через месяц увидите где теряете деньги.\n\n"
                "─────────────────\n🌷 Календарь цветочного бизнеса"
    },
    {
        "text": "💡 *Допродажи — простой способ поднять чек*\n\n"
                "• Предлагайте открытку к каждому букету\n"
                "• Показывайте сначала средний вариант, потом больший\n"
                "• Красивая упаковка = +30–50% к чеку\n"
                "• «За 1500 — вот так, за 2200 — вот так»\n\n"
                "Большинство покупателей выбирают средний или больший.\n\n"
                "─────────────────\n🌷 Календарь цветочного бизнеса"
    },
    {
        "text": "🛒 *Витрина, которая продаёт сама*\n\n"
                "• Самые красивые букеты — на уровне глаз\n"
                "• Обновляйте витрину минимум раз в 2 дня\n"
                "• Разные ценовые категории — дайте выбор\n"
                "• Аромат свежих цветов в зоне входа\n"
                "• Чистые вёдра и вазы = доверие к качеству\n\n"
                "─────────────────\n🌷 Календарь цветочного бизнеса"
    },
]

EVENING_TIPS = [
    {
        "text": "🌹 *Как правильно хранить розы*\n\n"
                "❄️ Холодильник: +2–5°C — оптимальная температура хранения\n"
                "✂️ Срез под углом 45° острым ножом, обновлять каждые 2 дня\n"
                "💧 Вода комнатной температуры, менять ежедневно\n"
                "🍃 Убирайте все листья ниже уровня воды\n"
                "🚫 Не ставьте рядом с фруктами — этилен ускоряет увядание\n"
                "⏰ При правильном хранении — 7–14 дней\n\n"
                "─────────────────\n🌷 Календарь цветочного бизнеса"
    },
    {
        "text": "🌷 *Тюльпаны: хранение и продажа*\n\n"
                "📦 В коробках при +2–4°C — до 2 недель\n"
                "↔️ Держите горизонтально — они тянутся к свету\n"
                "💧 В воде — не глубже 3 см, стебель гниёт\n"
                "🛒 Берите закрытые бутоны — клиент радуется дольше\n\n"
                "─────────────────\n🌷 Календарь цветочного бизнеса"
    },
    {
        "text": "🌸 *Сервис, который возвращает клиентов*\n\n"
                "👋 Здоровайтесь с каждым в первые 10 секунд\n"
                "❓ Спрашивайте: «Это подарок или для дома?»\n"
                "📱 Берите номер телефона при заказе от 500 руб\n"
                "⭐️ Просите отзыв на Яндекс.Картах\n\n"
                "Клиент покупает эмоцию — дайте её.\n\n"
                "─────────────────\n🌷 Календарь цветочного бизнеса"
    },
    {
        "text": "🪴 *Горшечные растения в периоды спада*\n\n"
                "Летом и в январе срезанные цветы продаются хуже.\n"
                "Горшечные — стабильны круглый год:\n\n"
                "• Монстера, фикус, потос — офисный сегмент\n"
                "• Орхидея — популярный подарок\n"
                "• Суккуленты — молодая аудитория\n\n"
                "Добавьте в ассортимент — выровняете выручку.\n\n"
                "─────────────────\n🌷 Календарь цветочного бизнеса"
    },
    {
        "text": "📱 *Соцсети для цветочного магазина*\n\n"
                "• Снимайте процесс сборки букетов — это завораживает\n"
                "• Публикуйте «до и после» — пустая витрина vs полная\n"
                "• Показывайте новые поступления с утра\n"
                "• Stories каждый день важнее постов раз в неделю\n\n"
                "─────────────────\n🌷 Kalendарь цветочного бизнеса"
    },
]

_morning_idx = 0
_evening_idx = 0

def next_morning_tip():
    global _morning_idx
    t = MORNING_TIPS[_morning_idx % len(MORNING_TIPS)]
    _morning_idx += 1
    return t

def next_evening_tip():
    global _evening_idx
    t = EVENING_TIPS[_evening_idx % len(EVENING_TIPS)]
    _evening_idx += 1
    return t

# ─── Генерация изображений ───────────────────────────────────────────────────

def _fig_base(w=10, h=5.5):
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(C_BG)
    ax.set_facecolor(C_CARD)
    for spine in ax.spines.values():
        spine.set_edgecolor("#30363d")
    ax.tick_params(colors=C_WHITE)
    return fig, ax

def _save(fig) -> bytes:
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def make_tip_card(title: str, lines: list[str], emoji_top: str = "💡") -> bytes:
    """Красивая карточка для текстового поста."""
    fig = plt.figure(figsize=(10, 6))
    fig.patch.set_facecolor(C_BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 10); ax.set_ylim(0, 6)
    ax.axis("off")
    ax.set_facecolor(C_BG)

    # Боковая полоса-акцент
    ax.add_patch(plt.Rectangle((0, 0), 0.12, 6, color=C_ACCENT, zorder=2))

    # Карточка
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.25, 0.3), 9.5, 5.4,
        boxstyle="round,pad=0.15", linewidth=1.5,
        edgecolor="#30363d", facecolor=C_CARD, zorder=1,
    ))

    # Иконка
    ax.text(0.65, 5.35, emoji_top, fontsize=22, va="top", ha="left", zorder=3)

    # Заголовок
    ax.text(1.4, 5.4, title, fontsize=14, fontweight="bold",
            color=C_ACCENT, va="top", ha="left", zorder=3)

    # Текст строк
    y = 4.5
    for line in lines:
        wrapped = textwrap.fill(line, width=72)
        ax.text(0.65, y, wrapped, fontsize=9.5, color=C_WHITE,
                va="top", ha="left", zorder=3, linespacing=1.5)
        y -= 0.55 * (wrapped.count("\n") + 1) + 0.15

    # Подпись
    ax.text(9.6, 0.18, "🌷 Календарь цветочного бизнеса",
            fontsize=8, color=C_GRAY, va="bottom", ha="right", zorder=3)

    return _save(fig)


def make_seasonality_chart() -> bytes:
    """Годовой график сезонности."""
    fig, ax = _fig_base(11, 5.5)
    labels = [m[:3] for m in MONTH_NAMES[1:]]
    values = SEASON_DEMAND
    colors = [C_GREEN if v >= 100 else C_YELLOW if v >= 70 else C_RED for v in values]
    x = np.arange(12)
    bars = ax.bar(x, values, color=colors, width=0.6, edgecolor="#30363d", linewidth=0.5)
    avg = sum(values) / 12
    ax.axhline(avg, color=C_WHITE, linestyle="--", linewidth=1, alpha=0.35, label=f"Среднее: {avg:.0f}")
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 4, str(v),
                ha="center", va="bottom", color=C_WHITE, fontsize=8, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels(labels, color=C_WHITE, fontsize=9)
    ax.set_ylabel("Индекс спроса (база = 100)", color=C_GRAY, fontsize=9)
    ax.yaxis.label.set_color(C_GRAY)
    ax.set_ylim(0, 240)
    ax.set_title("📊 Сезонность продаж цветов — по месяцам", color=C_WHITE, fontsize=12, fontweight="bold", pad=12)
    patches = [
        mpatches.Patch(color=C_GREEN,  label="Пик (≥100)"),
        mpatches.Patch(color=C_YELLOW, label="Умеренный (70–99)"),
        mpatches.Patch(color=C_RED,    label="Спад (<70)"),
    ]
    ax.legend(handles=patches, facecolor=C_BG, edgecolor="#30363d", labelcolor=C_WHITE, fontsize=8, loc="upper right")
    plt.tight_layout()
    return _save(fig)


def make_monthly_flowers_chart(month: int) -> bytes:
    """Карта сезонных цветов на текущий месяц."""
    data = SEASONAL_FLOWERS[month]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))
    fig.patch.set_facecolor(C_BG)

    # Левая: цветы по категориям
    ax = axes[0]
    ax.set_facecolor(C_CARD)
    for sp in ax.spines.values(): sp.set_edgecolor("#30363d")
    ax.axis("off")
    ax.set_title(f"🌸 {MONTH_NAMES[month]}: ассортимент", color=C_WHITE, fontsize=11, fontweight="bold")

    categories = [
        ("🟢  В СЕЗОНЕ — БРАТЬ", data["peak"],      C_GREEN),
        ("🟡  ЕСТЬ В НАЛИЧИИ",   data["available"],  C_YELLOW),
        ("🔴  ИЗБЕГАТЬ",         data["avoid"],      C_RED),
    ]
    y = 0.92
    for title, flowers, color in categories:
        ax.text(0.05, y, title, transform=ax.transAxes,
                fontsize=9, fontweight="bold", color=color, va="top")
        y -= 0.09
        if flowers:
            for f in flowers:
                ax.text(0.08, y, f"• {f}", transform=ax.transAxes,
                        fontsize=8.5, color=C_WHITE, va="top")
                y -= 0.08
        else:
            ax.text(0.08, y, "— нет ограничений", transform=ax.transAxes,
                    fontsize=8.5, color=C_GRAY, va="top")
            y -= 0.08
        y -= 0.04

    ax.text(0.05, y - 0.02, f"💡 {data['tip']}", transform=ax.transAxes,
            fontsize=8, color=C_ACCENT, va="top", wrap=True)

    # Правая: индекс спроса по месяцам с выделением текущего
    ax2 = axes[1]
    ax2.set_facecolor(C_CARD)
    for sp in ax2.spines.values(): sp.set_edgecolor("#30363d")
    labels = [m[:3] for m in MONTH_NAMES[1:]]
    values = SEASON_DEMAND
    bar_colors = [C_ACCENT if i + 1 == month else "#2d333b" for i in range(12)]
    x = np.arange(12)
    bars = ax2.bar(x, values, color=bar_colors, width=0.6, edgecolor="#30363d", linewidth=0.5)
    for bar, v, bc in zip(bars, values, bar_colors):
        if bc == C_ACCENT:
            ax2.text(bar.get_x() + bar.get_width() / 2, v + 4, str(v),
                     ha="center", va="bottom", color=C_ACCENT, fontsize=9, fontweight="bold")
    ax2.set_xticks(x); ax2.set_xticklabels(labels, color=C_WHITE, fontsize=8)
    ax2.tick_params(colors=C_WHITE)
    trend = "📈 Рост" if data["trend"] == 1 else "📉 Спад"
    ax2.set_title(f"Спрос в {MONTH_NAMES[month]}: {data['demand']}  {trend}",
                  color=C_WHITE, fontsize=11, fontweight="bold")
    ax2.set_ylim(0, 240)
    ax2.set_ylabel("Индекс спроса", color=C_GRAY, fontsize=9)

    plt.suptitle(f"🌷 Календарь цветочного бизнеса | {date.today().strftime('%d.%m.%Y')}",
                 color=C_GRAY, fontsize=9, y=0.02)
    plt.tight_layout()
    return _save(fig)


def make_event_growth_chart(event_name: str, days_left: int) -> bytes:
    """График роста спроса перед праздником."""
    fig, ax = _fig_base(9, 4.5)
    weeks = ["−3 нед", "−2 нед", "−1 нед", "−3 дня", "День X"]
    growth = [10, 30, 65, 130, 210]
    colors = [C_RED, C_RED, C_YELLOW, C_YELLOW, C_GREEN]
    x = np.arange(5)
    ax.bar(x, growth, color=colors, width=0.5, edgecolor="#30363d", linewidth=0.5)
    for i, (v, c) in enumerate(zip(growth, colors)):
        ax.text(i, v + 4, f"+{v}%", ha="center", va="bottom",
                color=C_WHITE, fontsize=9, fontweight="bold")
    week_idx = {21: 0, 14: 1, 7: 2, 3: 3}.get(days_left, 0)
    ax.annotate(
        f"◀ Вы здесь\n({days_left} дн.)",
        xy=(week_idx, growth[week_idx] / 2),
        xytext=(week_idx + 0.85, growth[week_idx] + 30),
        color=C_ACCENT, fontsize=8.5, fontweight="bold",
        arrowprops=dict(arrowstyle="->", color=C_ACCENT, lw=1.5),
    )
    ax.set_xticks(x); ax.set_xticklabels(weeks, color=C_WHITE, fontsize=9)
    ax.set_ylim(0, 270)
    ax.set_ylabel("Рост спроса к базовому уровню", color=C_GRAY, fontsize=9)
    ax.set_title(f"📈 Динамика спроса перед «{event_name}»",
                 color=C_WHITE, fontsize=11, fontweight="bold", pad=10)
    plt.tight_layout()
    return _save(fig)

# ─── ИИ генерация постов ──────────────────────────────────────────────────────

async def generate_post_with_ai(idea: str) -> str:
    if not ANTHROPIC_KEY:
        return f"💡 *Совет для флористов*\n\n{idea}\n\n─────────────────\n🌷 Календарь цветочного бизнеса"
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
            messages=[{
                "role": "user",
                "content": (
                    "Ты эксперт по цветочному бизнесу. Напиши короткий профессиональный пост "
                    "для Telegram-канала для флористов и владельцев цветочных магазинов. "
                    "Язык: русский. Стиль: живой, практичный, без воды.\n\n"
                    "Структура:\n"
                    "- Цепляющий заголовок с эмодзи (жирный)\n"
                    "- 3–5 конкретных практических пункта\n"
                    "- Краткий вывод или призыв к действию\n\n"
                    "ВАЖНО: никогда не указывай цены — ни примерные, ни ориентировочные.\n\n"
                    "Идея: " + idea + "\n\n"
                    "В конце добавь:\n─────────────────\n🌷 Календарь цветочного бизнеса"
                ),
            }],
        )
        return resp.content[0].text.strip()
    except Exception as ex:
        log.error(f"Claude API error: {ex}")
        return f"💡 *Совет для флористов*\n\n{idea}\n\n─────────────────\n🌷 Календарь цветочного бизнеса"

# ─── Очередь согласования ────────────────────────────────────────────────────

def load_pending() -> dict:
    return load_json(PENDING_FILE, {})

def save_pending(data: dict):
    save_json(PENDING_FILE, data)

def add_pending(post_id: str, text: str, photo: bytes = None):
    p = load_pending()
    p[post_id] = {"text": text, "has_photo": photo is not None}
    save_pending(p)
    if photo:
        Path(f"pending_{post_id}.png").write_bytes(photo)

def get_pending(post_id: str) -> dict | None:
    return load_pending().get(post_id)

def remove_pending(post_id: str):
    p = load_pending()
    p.pop(post_id, None)
    save_pending(p)
    Path(f"pending_{post_id}.png").unlink(missing_ok=True)

def approval_keyboard(post_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"approve:{post_id}"),
        InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit:{post_id}"),
        InlineKeyboardButton(text="❌ Отменить",     callback_data=f"reject:{post_id}"),
    ]])

# ─── Публикация ───────────────────────────────────────────────────────────────

async def publish_to_channel(bot: Bot, text: str, photo: bytes = None):
    try:
        if photo:
            await bot.send_photo(
                CHANNEL_ID,
                photo=BufferedInputFile(photo, filename="img.png"),
                caption=text, parse_mode="Markdown",
            )
        else:
            await bot.send_message(CHANNEL_ID, text, parse_mode="Markdown")
        log.info("Published to channel")
    except Exception as ex:
        log.error(f"Channel post error: {ex}")

async def post_to_channel(bot: Bot, text: str, photo: bytes = None):
    """Отправляет пост на согласование админу."""
    import uuid
    post_id = uuid.uuid4().hex[:8]
    add_pending(post_id, text, photo)

    preview = text[:800] + ("..." if len(text) > 800 else "")

    for admin_id in ADMIN_IDS:
        try:
            if photo:
                await bot.send_photo(
                    admin_id,
                    photo=BufferedInputFile(photo, filename="img.png"),
                    caption=f"📋 *Новый пост на согласование:*\n\n{preview}",
                    parse_mode="Markdown",
                    reply_markup=approval_keyboard(post_id),
                )
            else:
                await bot.send_message(
                    admin_id,
                    f"📋 *Новый пост на согласование:*\n\n{preview}",
                    parse_mode="Markdown",
                    reply_markup=approval_keyboard(post_id),
                )
        except Exception as ex:
            log.error(f"Approval send error: {ex}")

# ─── Расписание ───────────────────────────────────────────────────────────────

async def daily_check(bot: Bot):
    today = date.today()
    log.info(f"Daily check: {today}")
    events = get_events(today.year) + get_events(today.year + 1)
    for event in events:
        for days in NOTIFY_DAYS:
            if event["date"] - timedelta(days=days) == today:
                text = build_notification(event["name"], event["date"], days)
                save_to_history(event["name"], days, text)
                chart = make_event_growth_chart(event["name"], days)
                await post_to_channel(bot, text, photo=chart)
                log.info(f"Sent: {event['name']} in {days} days")

async def morning_post(bot: Bot):
    tip = next_morning_tip()
    # Извлекаем заголовок и строки для карточки
    lines_raw = tip["text"].split("\n")
    title = lines_raw[0].replace("*", "").strip() if lines_raw else "Совет"
    body_lines = [l for l in lines_raw[2:] if l and not l.startswith("─") and "Календарь" not in l]
    img = make_tip_card(title, body_lines, emoji_top="💡")
    await post_to_channel(bot, tip["text"], photo=img)

async def evening_post(bot: Bot):
    tip = next_evening_tip()
    lines_raw = tip["text"].split("\n")
    title = lines_raw[0].replace("*", "").strip() if lines_raw else "Совет"
    body_lines = [l for l in lines_raw[2:] if l and not l.startswith("─") and "Календарь" not in l]
    img = make_tip_card(title, body_lines, emoji_top="🌸")
    await post_to_channel(bot, tip["text"], photo=img)

async def monthly_flowers_post(bot: Bot):
    m = date.today().month
    data = SEASONAL_FLOWERS[m]
    trend_word = "📈 рост спроса" if data["trend"] == 1 else "📉 спад спроса"
    text = (
        f"🌸 *Сезонный анализ: {MONTH_NAMES[m]}*\n\n"
        f"Индекс спроса: *{data['demand']}* — {trend_word}\n\n"
        f"🟢 *В сезоне — брать:*\n" +
        "\n".join(f"• {f}" for f in data["peak"]) +
        f"\n\n🟡 *Доступно:*\n" +
        "\n".join(f"• {f}" for f in data["available"]) +
        (f"\n\n🔴 *Избегать:*\n" + "\n".join(f"• {f}" for f in data["avoid"]) if data["avoid"] else "") +
        f"\n\n💡 {data['tip']}\n\n"
        f"─────────────────\n🌷 Календарь цветочного бизнеса"
    )
    chart = make_monthly_flowers_chart(m)
    await post_to_channel(bot, text, photo=chart)

async def monthly_seasonality_post(bot: Bot):
    today = date.today()
    text = (
        f"📊 *Годовая сезонность продаж цветов*\n"
        f"🗓 {today.strftime('%d.%m.%Y')}\n\n"
        f"🟢 *Пики:* Март (+100%), Сентябрь (+65%), Май (+55%)\n"
        f"🟡 *Умеренный:* Февраль, Ноябрь, Декабрь\n"
        f"🔴 *Спад:* Июнь–Август, Январь\n\n"
        f"💡 В периоды спада: акции, горшечные растения,\n"
        f"корпоративные подписки, работа с базой клиентов.\n\n"
        f"─────────────────\n🌷 Календарь цветочного бизнеса"
    )
    chart = make_seasonality_chart()
    await post_to_channel(bot, text, photo=chart)

# ─── Инлайн-клавиатура подтверждения ─────────────────────────────────────────

def _confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data="post_yes"),
        InlineKeyboardButton(text="❌ Отменить",     callback_data="post_no"),
    ]])

# ─── Роутер ───────────────────────────────────────────────────────────────────

router = Router()

WELCOME_TEXT = (
    "🌹 *Добро пожаловать в канал, где цветочный бизнес расцветает\\!*\n\n"
    "Привет, коллеги\\! 👋 Мы создали это пространство специально для вас — тех, кто живет букетами, "
    "знает толк в сортах и понимает, что флористика — это не просто работа, это искусство, которое приносит доход\\.\n\n"
    "*Здесь вы найдете:*\n\n"
    "🎯 Практические лайфхаки по продажам\n\n"
    "💐 Актуальные тренды флористики\n\n"
    "📊 Разбор сезонов и праздников\n\n"
    "💰 Кейсы и истории успеха\n\n"
    "🔗 Контакты поставщиков и полезные ресурсы\n\n"
    "Ваши букеты заслуживают большей прибыли\\! 💚\n\n"
    "─────────────────\n"
    "🌷 Календарь цветочного бизнеса"
)

@router.message(CommandStart())
async def cmd_start(message: Message):
    subs = load_subscribers()
    uid = message.from_user.id
    if uid not in subs:
        subs.add(uid); save_subscribers(subs)
    await message.answer(WELCOME_TEXT, parse_mode="MarkdownV2")

@router.message(Command("stop"))
async def cmd_stop(message: Message):
    subs = load_subscribers(); subs.discard(message.from_user.id); save_subscribers(subs)
    await message.answer("❌ Вы отписались. Чтобы подписаться снова — /start")

@router.message(Command("events"))
async def cmd_events(message: Message):
    today = date.today()
    events = get_events(today.year) + get_events(today.year + 1)
    upcoming = sorted([e for e in events if e["date"] >= today], key=lambda e: e["date"])[:7]
    lines = [f"📅 *Ближайшие события* (сегодня {today.strftime('%d.%m.%Y')}):\n"]
    for e in upcoming:
        days = (e["date"] - today).days
        lines.append(f"• {e['name']} — {e['date'].strftime('%d.%m.%Y')} (через {days} дн.)")
    await message.answer("\n".join(lines), parse_mode="Markdown")

@router.message(Command("season"))
async def cmd_season(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("📊 Генерирую...")
    await monthly_flowers_post(message.bot)
    await message.answer("✅ Опубликовано.")

@router.message(Command("test"))
async def cmd_test(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    today = date.today()
    events = sorted(
        [e for e in get_events(today.year) + get_events(today.year + 1) if e["date"] >= today],
        key=lambda e: e["date"],
    )
    if not events: await message.answer("Нет предстоящих событий."); return
    event = events[0]
    days = 7
    text = build_notification(event["name"], event["date"], days)
    chart = make_event_growth_chart(event["name"], days)
    await post_to_channel(message.bot, text, photo=chart)
    await message.answer(f"✅ Тест опубликован: «{event['name']}»")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    subs = load_subscribers(); history = load_history()
    m = date.today().month
    data = SEASONAL_FLOWERS[m]
    trend = "📈 рост" if data["trend"] == 1 else "📉 спад"
    await message.answer(
        f"📊 *Статистика*\n"
        f"🗓 Сегодня: {date.today().strftime('%d.%m.%Y')}\n"
        f"👥 Подписчиков бота: {len(subs)}\n"
        f"📬 Уведомлений в архиве: {len(history)}\n\n"
        f"🌸 *Текущий месяц ({MONTH_NAMES[m]}):*\n"
        f"Спрос: {data['demand']} — {trend}\n"
        f"В сезоне: {', '.join(data['peak'][:3])}",
        parse_mode="Markdown",
    )

@router.message()
async def handle_plain_text(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    raw = (message.text or "").strip()
    if not raw or raw.startswith("/"): return

    # Режим редактирования scheduled поста
    edit_id_file = Path("editing_post_id.txt")
    if edit_id_file.exists():
        post_id = edit_id_file.read_text(encoding="utf-8").strip()
        edit_id_file.unlink(missing_ok=True)
        pending = get_pending(post_id)
        if pending:
            p = load_pending()
            p[post_id]["text"] = raw
            save_pending(p)
            photo = Path(f"pending_{post_id}.png").read_bytes() if pending["has_photo"] else None
            if photo:
                await message.answer_photo(
                    BufferedInputFile(photo, filename="img.png"),
                    caption=f"*Обновлённый пост:*\n\n{raw}\n\nОпубликовать?",
                    parse_mode="Markdown", reply_markup=approval_keyboard(post_id),
                )
            else:
                await message.answer(
                    f"*Обновлённый пост:*\n\n{raw}\n\nОпубликовать?",
                    parse_mode="Markdown", reply_markup=approval_keyboard(post_id),
                )
        return

    # Новая идея для поста
    wait = await message.answer("✍️ Claude пишет пост...")
    post = await generate_post_with_ai(raw)
    Path("pending_post.txt").write_text(post, encoding="utf-8")
    await wait.delete()
    await message.answer(
        f"*Вот готовый пост:*\n\n{post}\n\nОтправить в канал?",
        parse_mode="Markdown", reply_markup=_confirm_kb(),
    )

@router.callback_query(F.data == "post_yes")
async def cb_yes(call: CallbackQuery):
    p = Path("pending_post.txt")
    if not p.exists(): await call.answer("Пост не найден.", show_alert=True); return
    post = p.read_text(encoding="utf-8"); p.unlink()
    lines_raw = post.split("\n")
    title = lines_raw[0].replace("*", "").strip()
    body_lines = [l for l in lines_raw[2:] if l and not l.startswith("─") and "Календарь" not in l]
    img = make_tip_card(title, body_lines)
    await publish_to_channel(call.bot, post, photo=img)
    await call.message.edit_text("✅ Опубликовано в канале!")
    await call.answer()

@router.callback_query(F.data == "post_no")
async def cb_no(call: CallbackQuery):
    Path("pending_post.txt").unlink(missing_ok=True)
    await call.message.edit_text("🗑 Пост отменён.")
    await call.answer()

# ─── Обработчики согласования scheduled постов ───────────────────────────────

@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(call: CallbackQuery):
    post_id = call.data.split(":", 1)[1]
    pending = get_pending(post_id)
    if not pending:
        await call.answer("Пост не найден.", show_alert=True); return
    text = pending["text"]
    photo = Path(f"pending_{post_id}.png").read_bytes() if pending["has_photo"] else None
    remove_pending(post_id)
    await publish_to_channel(call.bot, text, photo=photo)
    await call.message.edit_caption("✅ Опубликовано в канале!") if pending["has_photo"] else await call.message.edit_text("✅ Опубликовано в канале!")
    await call.answer()

@router.callback_query(F.data.startswith("reject:"))
async def cb_reject(call: CallbackQuery):
    post_id = call.data.split(":", 1)[1]
    remove_pending(post_id)
    await call.message.edit_caption("❌ Пост отменён.") if call.message.photo else await call.message.edit_text("❌ Пост отменён.")
    await call.answer()

@router.callback_query(F.data.startswith("edit:"))
async def cb_edit(call: CallbackQuery):
    post_id = call.data.split(":", 1)[1]
    pending = get_pending(post_id)
    if not pending:
        await call.answer("Пост не найден.", show_alert=True); return
    # Сохраняем ID для редактирования
    Path("editing_post_id.txt").write_text(post_id, encoding="utf-8")
    await call.message.reply(
        "✏️ Отправь новый текст поста — я заменю им текущий и опубликую в канал.\n\n"
        "Или напиши /cancel чтобы оставить как есть."
    )
    await call.answer()

@router.message(Command("cancel"))
async def cmd_cancel(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    Path("editing_post_id.txt").unlink(missing_ok=True)
    await message.answer("↩️ Редактирование отменено.")

# ─── Запуск ───────────────────────────────────────────────────────────────────

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

    # Ежедневная проверка праздников
    scheduler.add_job(daily_check, "cron", hour=9, minute=0, args=[bot])

    # Утренний пост (10:00)
    scheduler.add_job(morning_post, "cron", hour=10, minute=0, args=[bot])

    # Вечерний пост (19:00)
    scheduler.add_job(evening_post, "cron", hour=19, minute=0, args=[bot])

    # 1-е число: сезонные цветы месяца
    scheduler.add_job(monthly_flowers_post, "cron", day=1, hour=11, minute=0, args=[bot])

    # 15-е число: годовой график сезонности
    scheduler.add_job(monthly_seasonality_post, "cron", day=15, hour=11, minute=0, args=[bot])

    scheduler.start()
    log.info(f"Bot started. Today: {date.today()}. Channel: {CHANNEL_ID}")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
