import os
import sys
import json
import re
import html
import requests
import feedparser
import time
import difflib
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
from groq import Groq

# ==================================================================
# CollectPin — ИИ-скаут
#
# Две работы:
#   1. НАХОДКИ — ищет посты о коллекционках GTA 6 и кладёт кандидатов
#      в лист sbor_ai (как раньше).
#   2. НОВОСТИ — с 1 ноября ищет самые громкие новости GTA 6 и кладёт
#      черновики в лист gazeta. На сайт новость попадает, только когда
#      владелец впишет «да» в колонку status.
#
# Режимы. Переключаются кнопкой Run workflow на GitHub,
# в выпадающем списке.
#
#   obychnyy  — обычная работа: читает источники, показывает
#               кандидатов ИИ, отправляет в Таблицу.
#
#   proverka  — только проверяет источники. Ни одного обращения
#               к ИИ, ни одного к Таблице, ничего не меняет.
#               Показывает по каждому источнику: жив ли, сколько
#               записей, заголовок самой свежей. Этим режимом
#               проверяются адреса каналов и сабреддитов.
#
#   novosti   — только новости, без поиска находок.
#
#   test      — отправляет в Таблицу одну выдуманную находку
#               с пометкой ТЕСТ (и одну тестовую новость в gazeta,
#               если новостной вебхук настроен). Нужен, чтобы убедиться, что
#               пароль и вебхук соединены. Без него это проверить
#               нельзя: в обычном режиме бот молчит, пока в лентах
#               нет новых постов, и непонятно, работает связь
#               или нет.
#
# ------------------------------------------------------------------
# ЧТО ИСПРАВЛЕНО 16 АВГУСТА
#
# 1. Два адреса каналов YouTube были выдуманы — оба отдавали 404.
#    Настоящие подставлены и сверены по независимым источникам.
#    У PowerPyx старый адрес совпадал с настоящим по первым
#    семнадцати знакам и расходился в хвосте: модель помнила
#    начало и досочинила конец. Такую ошибку глазами не поймать,
#    поэтому теперь есть режим proverka.
#
# 2. Сабреддиты читаются каждый по отдельности.
#    Было: r/GTA6+GTA6_Mapping одной лентой. Reddit в таком случае
#    молча выбрасывает несуществующий сабреддит и отдаёт 200
#    с записями из оставшегося. То есть мёртвый сабреддит выглядел
#    как живой. По отдельности такое сразу видно.
#
# 3. Находки больше не теряются. requests не считает ошибкой ответ
#    вида 401 или 500 — исключения не будет. Раньше бот при отказе
#    Таблицы всё равно помечал посты прочитанными, и кандидаты
#    пропадали навсегда. Теперь ответ проверяется по-настоящему.
#
# 4. Модель читает текст, а не HTML с тегами.
#
# 5. Отправка идёт с паролем.
#
# ------------------------------------------------------------------
# ЧТО ИСПРАВЛЕНО 1 НОЯБРЯ (версия 8)
#
# 1. Бот больше не отбрасывает ВСЁ после релиза. В подсказке модели
#    было жёстко написано «игра ещё не вышла» — 19 ноября он отбраковал
#    бы каждую настоящую находку. Теперь правило зависит от даты поста.
#
# 2. Если модель на Groq снимут с поддержки или упрётся в дневной
#    лимит, бот пробует следующую из списка GROQ_MODELS.
#
# 3. Запуск краснеет, если сломалось важное: не читается ни один
#    источник или ИИ не ответил ни разу. GitHub пришлёт письмо.
#
# 4. После каждого запуска бот пишет отчёт в README.md — он виден
#    прямо на главной странице репозитория.
#
# 5. Новости для газеты (см. выше).
# ==================================================================


GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL")
WEBHOOK_KEY  = os.environ.get("WEBHOOK_KEY")
LOG_FILE     = "seen_posts.json"
REPORT_FILE  = "README.md"

# Новостной вебхук — отдельное веб-приложение Apps Script (файл
# apps-script-gazeta.gs). Пароль тот, что вписан в нём.
NEWS_WEBHOOK_URL = os.environ.get("NEWS_WEBHOOK_URL")
NEWS_WEBHOOK_KEY = os.environ.get("NEWS_WEBHOOK_KEY")

# Модели Groq по порядку. Первая не отвечает — берётся следующая.
GROQ_MODELS = [m.strip() for m in os.environ.get(
    "GROQ_MODELS",
    "llama-3.3-70b-versatile,openai/gpt-oss-120b,llama-3.1-8b-instant"
).split(",") if m.strip()]

# День релиза. До него находок в GTA 6 быть не может.
RELIZ = datetime(2026, 11, 19, tzinfo=timezone.utc)

# Сколько новостей за один запуск показывать ИИ. Бесплатный лимит
# Groq — около 100 тысяч токенов в сутки на модель; при запуске
# каждые 15 минут больше брать нельзя.
NEWS_ZA_RAZ = 12
# Порог «громкости» от 1 до 10: тише — в газету не предлагаем.
NEWS_POROG = 6

# ── АВТООДОБРЕНИЕ НОВОСТЕЙ ──
# Черновик сразу получает status «да» (и попадает на сайт), если:
#   • громкость не ниже NEWS_AVTO_POROG,
#   • в тексте нет чисел, которых не было в заголовке (иначе — «проверь»),
#   • и либо издание есть в списке NADEZHNYE, либо ту же новость в этом
#     запуске дали два разных издания и больше.
# Остальное приходит с пустым status — решаешь сам: «да» или «нет».
# Не больше NEWS_AVTO_V_DEN автоматически в сутки, чтобы газета не забивалась.
NEWS_AVTO_POROG = 7
NEWS_AVTO_V_DEN = 4
NADEZHNYE = [
    "rockstar", "take-two", "ign", "gamespot", "kotaku", "eurogamer", "pc gamer", "polygon",
    "vgc", "video games chronicle", "gamesradar", "the verge", "bloomberg", "reuters",
    "insider gaming", "push square", "pure xbox", "playstation", "xbox wire", "forbes",
    "variety", "rolling stone", "the game business", "mashable", "dexerto", "gamerant", "game rant",
    "stopgame", "игромания", "igromania", "канобу", "kanobu", "dtf", "3dnews", "shazoo",
    "ixbt", "riot pixels", "goha", "cybersport", "vgtimes", "overclockers", "playground",
]

