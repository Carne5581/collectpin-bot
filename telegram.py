import os
import sys
import csv
import io
import json
import html
import time
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

# ==================================================================
# CollectPin — автопостинг газеты в Telegram
#
# Файл лежит в КОРНЕ репозитория collectpin-bot рядом с bot.py.
# Запускается своим расписанием: .github/workflows/telegram.yml
#
# ЧТО ДЕЛАЕТ
#   Раз в полчаса читает лист «gazeta» таблицы GTA 6 (опубликованный
#   CSV — тот же, что забирает сайт). В канал один раз уходит новость,
#   у которой в status стоит «да» И громкость (колонка hype) 9 или 10.
#   В газете на сайте — всё одобренное, в канале — только самое громкое.
#   Хочешь отправить в канал новость с громкостью ниже — впиши ей
#   в hype 9 руками.
#
# ЧТО НЕ ДЕЛАЕТ
#   • Не публикует черновики (status пустой), новости GTA 5
#     и новости с громкостью ниже 9 (или без громкости).
#   • Не публикует старьё: новость старше 3 дней пропускает.
#   • Не пишет ночью: с 01:00 до 08:00 по Москве новости ждут утра.
#   • Не заваливает канал: не больше 2 постов за запуск и 6 в сутки.
#   • Ни личных сообщений, ни партнёрских ссылок, ни имени владельца.
#
# ПЕРВЫЙ ЗАПУСК
#   Всё, что уже одобрено к этому моменту, помечается как
#   «уже было» и в канал НЕ уходит. Иначе канал получил бы пачку
#   старых новостей разом. Публикуются только новые одобрения.
#
# РЕЖИМЫ (кнопка Run workflow на GitHub, список «Что сделать»)
#   obychnyy  — обычная работа.
#   proverka  — ничего не публикует. Проверяет: жив ли бот,
#               админ ли он в канале, читается ли лист gazeta,
#               и показывает, что ушло бы в канал прямо сейчас.
#
# СЕКРЕТЫ (Settings → Secrets and variables → Actions)
#   TG_BOT_TOKEN — токен от @BotFather
#   TG_CHANNEL   — @collectpin
#
# Что уже опубликовано — в файле tg_posted.json (бот сам его ведёт).
# ==================================================================

TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
CHANNEL = os.environ.get("TG_CHANNEL", "").strip()
API = os.environ.get("TG_API", "https://api.telegram.org")
NEWS_CSV = os.environ.get("NEWS_CSV_URL") or (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vTo8nlXDEW0d2p7_w7gHPCHOOUAwn9inlhTsK7jHz6udhtMK9xYXbGq9KPE1rwuO0xrqjmZoIUYH0at"
    "/pub?gid=1686448780&single=true&output=csv")
STATE = "tg_posted.json"
MSK = timezone(timedelta(hours=3))

GROMKOST = 9         # в канал — только новости с hype не ниже этого
ZA_RAZ = 2           # постов за один запуск
V_DEN = 6            # постов в сутки
SVEZHEST_DNEY = 3    # новость старше — не публикуем
NOCH = (1, 8)        # с 01:00 до 08:00 по Москве молчим
OK = ["да", "+", "ok", "ок", "yes", "1", "true", "✓", "v"]   # как на сайте (gazeta.js)
KARTA = "https://collectpin.com/ru/gta-6/map/"


def load_state():
    if not os.path.exists(STATE):
        return None
    try:
        with open(STATE, encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("posted", [])
        s.setdefault("log", [])
        return s
    except Exception as e:
        print(f"tg_posted.json не читается ({e}) — начинаю заново как первый запуск.")
        return None


def save_state(s):
    s["log"] = s["log"][-300:]
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def read_news():
    """Строки листа gazeta как список словарей."""
    if NEWS_CSV.startswith("file:"):
        text = open(NEWS_CSV[5:], encoding="utf-8").read()
    else:
        r = requests.get(NEWS_CSV, timeout=40, headers={"User-Agent": "CollectPin telegram bot"})
        if r.status_code != 200:
            raise RuntimeError(f"лист gazeta ответил кодом {r.status_code}")
        r.encoding = "utf-8"
        text = r.text
    if text.lstrip().startswith("<"):
        raise RuntimeError("вместо таблицы пришла веб-страница — проверь, что лист gazeta опубликован как CSV")
    rows = list(csv.DictReader(io.StringIO(text)))
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in rows]


