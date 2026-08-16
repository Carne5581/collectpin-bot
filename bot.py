import os
import sys
import json
import re
import html
import requests
import feedparser
import time
from groq import Groq

# ==================================================================
# CollectPin — ИИ-скаут
#
# У бота три режима. Переключаются кнопкой Run workflow на GitHub,
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
#   test      — отправляет в Таблицу одну выдуманную находку
#               с пометкой ТЕСТ. Нужен, чтобы убедиться, что
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
# ==================================================================


GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL")
WEBHOOK_KEY  = os.environ.get("WEBHOOK_KEY")
LOG_FILE     = "seen_posts.json"


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

SOURCES = [
    {
        "name": "Reddit · r/GTA6",
        "url": "https://www.reddit.com/r/GTA6/new.rss?limit=50",
    },
    {
        "name": "Reddit · r/GTAVI",
        "url": "https://www.reddit.com/r/GTAVI/new.rss?limit=50",
    },
    {
        # Охотники за секретами GTA 5. После релиза этот народ
        # первым полезет искать тайники в GTA 6.
        "name": "Reddit · r/chiliadmystery",
        "url": "https://www.reddit.com/r/chiliadmystery/new.rss?limit=50",
    },
    {
        # GTA Series Videos — самый авторитетный канал по GTA.
        # Адрес сверен: UCuWcjpKbIDAbZfHoru1toFg
        "name": "YouTube · GTA Series Videos",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCuWcjpKbIDAbZfHoru1toFg",
    },
    {
        # PowerPyx — трофейные гайды, выходят в первые дни.
        # Адрес сверен: UCWBA1-H9A5IldSb3tNwQmtQ
        # В старой версии стояло UCWBA1-H9A5IldSb3JC5Tq1w — 404.
        "name": "YouTube · PowerPyx",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCWBA1-H9A5IldSb3tNwQmtQ",
    },
    {
        # TGG — гайды по GTA.
        # Адрес сверен: UC72PuhDwKtZ5MikpGNhPAtA
        "name": "YouTube · TGG",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC72PuhDwKtZ5MikpGNhPAtA",
    },
    {
        "name": "Google Новости · GTA 6 location/found/map",
        "url": "https://news.google.com/rss/search?q=GTA+6+location+OR+found+OR+map&hl=en-US&gl=US&ceid=US:en",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CollectPin-Scout/7.0"
}

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
        json.dump(seen_list[-2000:], f)


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


def fetch_feed(source):
    """Возвращает (feed, oshibka). feed = None, если не получилось."""
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
    except Exception as e:
        return None, f"сбой связи: {e}"

    if r.status_code != 200:
        podskazka = ""
        if r.status_code == 404:
            podskazka = " — такого канала или сабреддита не существует"
        elif r.status_code in (403, 429):
            podskazka = " — сайт блокирует запросы с серверов GitHub"
        return None, f"ответ {r.status_code}{podskazka}"

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

    for source in SOURCES:
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

        time.sleep(2)

    print("\n" + "=" * 64)
    print(f"ИТОГ: живых источников {zhivyh} из {len(SOURCES)}")
    if zhivyh < len(SOURCES):
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

    if send_to_sheet(proba):
        print("\nГотово. Открой лист sbor_ai — там должна появиться строка")
        print(f"с пометкой ТЕСТ и меткой {metka}. Её можно удалить.")
        print("Связь бота с Таблицей работает.")
    else:
        print("\nСвязь не работает. Разбор причины — в строках выше.")
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


def analyze_with_ai(client, post):
    """Возвращает: dict (кандидат), False (мусор), None (ошибка сервера)"""
    prompt = f"""
    Ты — строгий аналитик данных для интерактивной карты GTA 6.
    Прочитай этот текст. Твоя задача — определить, является ли это РЕАЛЬНОЙ находкой в игре.

    Заголовок: {post['title']}
    Текст: {post['text']}

    ПРАВИЛА:
    1. Если это слух, новость, мем, обсуждение — установи "is_candidate": false.
    2. НИКОГДА не выдумывай координаты! Если X и Y не написаны в тексте прямо, оставляй поля пустыми ("").
    3. Отвечай СТРОГО в формате JSON:
    {{
        "is_candidate": true или false,
        "evidence": "ТОЧНАЯ ЦИТАТА из текста на оригинальном языке, доказывающая находку (копируй слово в слово!)",
        "category": "тайник / транспорт / секрет / коллекционка",
        "name": "Название предмета",
        "region": "Регион (если указан, иначе пусто)",
        "coord_x": "Координата X (ТОЛЬКО если есть в тексте, иначе пусто)",
        "coord_y": "Координата Y (ТОЛЬКО если есть в тексте, иначе пусто)"
    }}
    """
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = json.loads(chat_completion.choices[0].message.content.strip())

        if not data.get("is_candidate"):
            return False

        full_text = post["title"] + " " + post["text"]
        data["evidence"] = verify_evidence(data.get("evidence", ""), full_text)
        data["source_url"] = post["link"]
        data["image_url"] = post["image_url"]
        return data

    except Exception as e:
        print(f"  Ошибка ИИ (сервер упал или лимиты): {e}")
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


# ── РЕЖИМ: ОБЫЧНЫЙ ──────────────────────────────────────────

def rezhim_obychnyy():
    if not GROQ_API_KEY:
        print("GROQ_API_KEY не задан. Без него анализировать нечем.")
        sys.exit(1)

    client = Groq(api_key=GROQ_API_KEY)

    seen_list  = load_seen_posts()
    seen_posts = set(seen_list)

    print("=" * 64)
    print("ИСТОЧНИКИ")
    print("=" * 64)

    posts = []
    for source in SOURCES:
        feed, oshibka = fetch_feed(source)

        if oshibka:
            print(f"  ✗ {source['name']}: {oshibka}")
            time.sleep(2)
            continue

        novyh = 0
        for entry in feed.entries:
            post_id = getattr(entry, "yt_videoid", getattr(entry, "id", entry.link))
            if post_id in seen_posts:
                continue
            syroy = getattr(entry, "summary", "")
            posts.append({
                "id": post_id,
                "title": entry.title,
                "text": html_to_text(syroy),
                "image_url": extract_image_from_html(syroy),
                "link": entry.link,
            })
            novyh += 1

        print(f"  ✓ {source['name']}: записей {len(feed.entries)}, новых {novyh}")
        time.sleep(2)

    print("=" * 64)
    print(f"Новых постов для проверки: {len(posts)}")

    if not posts:
        print("Проверять нечего. Выходим, ничего не меняя.")
        print("Это нормально: в лентах пока нет постов, которых бот не видел.")
        return

    ai_results = []
    obrabotano = []

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
            sys.exit(1)
    else:
        print("\nКандидатов нет — отправлять нечего.")

    seen_list.extend(obrabotano)
    save_seen_posts(seen_list)
    print(f"Готово. В списке прочитанного: {len(seen_list[-2000:])}")


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
    else:
        print(f"Неизвестный режим: {rezhim}")
        print("Бывают: obychnyy, proverka, test")
        sys.exit(1)


if __name__ == "__main__":
    main()