# Отчёт для README.md, собирается по ходу запуска
OTCHET = {"istochniki": [], "ii_ok": 0, "ii_oshibki": 0, "model": "",
          "nahodki": 0, "novosti": 0, "zametki": []}


# ── ИСТОЧНИКИ ───────────────────────────────────────────────
#
# Добавить источник — дописать строку. Удалить — стереть строку.
# После любой правки запусти режим proverka и убедись, что стоит ✓.
#
# Как узнать адрес ленты канала YouTube:
#   1. Открыть канал в браузере
#   2. Правой кнопкой → «Показать код страницы»
#   3. Ctrl+F, искать   channelId
#   4. Скопировать значение вида UC...... (24 знака)
#   5. Подставить в https://www.youtube.com/feeds/videos.xml?channel_id=ЗДЕСЬ
#
# Как узнать адрес ленты сабреддита:
#   https://www.reddit.com/r/ИМЯ/new.rss?limit=50
#   Сначала открой reddit.com/r/ИМЯ и убедись, что он существует.

# ВАЖНО: сабреддиты идут ЧЕРЕЗ ОДИН с остальными, а не подряд.
# Reddit считает обращения по времени, и три запроса подряд он
# отбивает ответом 429 — ровно это и случилось на первом запуске.
# Когда между ними стоит YouTube, Reddit успевает остыть сам,
# без длинного ожидания.
SOURCES = [
    {
        "name": "Reddit · r/GTA6",
        "url": "https://www.reddit.com/r/GTA6/new.rss?limit=50",
        "pro": "сабреддит про GTA 6",
    },
    {
        # GTA Series Videos — самый авторитетный канал по GTA.
        # Адрес сверен: UCuWcjpKbIDAbZfHoru1toFg
        "name": "YouTube · GTA Series Videos",
        "pro": "канал про все игры серии GTA, включая старые",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCuWcjpKbIDAbZfHoru1toFg",
        "yt": "@GTASeriesVideos",
    },
    {
        "name": "Reddit · r/GTAVI",
        "url": "https://www.reddit.com/r/GTAVI/new.rss?limit=50",
        "pro": "сабреддит про GTA 6",
    },
    {
        # PowerPyx — трофейные гайды, выходят в первые дни.
        # Адрес сверен: UCWBA1-H9A5IldSb3tNwQmtQ
        # В старой версии стояло UCWBA1-H9A5IldSb3JC5Tq1w — 404.
        "name": "YouTube · PowerPyx",
        "pro": "канал с гайдами по разным играм, не только GTA",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCWBA1-H9A5IldSb3tNwQmtQ",
        "yt": "@PowerPyx",
    },
    {
        # Охотники за секретами GTA 5. После релиза этот народ
        # первым полезет искать тайники в GTA 6.
        "name": "Reddit · r/chiliadmystery",
        "url": "https://www.reddit.com/r/chiliadmystery/new.rss?limit=50",
        # ВАЖНО: это сабреддит про секреты GTA 5, а не GTA 6.
        # До релиза он приносит только находки из старой игры —
        # именно они и попали в таблицу 17 августа. Держим его
        # ради 19 ноября: эти люди первыми полезут искать в GTA 6.
        "pro": "сабреддит про секреты GTA 5, НЕ про GTA 6",
    },
    {
        # TGG — гайды по GTA.
        # Адрес сверен: UC72PuhDwKtZ5MikpGNhPAtA
        "name": "YouTube · TGG",
        "pro": "канал про GTA Online и GTA 5, иногда новости GTA 6",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC72PuhDwKtZ5MikpGNhPAtA",
        "yt": "@TGG",
    },
    {
        "name": "Google Новости · GTA 6 location/found/map",
        "pro": "новостная выдача, много статей не про находки",
        "url": "https://news.google.com/rss/search?q=GTA+6+location+OR+found+OR+map&hl=en-US&gl=US&ceid=US:en",
    },
]

# ── ИСТОЧНИКИ НОВОСТЕЙ ──────────────────────────────────────
# Выдача Google Новостей за последние двое суток. Rockstar отдельно:
# всё, что вышло на её сайте за неделю.
def gnews(zapros, ru=False):
    if ru:
        return ("https://news.google.com/rss/search?q=" + quote_plus(zapros) +
                "&hl=ru&gl=RU&ceid=RU:ru")
    return ("https://news.google.com/rss/search?q=" + quote_plus(zapros) +
            "&hl=en-US&gl=US&ceid=US:en")

NEWS_SOURCES = [
    {"name": "Новости · Rockstar",
     "url": gnews('site:rockstargames.com "Grand Theft Auto VI" when:7d')},
    {"name": "Новости · GTA 6 (англ.)",
     "url": gnews('"GTA 6" OR "GTA VI" OR "Grand Theft Auto VI" when:2d')},
    {"name": "Новости · GTA 6 (рус.)",
     "url": gnews('GTA 6 when:2d', ru=True)},
]

NEWS_SLOVA = ["gta 6", "gta vi", "gta6", "grand theft auto vi", "гта 6", "гта6"]


# Reddit просит присылать своё, описательное имя программы, а не
# притворяться браузером. Запросы от «браузера» с серверов дата-центра
# он режет заметно охотнее.
HEADERS_REDDIT = {
    "User-Agent": "collectpin-scout/7.1 (+https://collectpin.com)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CollectPin-Scout/7.1",
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
}

# Сколько ждать после обращения к сайту, прежде чем идти к нему снова.
# Reddit считает запросы по адресу сервера, а адреса у GitHub общие
# на всех — то есть лимит мы делим с тысячами чужих ботов. Отсюда
# длинная пауза именно для него.
PAUZA = {
    "www.reddit.com": 15,
    "news.google.com": 3,
    "www.youtube.com": 2,
}
PAUZA_PO_UMOLCHANIYU = 2