def parse_date(s):
    s = (s or "").strip().lstrip("'")
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def candidates(rows):
    """Одобренные громкие новости GTA 6 с заголовком, по порядку даты."""
    out = []
    for r in rows:
        if r.get("status", "").lower() not in OK:
            continue
        try:
            hype = float(r.get("hype", "").replace(",", "."))
        except ValueError:
            hype = 0
        if hype < GROMKOST:
            continue
        if r.get("cat", "").lower() not in ("", "gta6", "gta 6"):
            continue
        if not r.get("id") or not r.get("title_ru"):
            continue
        out.append(r)
    out.sort(key=lambda r: (parse_date(r.get("date")) or datetime(2000, 1, 1).date(), r["id"]))
    return out


def domain(url):
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


def message(r):
    e = lambda s: html.escape(s or "", quote=False)
    parts = [f"<b>📰 {e(r['title_ru'])}</b>"]
    if r.get("text_ru"):
        parts.append(e(r["text_ru"]))
    if "слух" in r.get("tag", "").lower() or "rumor" in r.get("tag", "").lower():
        parts.append("⚠️ <i>Это слух — официально не подтверждено.</i>")
    src = r.get("source", "")
    tail = []
    if src.startswith("http"):
        tail.append(f'Источник: <a href="{html.escape(src)}">{e(domain(src))}</a>')
    tail.append(f'🗺 <a href="{KARTA}">Карта GTA 6</a> · газета Leonida Daily на CollectPin')
    parts.append("\n".join(tail))
    return "\n\n".join(parts)


def tg(method, **params):
    r = requests.post(f"{API}/bot{TOKEN}/{method}", json=params, timeout=40)
    try:
        d = r.json()
    except Exception:
        d = {"ok": False, "description": r.text[:200]}
    return d


def send(r):
    """True — опубликовано. Повторяет один раз, если Telegram просит подождать."""
    params = {"chat_id": CHANNEL, "text": message(r), "parse_mode": "HTML"}
    src = r.get("source", "")
    if src.startswith("http"):
        params["link_preview_options"] = {"url": src, "prefer_large_media": True}
    for _ in range(2):
        d = tg("sendMessage", **params)
        if d.get("ok"):
            return True
        wait = (d.get("parameters") or {}).get("retry_after")
        if wait and wait <= 60:
            print(f"  Telegram просит подождать {wait} с")
            time.sleep(wait + 1)
            continue
        print(f"  Telegram не принял: {d.get('description')}")
        return False
    return False