def zagolovki_dlya(url):
    return HEADERS_REDDIT if "reddit.com" in url else HEADERS


def pauza_dlya(url):
    for host, sek in PAUZA.items():
        if host in url:
            return sek
    return PAUZA_PO_UMOLCHANIYU

KEYWORDS = ["found", "location", "map", "collectible", "hidden", "secret",
            "stash", "wyman", "coordinate", "x:", "y:", "guide", "easter egg"]


# ── ВСПОМОГАТЕЛЬНОЕ ─────────────────────────────────────────

def load_seen_posts():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Не смог прочитать {LOG_FILE}: {e}. Начинаю с пустого списка.")
            return []
    return []


def save_seen_posts(seen_list):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_list[-5000:], f)


def extract_image_from_html(raw):
    match = re.search(r'<img[^>]+src="([^">]+)"', raw or "")
    return match.group(1) if match else ""


def html_to_text(raw):
    """Выкидывает теги и разворачивает &amp; обратно в &.

    Модель должна читать текст поста, а не разметку. Раньше в неё
    уходил HTML целиком: это жгло лимиты и ломало сверку цитаты
    с оригиналом, потому что после очистки тег <p> превращался
    в букву p."""
    if not raw:
        return ""
    text = re.sub(r"<script.*?</script>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:4000]


YT_ID = re.compile(r'(?:"externalId"|"channelId")\s*:\s*"(UC[\w-]{22})"|channel/(UC[\w-]{22})')


def youtube_id_po_imeni(imya):
    """Находит настоящий номер канала (UC…) по его @имени.
    Номера в ленте иногда устаревают или вписаны с ошибкой — тогда
    лента отвечает 404/500, а страница канала по @имени работает."""
    try:
        r = requests.get("https://www.youtube.com/" + imya, headers=HEADERS, timeout=20,
                         cookies={"CONSENT": "YES+1"})
        if r.status_code != 200:
            return None
        m = YT_ID.search(r.text)
        return (m.group(1) or m.group(2)) if m else None
    except Exception:
        return None


def fetch_feed(source, popytok=3):
    feed, oshibka = _fetch_feed(source, popytok)
    if oshibka and source.get("yt"):
        nomer = youtube_id_po_imeni(source["yt"])
        if nomer:
            novyy = dict(source, url="https://www.youtube.com/feeds/videos.xml?channel_id=" + nomer)
            feed2, oshibka2 = _fetch_feed(novyy, popytok)
            if not oshibka2:
                print(f"    {source['yt']}: номер канала {nomer} — поправь его в SOURCES")
                OTCHET["zametki"].append(f"{source['name']}: рабочий номер канала {nomer} (найден по {source['yt']})")
                return feed2, None
            return None, f"{oshibka}; по {source['yt']} найден {nomer}, но лента тоже не ответила: {oshibka2}"
        return None, f"{oshibka}; канал {source['yt']} не нашёлся"
    return feed, oshibka


def _fetch_feed(source, popytok=3):
    """Возвращает (feed, oshibka). feed = None, если не получилось.

    Про повторы. Reddit отвечает 429 «слишком часто», когда к нему
    приходят подряд. Это временный отказ, а не поломка: первый
    сабреддит читается, а следующие два получают от ворот поворот.
    Раньше бот на этом сдавался и терял два источника из трёх.
    Теперь он ждёт и пробует ещё раз, с каждым разом дольше."""

    url = source["url"]
    posledniy_otvet = None

    for popytka in range(popytok):
        try:
            r = requests.get(url, headers=zagolovki_dlya(url), timeout=20)
        except Exception as e:
            return None, f"сбой связи: {e}"

        # Временный отказ — подождём и попробуем снова
        if r.status_code in (429, 503) and popytka < popytok - 1:
            zhdat = 20 * (popytka + 1)
            print(f"    ответ {r.status_code} (просит подождать), "
                  f"пауза {zhdat} c, попытка {popytka + 2} из {popytok}")
            time.sleep(zhdat)
            posledniy_otvet = r.status_code
            continue

        posledniy_otvet = r.status_code
        break

    if posledniy_otvet != 200:
        if posledniy_otvet == 404:
            podskazka = " — такого канала или сабреддита не существует"
        elif posledniy_otvet == 429:
            podskazka = (" — сайт просит обращаться реже. Это временно, "
                         "не поломка. Если повторяется каждый день — "
                         "увеличь паузу в блоке PAUZA")
        elif posledniy_otvet == 403:
            podskazka = " — сайт закрыл доступ серверам GitHub"
        else:
            podskazka = ""
        return None, f"ответ {posledniy_otvet}{podskazka}"

    feed = feedparser.parse(r.content)
    if not feed.entries:
        return None, "ответ 200, но записей ноль — проверь адрес ленты"

    return feed, None


# ── РЕЖИМ: ПРОВЕРКА ─────────────────────────────────────────

def rezhim_proverka():
    print("=" * 64)
    print("ПРОВЕРКА ИСТОЧНИКОВ")
    print("Ни ИИ, ни Таблица не задействованы. Ничего не меняется.")
    print("=" * 64)

    zhivyh = 0

    for source in SOURCES + NEWS_SOURCES:
        print(f"\n{source['name']}")
        feed, oshibka = fetch_feed(source)

        if oshibka:
            print(f"  ✗ {oshibka}")
            print(f"  {source['url']}")
        else:
            zhivyh += 1
            svezhiy = feed.entries[0]
            zagolovok = getattr(svezhiy, "title", "без заголовка")
            data = getattr(svezhiy, "published", getattr(svezhiy, "updated", "дата неизвестна"))
            print(f"  ✓ записей {len(feed.entries)}")
            print(f"  свежее: {zagolovok[:70]}")
            print(f"  когда:  {data}")

        time.sleep(pauza_dlya(source["url"]))

    print("\n" + "=" * 64)
    vsego = len(SOURCES) + len(NEWS_SOURCES)
    print(f"ИТОГ: живых источников {zhivyh} из {vsego}")
    print(f"Модели ИИ по порядку: {', '.join(GROQ_MODELS)}")
    if zhivyh < vsego:
        print("Строки с ✗ надо поправить или удалить в блоке SOURCES в bot.py.")
    print("=" * 64)


# ── РЕЖИМ: ТЕСТ ─────────────────────────────────────────────

def rezhim_test():
    print("=" * 64)
    print("ТЕСТ СВЯЗИ С ТАБЛИЦЕЙ")
    print("=" * 64)

    metka = str(int(time.time()))
    proba = [{
        "category": "ТЕСТ",
        "name": "ТЕСТОВАЯ СТРОКА — можно удалить",
        "hint": "Это не находка. Проверка связи бота с Таблицей.",
        "region": "",
        "coord_x": "",
        "coord_y": "",
        "evidence": f"Отправлено ботом, метка {metka}",
        "source_url": f"https://collectpin.com/TEST-{metka}",
        "image_url": "",
    }]

    ok = send_to_sheet(proba)
    if ok:
        print("\nГотово. Открой лист sbor_ai — там должна появиться строка")
        print(f"с пометкой ТЕСТ и меткой {metka}. Её можно удалить.")
        print("Связь бота с Таблицей работает.")
    else:
        print("\nСвязь не работает. Разбор причины — в строках выше.")

    print("\n" + "=" * 64)
    print("ТЕСТ СВЯЗИ С ЛИСТОМ gazeta")
    print("=" * 64)
    if not NEWS_WEBHOOK_URL:
        print("NEWS_WEBHOOK_URL не задан — новостной вебхук ещё не подключён.")
        print("Это не ошибка, если газету пока не настраивали.")
    else:
        test_news = [{
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "title_ru": "ТЕСТ — можно удалить", "text_ru": f"Проверка связи, метка {metka}",
            "title_en": "TEST — delete me", "text_en": f"Connection check, tag {metka}",
            "source": f"https://collectpin.com/TEST-NEWS-{metka}", "tag": "", "hype": 0,
            "note": "Тестовая строка от бота",
        }]
        if send_news(test_news):
            print("\nГотово. В листе gazeta появилась строка «ТЕСТ — можно удалить».")
            print("В status она пустая, поэтому на сайт не попадёт. Удали её.")
        else:
            ok = False
            print("\nНовостной вебхук не работает. Разбор — в строках выше.")
    if not ok:
        sys.exit(1)


# ── ИИ ──────────────────────────────────────────────────────

def verify_evidence(evidence, original_text):
    """ДЕТЕКТОР ЛЖИ: проверяет, не выдумал ли ИИ цитату."""
    if not evidence:
        return ""
    clean_evidence = re.sub(r"\W+", "", evidence).lower()
    clean_original = re.sub(r"\W+", "", original_text).lower()

    if clean_evidence and clean_evidence not in clean_original:
        return f"⚠️ [ВЫДУМАНО ИИ?] {evidence}"
    return evidence


MERTVYE_MODELI = set()
START = time.time()
# GitHub обрывает запуск на 10-й минуте (timeout-minutes в run_bot.yml).
# Раньше бот ждал Groq по кругу и не успевал записать отчёт — запуск
# 2 ноября был отменён. Теперь после 7 минут ИИ больше не спрашиваем.
II_DEDLAYN = 7 * 60


def vybrat_modeli(client):
    """Спрашивает у Groq, какие модели сейчас есть, и строит порядок:
    сначала наши любимые из GROQ_MODELS, затем любые другие текстовые."""
    global GROQ_MODELS
    try:
        est = [m.id for m in client.models.list().data]
    except Exception as e:
        print(f"Список моделей Groq не получен ({str(e)[:120]}), беру свой.")
        return
    lishnie = ("whisper", "guard", "tts", "playai", "orpheus", "prompt", "distil", "compound", "allam")
    tekstovye = [m for m in est if not any(x in m.lower() for x in lishnie)]
    nashi = [m for m in GROQ_MODELS if m in est]
    drugie = sorted(m for m in tekstovye if m not in nashi)
    ubrany = [m for m in GROQ_MODELS if m not in est]
    if ubrany:
        OTCHET["zametki"].append("Groq больше не даёт: " + ", ".join(ubrany))
    GROQ_MODELS = (nashi + drugie)[:5] or GROQ_MODELS
    print("Модели ИИ по порядку: " + ", ".join(GROQ_MODELS))
II_STOP = False   # все модели упёрлись в лимит — до конца запуска не трогаем ИИ


def sprosit_ii(client, prompt):
    """Спрашивает модели по очереди. Возвращает dict или None."""
    global II_STOP
    if II_STOP:
        return None
    if time.time() - START > II_DEDLAYN:
        II_STOP = True
        OTCHET["zametki"].append("время запуска вышло — остальное в следующий раз")
        return None
    limit_u_vseh = True
    for model in GROQ_MODELS:
        if model in MERTVYE_MODELI:
            continue
        try:
            otvet = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            data = json.loads(otvet.choices[0].message.content.strip())
            OTCHET["ii_ok"] += 1
            OTCHET["model"] = model
            return data
        except Exception as e:
            tekst = str(e).lower()
            if any(k in tekst for k in ["decommission", "model_not_found", "does not exist", "not found", "unsupported"]):
                print(f"  Модель {model} недоступна: {str(e)[:160]}. Беру следующую.")
                MERTVYE_MODELI.add(model)
                OTCHET["zametki"].append(f"модель {model} недоступна")
                continue
            if "429" in tekst or "rate limit" in tekst or "rate_limit" in tekst:
                print(f"  Модель {model} упёрлась в лимит. Беру следующую.")
                MERTVYE_MODELI.add(model)
                continue
            limit_u_vseh = False
            print(f"  Ошибка ИИ ({model}): {str(e)[:200]}")
            OTCHET["ii_oshibki"] += 1
            return None
    if limit_u_vseh:
        II_STOP = True
        OTCHET["zametki"].append("все модели упёрлись в лимит Groq")
        print("  Все модели недоступны или упёрлись в лимит — ИИ до конца запуска не трогаю.")
    OTCHET["ii_oshibki"] += 1
    return None


def pravilo_daty(post):
    """Правило про дату для подсказки модели."""
    kogda = post.get("published")
    if kogda and kogda < RELIZ:
        return (f"Пост опубликован {kogda.strftime('%d.%m.%Y')}, ДО выхода GTA 6 "
                "(19 ноября 2026). Находок внутри GTA 6 в нём быть не может — "
                "ставь \"is_candidate\": false.")
    if not kogda and datetime.now(timezone.utc) < RELIZ:
        return ("GTA 6 выходит 19 ноября 2026 года и ещё НЕ ВЫШЛА, поэтому "
                "находок внутри GTA 6 существовать не может — ставь \"is_candidate\": false.")
    return ("GTA 6 уже вышла (19 ноября 2026), находки в ней возможны. "
            "Но пост должен описывать конкретный предмет или место в игре.")


def analyze_with_ai(client, post):
    """Возвращает: dict (кандидат), False (мусор), None (ошибка сервера)"""
    prompt = f"""
    Ты — строгий аналитик данных для интерактивной карты GTA 6 (Grand Theft Auto VI).
    Прочитай этот текст и определи, является ли это РЕАЛЬНОЙ находкой ИМЕННО В GTA 6.

    Откуда пост: {post.get('istochnik', 'неизвестно')}
    Про что этот источник: {post.get('pro', 'неизвестно')}

    Заголовок: {post['title']}
    Текст: {post['text']}

    ГЛАВНОЕ ПРАВИЛО, ПРОВЕРЬ ЕГО ПЕРВЫМ:
    {pravilo_daty(post)}
    Если находка относится к GTA 5, GTA Online, GTA San Andreas, RDR 2
    или любой другой игре — это НЕ наша находка, ставь "is_candidate": false.
    Названия вроде Chiliad, Los Santos, Blaine County, Michael, Franklin,
    Trevor, Cluckin Bell, Kortz Center — это GTA 5. Vice City, Leonida,
    Port Gellhorn, Ambrosia, Jason, Lucia — это GTA 6.
    Сомневаешься, какая игра — ставь "is_candidate": false.

    ОСТАЛЬНЫЕ ПРАВИЛА:
    1. Если это слух, новость, мем, обсуждение, трейлер — "is_candidate": false.
    2. НИКОГДА не выдумывай координаты! Если X и Y не написаны в тексте прямо, оставляй поля пустыми ("").
    3. Отвечай СТРОГО в формате JSON:
    {{
        "is_candidate": true или false,
        "game": "точное название игры, о которой пост: GTA 6 / GTA 5 / RDR 2 / другое / непонятно",
        "evidence": "ТОЧНАЯ ЦИТАТА из текста на оригинальном языке, доказывающая находку (копируй слово в слово!)",
        "category": "тайник / транспорт / секрет / коллекционка",
        "name": "Название предмета",
        "region": "Регион (если указан, иначе пусто)",
        "coord_x": "Координата X (ТОЛЬКО если есть в тексте, иначе пусто)",
        "coord_y": "Координата Y (ТОЛЬКО если есть в тексте, иначе пусто)"
    }}
    """
    try:
        data = sprosit_ii(client, prompt)
        if data is None:
            return None

        if not data.get("is_candidate"):
            return False

        # Вторая линия обороны: не полагаемся на то, что модель
        # правильно поняла правило. 17 августа она пропустила восемь
        # находок из GTA 5 с сабреддита r/chiliadmystery — все реальные,
        # но не из той игры. Проверяем поле game сами.
        igra = str(data.get("game", "")).lower()
        nasha = any(k in igra for k in ["gta 6", "gta6", "gta vi", "gtavi", "grand theft auto vi"])
        if not nasha:
            print(f"  Отбраковано: пост про «{data.get('game', '?')}», а не про GTA 6.")
            return False

        full_text = post["title"] + " " + post["text"]
        data["evidence"] = verify_evidence(data.get("evidence", ""), full_text)
        data.pop("game", None)          # в лист не уходит, колонки не меняются
        data["source_url"] = post["link"]
        data["image_url"] = post["image_url"]
        return data

    except Exception as e:
        print(f"  Ошибка разбора ответа ИИ: {e}")
        return None


# ── ОТПРАВКА ────────────────────────────────────────────────

def send_to_sheet(ai_results):
    """Отправляет находки в Таблицу.
    Возвращает True только если Таблица подтвердила запись.

    Здесь была главная ошибка прошлой версии. requests.post не
    считает ошибкой ответ 401 или 500 — исключения не будет.
    Поэтому проверяем три вещи подряд: что запрос дошёл, что код
    ответа 200, и что внутри написано success. Если хоть что-то
    не так — возвращаем False, и бот НЕ пометит посты прочитанными."""

    if not WEBHOOK_URL:
        print("WEBHOOK_URL не задан — отправлять некуда.")
        return False
    if not WEBHOOK_KEY:
        print("WEBHOOK_KEY не задан. Таблица отклонит запрос без пароля.")
        print("Добавь секрет: Settings → Secrets and variables → Actions.")
        return False

    try:
        r = requests.post(WEBHOOK_URL,
                          json={"key": WEBHOOK_KEY, "items": ai_results},
                          timeout=60)
    except Exception as e:
        print(f"Вебхук недоступен: {e}")
        return False

    if r.status_code != 200:
        print(f"Таблица ответила кодом {r.status_code}. Находки НЕ записаны.")
        print(f"Ответ: {r.text[:300]}")
        return False

    try:
        otvet = r.json()
    except Exception:
        print("Таблица ответила не JSON. Обычно это значит, что вебхук")
        print("развёрнут с доступом «только для меня» вместо «для всех»,")
        print("или после правки скрипта не сделали Deploy → New version.")
        print(f"Ответ: {r.text[:300]}")
        return False

    if otvet.get("status") != "success":
        print(f"Таблица отказалась записывать: {otvet}")
        if "пароль" in str(otvet):
            print("Пароль в Apps Script и секрет WEBHOOK_KEY на GitHub — разные.")
        return False

    print(f"Таблица приняла. Добавлено: {otvet.get('dobavleno')}, "
          f"пропущено (повторы и без источника): {otvet.get('propuscheno')}")
    return True


def send_news(items):
    """Отправляет черновики новостей в лист gazeta. True — если записаны."""
    if not NEWS_WEBHOOK_URL:
        print("NEWS_WEBHOOK_URL не задан — новости отправлять некуда.")
        return False
    if not NEWS_WEBHOOK_KEY:
        print("NEWS_WEBHOOK_KEY не задан. Добавь секрет на GitHub.")
        return False
    try:
        r = requests.post(NEWS_WEBHOOK_URL,
                          json={"key": NEWS_WEBHOOK_KEY, "items": items},
                          timeout=60)
    except Exception as e:
        print(f"Новостной вебхук недоступен: {e}")
        return False
    if r.status_code != 200:
        print(f"Лист gazeta ответил кодом {r.status_code}. Ответ: {r.text[:300]}")
        return False
    try:
        otvet = r.json()
    except Exception:
        print("Новостной вебхук ответил не JSON. Проверь, что развёрнут")
        print("с доступом «Все» и после правки сделан Deploy → New version.")
        print(f"Ответ: {r.text[:300]}")
        return False
    if otvet.get("status") != "success":
        print(f"Лист gazeta отказался записывать: {otvet}")
        return False
    print(f"Лист gazeta принял. Добавлено: {otvet.get('dobavleno')}, "
          f"пропущено (повторы): {otvet.get('propuscheno')}")
    return True


# ── НОВОСТИ ДЛЯ ГАЗЕТЫ ──────────────────────────────────────

NEWS_LOG = "news_seen.json"   # заголовки уже предложенных новостей


def chisla(tekst):
    return set(re.findall(r"\d+", tekst or ""))


def analyze_news(client, post):
    """dict — черновик новости, False — неинтересно, None — ошибка ИИ."""
    prompt = f"""
    Ты — редактор игровой газеты про GTA 6 (Grand Theft Auto VI) для игроков.
    Сегодня {datetime.now(timezone.utc).strftime('%d.%m.%Y')}. Релиз GTA 6 — 19 ноября 2026.
    Реши, стоит ли этот материал показать игрокам как НОВОСТЬ.

    Заголовок: {post['title']}
    Описание: {post['text'][:600]}
    Издание: {post.get('izdanie', '')}

    НОВОСТЬ — это свежий факт: официальное заявление Rockstar или Take-Two,
    трейлер, дата, цена, предзагрузка, новые детали игры или геймплея,
    крупная утечка, перенос, рекорд, громкое событие вокруг игры.
    НЕ новость: мнения, «10 вещей, которые мы хотим», догадки, теории фанатов,
    гайды, обзоры старых новостей, материалы про другие игры.

    hype — насколько это взорвёт сообщество игроков, от 1 до 10.
    Официальный трейлер или дата — 9–10. Новые детали геймплея — 7–8.
    Крупная утечка — 7–8. Мелкая подробность или пересказ — 3–5.

    kind: "официально" — если это сказала или показала Rockstar/Take-Two,
    "утечка" — слив, "слух" — неподтверждённое, "другое" — всё прочее.

    ПРАВИЛА ТЕКСТА:
    - Пиши ТОЛЬКО то, что есть в заголовке и описании. Ничего не добавляй.
      Никаких цифр, имён и дат, которых нет в тексте выше.
    - title_ru и title_en — до 60 знаков, живо, без кликбейта и капслока.
    - text_ru и text_en — 1–2 коротких предложения, до 200 знаков.
    - Русский — простой разговорный, на «ты» не обращайся.

    Отвечай СТРОГО JSON:
    {{
      "is_news": true или false,
      "hype": число 1–10,
      "kind": "официально / утечка / слух / другое",
      "title_ru": "...", "text_ru": "...",
      "title_en": "...", "text_en": "..."
    }}
    """
    data = sprosit_ii(client, prompt)
    if data is None:
        return None
    if not data.get("is_news"):
        return False
    try:
        hype = int(data.get("hype", 0))
    except Exception:
        hype = 0
    if hype < NEWS_POROG:
        return False

    # Детектор выдумки: числа в тексте модели должны быть в исходнике
    ishodnik = chisla(post["title"] + " " + post["text"])
    lishnie = (chisla(data.get("text_ru", "")) | chisla(data.get("text_en", "")) |
               chisla(data.get("title_ru", "")) | chisla(data.get("title_en", ""))) - ishodnik - {"6"}
    zametka = post.get("izdanie", "")
    if lishnie:
        zametka = f"⚠️ числа {', '.join(sorted(lishnie))} не из заголовка — проверь. " + zametka

    kind = str(data.get("kind", "")).lower()
    return {
        "kind": kind,
        "date": (post.get("published") or datetime.now(timezone.utc)).strftime("%Y-%m-%d"),
        "title_ru": str(data.get("title_ru", ""))[:90],
        "text_ru": str(data.get("text_ru", ""))[:260],
        "title_en": str(data.get("title_en", ""))[:90],
        "text_en": str(data.get("text_en", ""))[:260],
        "source": post["link"],
        "tag": "слух" if ("утеч" in kind or "слух" in kind) else "",
        "hype": hype,
        "note": zametka.strip(),
    }


def avto_reshenie(r, k, uzhe):
    """(True/False, почему) — можно ли показать новость на сайте без проверки."""
    izd = (k.get("izdanie") or "").strip()
    nizd = izd.lower()
    segodnya = "__avto__" + datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if "⚠️" in r.get("note", ""):
        return False, "в тексте есть числа не из заголовка"
    if r["hype"] < NEWS_AVTO_POROG:
        return False, f"громкость {r['hype']} — ниже {NEWS_AVTO_POROG}"
    if uzhe.count(segodnya) >= NEWS_AVTO_V_DEN:
        return False, f"сегодня уже {NEWS_AVTO_V_DEN} новости ушли автоматически"
    nadezhno = any(n in nizd for n in NADEZHNYE)
    ohvat = k.get("ohvat", 1)
    if nadezhno:
        return True, f"{izd or 'издание'} — в списке надёжных"
    if ohvat >= 2:
        return True, f"ту же новость дали {ohvat} издания"
    return False, f"{izd or 'издание'} нет в списке надёжных, и пишет только оно"


def pohozhe(a, b):
    a = re.sub(r"[^a-zа-яё0-9 ]", "", (a or "").lower())
    b = re.sub(r"[^a-zа-яё0-9 ]", "", (b or "").lower())
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.6


def sobrat_novosti(client, seen_posts, obrabotano):
    """Читает новостные ленты, отбирает громкое. Возвращает черновики."""
    if not NEWS_WEBHOOK_URL:
        print("\nНовости: вебхук не подключён (NEWS_WEBHOOK_URL), пропускаю.")
        return []

    try:
        with open(NEWS_LOG, "r", encoding="utf-8") as f:
            uzhe = json.load(f)
    except Exception:
        uzhe = []

    print("\n" + "=" * 64)
    print("НОВОСТИ")
    print("=" * 64)
    kandidaty = []
    for source in NEWS_SOURCES:
        feed, oshibka = fetch_feed(source)
        if oshibka:
            print(f"  ✗ {source['name']}: {oshibka}")
            OTCHET["istochniki"].append((source["name"], False, oshibka))
            time.sleep(pauza_dlya(source["url"]))
            continue
        novyh = 0
        for entry in feed.entries:
            pid = "news:" + str(getattr(entry, "id", entry.link))
            if pid in seen_posts:
                continue
            zag = getattr(entry, "title", "")
            if not any(s in zag.lower() for s in NEWS_SLOVA):
                obrabotano.append(pid)
                continue
            p = getattr(entry, "published_parsed", None)
            kandidaty.append({
                "id": pid, "title": zag,
                "text": html_to_text(getattr(entry, "summary", "")),
                "link": entry.link,
                "izdanie": getattr(getattr(entry, "source", None), "title", "") if hasattr(entry, "source") else "",
                "published": datetime(*p[:6], tzinfo=timezone.utc) if p else None,
            })
            novyh += 1
        print(f"  ✓ {source['name']}: записей {len(feed.entries)}, новых про GTA 6 {novyh}")
        OTCHET["istochniki"].append((source["name"], True, f"{len(feed.entries)} записей"))
        time.sleep(pauza_dlya(source["url"]))

    # Сначала свежие. Похожие заголовки (одна новость в десяти изданиях)
    # отсекаем ещё до ИИ, чтобы не тратить лимит.
    kandidaty.sort(key=lambda x: x["published"] or datetime.now(timezone.utc), reverse=True)
    for k in kandidaty:
        k["ohvat"] = len({(o.get("izdanie") or o["link"]).lower() for o in kandidaty if pohozhe(k["title"], o["title"])})
    otobrany, ostalos = [], []
    for k in kandidaty:
        if any(pohozhe(k["title"], z) for z in uzhe[-400:]) or any(pohozhe(k["title"], o["title"]) for o in otobrany):
            obrabotano.append(k["id"])
            continue
        (otobrany if len(otobrany) < NEWS_ZA_RAZ else ostalos).append(k)
    # Не влезшие в лимит не помечаем — заберём в следующий запуск.
    print(f"Новостей на проверку ИИ: {len(otobrany)} (отложено до следующего раза: {len(ostalos)})")

    gotovo = []
    for k in otobrany:
        print(f"Новость? {k['title'][:80]}")
        r = analyze_news(client, k)
        if r is None:
            print("  Откладываем до следующего запуска.")
            continue
        obrabotano.append(k["id"])
        uzhe.append(k["title"])
        if r is False:
            print("  Тихая или не новость.")
        else:
            if any(pohozhe(r["title_en"], g["title_en"]) for g in gotovo):
                print("  Повтор уже отобранной.")
                continue
            avto, pochemu = avto_reshenie(r, k, uzhe)
            r["status"] = "да" if avto else ""
            r["note"] = (("авто: " if avto else "проверь: ") + pochemu + ". " + r["note"]).strip()
            if avto:
                uzhe.append("__avto__" + datetime.now(timezone.utc).strftime("%Y-%m-%d"))
                OTCHET["avto"] = OTCHET.get("avto", 0) + 1
            print(f"  📰 Громкость {r['hype']}: {r['title_ru']} — {'на сайт сразу' if avto else 'ждёт тебя'} ({pochemu})")
            gotovo.append(r)
            uzhe.append(r["title_en"])
        time.sleep(2)

    with open(NEWS_LOG, "w", encoding="utf-8") as f:
        json.dump(uzhe[-1000:], f, ensure_ascii=False)
    return gotovo


# ── ОТЧЁТ В README.md ───────────────────────────────────────

def zapisat_otchet(rezhim):
    msk = datetime.now(timezone.utc) + timedelta(hours=3)
    stroki = [
        "# CollectPin — ИИ-скаут",
        "",
        "Этот файл бот переписывает после каждого запуска. Здесь видно, жив ли он.",
        "",
        f"**Последний запуск:** {msk.strftime('%d.%m.%Y %H:%M')} по Москве, режим `{rezhim}`",
        "",
        "| Источник | | |",
        "|---|---|---|",
    ]
    for imya, ok, chto in OTCHET["istochniki"]:
        stroki.append(f"| {imya} | {'✓' if ok else '✗'} | {chto} |")
    stroki += [
        "",
        f"- ИИ ответил: **{OTCHET['ii_ok']}** раз, ошибок: **{OTCHET['ii_oshibki']}**"
        + (f", модель `{OTCHET['model']}`" if OTCHET["model"] else ""),
        f"- Находок отправлено в `sbor_ai`: **{OTCHET['nahodki']}**",
        f"- Новостей отправлено в `gazeta`: **{OTCHET['novosti']}**"
        + (f", из них сразу на сайт: **{OTCHET.get('avto', 0)}**" if OTCHET['novosti'] else ""),
    ]
    for z in OTCHET["zametki"]:
        stroki.append(f"- ⚠️ {z}")
    stroki += ["", "Строка с ✗ — источник не ответил. Если ✗ держится несколько дней подряд,",
               "запусти режим `proverka` и пришли его вывод."]
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(stroki) + "\n")