def proverka():
    print("ПРОВЕРКА — в канал ничего не публикуется\n")
    plokho = False
    if not TOKEN or not CHANNEL:
        print("✗ Не заданы секреты TG_BOT_TOKEN и/или TG_CHANNEL.")
        sys.exit(1)
    me = tg("getMe")
    if not me.get("ok"):
        print(f"✗ Токен не подходит: {me.get('description')}")
        sys.exit(1)
    bot = me["result"]
    print(f"✓ Бот: @{bot.get('username')}")
    ch = tg("getChat", chat_id=CHANNEL)
    if not ch.get("ok"):
        print(f"✗ Канал {CHANNEL} не найден или бот в него не добавлен: {ch.get('description')}")
        plokho = True
    else:
        print(f"✓ Канал: {ch['result'].get('title')}")
        m = tg("getChatMember", chat_id=CHANNEL, user_id=bot["id"])
        st = (m.get("result") or {})
        if st.get("status") == "administrator" and st.get("can_post_messages", True):
            print("✓ Бот — администратор канала и может публиковать")
        else:
            print(f"✗ Бот не администратор канала или без права публикации (статус: {st.get('status')})")
            plokho = True
    try:
        rows = read_news()
        print(f"✓ Лист gazeta читается: строк {len(rows)}")
    except Exception as e:
        print(f"✗ Лист gazeta не читается: {e}")
        sys.exit(1)
    s = load_state()
    c = candidates(rows)
    if s is None:
        print(f"\nПервого запуска ещё не было. Одобрено с громкостью {GROMKOST}+: {len(c)} — при первом запуске")
        print("они будут помечены как «уже было» и в канал не уйдут.")
    else:
        novye = [r for r in c if r["id"] not in s["posted"]]
        print(f"\nУже опубликовано: {len(s['posted'])}. Ждут публикации (одобрено и громкость {GROMKOST}+): {len(novye)}")
        for r in novye[:5]:
            print("\n--- так будет выглядеть пост ---")
            print(message(r))
    if plokho:
        sys.exit(1)


def obychnyy():
    if not TOKEN or not CHANNEL:
        print("Не заданы секреты TG_BOT_TOKEN и/или TG_CHANNEL — публиковать некуда.")
        sys.exit(1)
    rows = read_news()
    c = candidates(rows)
    s = load_state()
    now = datetime.now(MSK)
    if s is None:
        s = {"posted": [r["id"] for r in c], "init": now.strftime("%Y-%m-%d %H:%M"), "log": []}
        save_state(s)
        print(f"Первый запуск: {len(c)} уже одобренных новостей помечены как «уже было».")
        print("В канал пойдут только те, что одобрят после этого.")
        return
    novye = [r for r in c if r["id"] not in s["posted"]]
    print(f"Одобрено всего: {len(c)}, новых для канала: {len(novye)}")
    if not novye:
        return
    if NOCH[0] <= now.hour < NOCH[1]:
        print(f"Сейчас {now:%H:%M} по Москве — ночью не публикую, подожду утра.")
        return
    segodnya = now.strftime("%Y-%m-%d")
    uzhe = sum(1 for x in s["log"] if x.get("d") == segodnya and x.get("ok"))
    granica = now.date() - timedelta(days=SVEZHEST_DNEY)
    vyshlo = 0
    for r in novye:
        d = parse_date(r.get("date"))
        if d and d < granica:
            print(f"  {r['id']}: новость от {d} — старше {SVEZHEST_DNEY} дней, пропускаю")
            s["posted"].append(r["id"])
            s["log"].append({"id": r["id"], "d": segodnya, "ok": False, "why": "старая"})
            continue
        if vyshlo >= ZA_RAZ or uzhe >= V_DEN:
            print(f"  {r['id']}: подождёт следующего запуска (лимит)")
            continue
        print(f"Публикую {r['id']}: {r['title_ru'][:80]}")
        if send(r):
            s["posted"].append(r["id"])
            s["log"].append({"id": r["id"], "d": segodnya, "t": now.strftime("%H:%M"), "ok": True})
            vyshlo += 1
            uzhe += 1
            time.sleep(3)
        else:
            save_state(s)
            print("Публикация не удалась — попробую в следующий запуск.")
            sys.exit(1)
    save_state(s)
    print(f"Готово: опубликовано {vyshlo}.")


if __name__ == "__main__":
    rezhim = (os.environ.get("MODE") or (sys.argv[1] if len(sys.argv) > 1 else "obychnyy")).strip().lower()
    print(f"Режим: {rezhim}\n")
    if rezhim == "proverka":
        proverka()
    elif rezhim == "obychnyy":
        obychnyy()
    else:
        print("Бывают режимы: obychnyy, proverka")
        sys.exit(1)