# ── РЕЖИМ: ОБЫЧНЫЙ ──────────────────────────────────────────

def rezhim_obychnyy(tolko_novosti=False):
    if not GROQ_API_KEY:
        print("GROQ_API_KEY не задан. Без него анализировать нечем.")
        sys.exit(1)

    # max_retries=0: на лимите Groq сам ждал минутами, и запуск не укладывался в 10 минут
    client = Groq(api_key=GROQ_API_KEY, max_retries=0, timeout=40)
    vybrat_modeli(client)

    seen_list  = load_seen_posts()
    seen_posts = set(seen_list)

    print("=" * 64)
    print("ИСТОЧНИКИ")
    print("=" * 64)

    posts = []
    for source in ([] if tolko_novosti else SOURCES):
        feed, oshibka = fetch_feed(source)

        if oshibka:
            print(f"  ✗ {source['name']}: {oshibka}")
            OTCHET["istochniki"].append((source["name"], False, oshibka))
            time.sleep(pauza_dlya(source["url"]))
            continue

        novyh = 0
        for entry in feed.entries:
            post_id = getattr(entry, "yt_videoid", getattr(entry, "id", entry.link))
            if post_id in seen_posts:
                continue
            syroy = getattr(entry, "summary", "")
            p = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
            posts.append({
                "published": datetime(*p[:6], tzinfo=timezone.utc) if p else None,
                "id": post_id,
                "title": entry.title,
                "text": html_to_text(syroy),
                "image_url": extract_image_from_html(syroy),
                "link": entry.link,
                "istochnik": source["name"],
                "pro": source.get("pro", ""),
            })
            novyh += 1

        print(f"  ✓ {source['name']}: записей {len(feed.entries)}, новых {novyh}")
        OTCHET["istochniki"].append((source["name"], True, f"{len(feed.entries)} записей, новых {novyh}"))
        time.sleep(pauza_dlya(source["url"]))

    print("=" * 64)
    print(f"Новых постов для проверки: {len(posts)}")

    ai_results = []
    obrabotano = []

    do_reliza = datetime.now(timezone.utc) < RELIZ
    if do_reliza and posts:
        print("До релиза находок в GTA 6 не бывает — посты помечаю прочитанными без ИИ,")
        print("весь лимит ИИ отдаю новостям.")
        obrabotano.extend(p["id"] for p in posts)
        OTCHET["zametki"].append(f"до релиза: {len(posts)} постов пропущено без ИИ (находок ещё быть не может)")
        posts = []

    for post in posts:
        content = (post["title"] + " " + post["text"]).lower()

        if not any(word in content for word in KEYWORDS):
            obrabotano.append(post["id"])
            continue

        print(f"Анализируем: {post['title'][:80]}")
        result = analyze_with_ai(client, post)

        if result is None:
            # Ошибка ИИ. Пост НЕ помечаем прочитанным — вернёмся завтра.
            print("  Откладываем пост до следующего запуска.")
            continue

        if result is False:
            print("  Отбраковано ИИ (мусор или слух).")
            obrabotano.append(post["id"])
        else:
            print(f"  ✅ Кандидат: {result.get('name', 'без названия')}")
            ai_results.append(result)
            obrabotano.append(post["id"])

        time.sleep(2)

    if ai_results:
        print(f"\nОтправляю кандидатов: {len(ai_results)}")
        if not send_to_sheet(ai_results):
            print("Отправка не удалась. Список прочитанного НЕ сохраняю,")
            print("чтобы находки не потерялись — попробуем в следующий раз.")
            OTCHET["zametki"].append("Таблица не приняла находки — повтор в следующий запуск")
            zapisat_otchet("obychnyy")
            sys.exit(1)
        OTCHET["nahodki"] = len(ai_results)
    else:
        print("\nКандидатов нет — отправлять нечего.")

    # Новости: их сбой не мешает находкам, поэтому отдельно
    news_obrabotano = []
    try:
        novosti = sobrat_novosti(client, seen_posts, news_obrabotano)
    except Exception as e:
        print(f"Новости упали: {e}")
        OTCHET["zametki"].append(f"сбор новостей упал: {str(e)[:120]}")
        novosti, news_obrabotano = [], []
    if novosti:
        print(f"\nОтправляю новостей в gazeta: {len(novosti)}")
        if send_news(novosti):
            OTCHET["novosti"] = len(novosti)
            obrabotano.extend(news_obrabotano)
        else:
            OTCHET["zametki"].append("лист gazeta не принял новости — повтор в следующий запуск")
    else:
        obrabotano.extend(news_obrabotano)

    seen_list.extend(obrabotano)
    save_seen_posts(seen_list)
    zapisat_otchet("novosti" if tolko_novosti else "obychnyy")
    print(f"Готово. В списке прочитанного: {len(seen_list[-5000:])}")

    # Запуск краснеет, если сломалось важное — GitHub пришлёт письмо
    zhivyh = sum(1 for _, ok, _ in OTCHET["istochniki"] if ok)
    if OTCHET["istochniki"] and zhivyh == 0:
        print("\nОШИБКА: не ответил ни один источник.")
        sys.exit(1)
    if OTCHET["ii_ok"] == 0 and OTCHET["ii_oshibki"] > 0:
        print("\nОШИБКА: ИИ не ответил ни разу. Проверь ключ GROQ_API_KEY и список моделей.")
        sys.exit(1)


# ── ЗАПУСК ──────────────────────────────────────────────────

def main():
    rezhim = os.environ.get("MODE", "").strip().lower()
    if not rezhim and len(sys.argv) > 1:
        rezhim = sys.argv[1].strip().lower()
    if not rezhim:
        rezhim = "obychnyy"

    print(f"Режим: {rezhim}\n")

    if rezhim == "proverka":
        rezhim_proverka()
    elif rezhim == "test":
        rezhim_test()
    elif rezhim == "obychnyy":
        rezhim_obychnyy()
    elif rezhim == "novosti":
        rezhim_obychnyy(tolko_novosti=True)
    else:
        print(f"Неизвестный режим: {rezhim}")
        print("Бывают: obychnyy, novosti, proverka, test")
        sys.exit(1)


if __name__ == "__main__":
    main()
