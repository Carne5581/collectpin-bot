import os
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
# ЧТО ИСПРАВЛЕНО ПО СРАВНЕНИЮ С ПРЕДЫДУЩЕЙ ВЕРСИЕЙ
#
# 1. ГЛАВНОЕ. Находки больше не теряются.
#    Было: requests.post(...) без проверки ответа. Беда в том, что
#    requests НЕ считает ошибкой ответ вида 401 или 500 — исключения
#    не будет. То есть если Таблица отвечала "неверный пароль" или
#    "ошибка", бот считал отправку успешной, помечал посты как
#    прочитанные и больше к ним не возвращался. Кандидаты пропадали
#    молча и навсегда. Теперь ответ проверяется по-настоящему.
#
# 2. Видно, какой источник не отвечает.
#    Было: "if response.status_code == 200" и молчание в остальных
#    случаях. Reddit часто отдаёт 403 серверам GitHub — бот при этом
#    работал "успешно", просто без единого поста с Reddit, и понять
#    это было нельзя. Теперь в журнал пишется, что ответил каждый
#    источник и сколько записей пришло.
#
# 3. Модель больше не читает HTML.
#    Было: в неё уходило поле summary как есть, вместе с тегами
#    <p>, <div>, <a href=...>. Это и тратило лимиты, и мешало
#    "детектору лжи": после очистки тег <p> превращался в букву p
#    и портил сравнение цитаты с оригиналом.
#
# 4. Пароль для Таблицы.
#    Отправка теперь идёт в виде {"key": ..., "items": [...]},
#    как ждёт новый скрипт Apps Script.
#
# 5. Проверка списка прочитанного стала мгновенной (set вместо list).
#
# ==================================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL")
WEBHOOK_KEY  = os.environ.get("WEBHOOK_KEY")   # НОВОЕ: пароль Таблицы
LOG_FILE     = "seen_posts.json"

client = Groq(api_key=GROQ_API_KEY)


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


def extract_image_from_html(html_content):
    match = re.search(r'<img[^>]+src="([^">]+)"', html_content or "")
    return match.group(1) if match else ""


def html_to_text(raw):
    """Выкидывает теги и разворачивает &amp; обратно в &.

    Нужно, чтобы модель читала текст поста, а не разметку.
    Раньше в неё уходил кусок HTML целиком: это и лимиты жгло,
    и ломало сверку цитаты с оригиналом."""
    if not raw:
        return ""
    text = re.sub(r"<script.*?</script>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:4000]      # длиннее модели не нужно и незачем платить


def fetch_all_sources(seen_posts):
    urls = [
        "https://www.reddit.com/r/GTA6+GTA6_Mapping/new.rss?limit=50",
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC_nK_i3kOyyA_K3sA_4WnQg",
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCWBA1-H9A5IldSb3JC5Tq1w",
        "https://news.google.com/rss/search?q=GTA+6+location+OR+found+OR+map&hl=en-US&gl=US&ceid=US:en",
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CollectPin-Scout/6.1"
    }
    posts = []

    print("=" * 60)
    print("ИСТОЧНИКИ")
    print("=" * 60)

    for url in urls:
        korotko = url.split("//")[-1][:70]
        try:
            response = requests.get(url, headers=headers, timeout=15)

            if response.status_code != 200:
                # Раньше это место молчало, и мёртвый источник
                # выглядел как источник без новостей.
                print(f"  ✗ {korotko}")
                print(f"    ответ {response.status_code} — записей ноль")
                if response.status_code in (403, 429):
                    print(f"    (этот сайт блокирует запросы с серверов GitHub)")
                continue

            feed = feedparser.parse(response.content)

            if not feed.entries:
                print(f"  ⚠ {korotko}")
                print(f"    ответ 200, но записей ноль — проверь адрес ленты")
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

            print(f"  ✓ {korotko}")
            print(f"    записей {len(feed.entries)}, из них новых {novyh}")

        except Exception as e:
            print(f"  ✗ {korotko}")
            print(f"    сбой: {e}")

        time.sleep(2)

    print("=" * 60)
    return posts


def verify_evidence(evidence, original_text):
    """ДЕТЕКТОР ЛЖИ: проверяет, не выдумал ли ИИ цитату."""
    if not evidence:
        return ""
    clean_evidence = re.sub(r"\W+", "", evidence).lower()
    clean_original = re.sub(r"\W+", "", original_text).lower()

    if clean_evidence and clean_evidence not in clean_original:
        return f"⚠️ [ВЫДУМАНО ИИ?] {evidence}"
    return evidence


def analyze_with_ai(post):
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
        result_text = chat_completion.choices[0].message.content.strip()
        data = json.loads(result_text)

        if not data.get("is_candidate"):
            return False

        full_text = post["title"] + " " + post["text"]
        data["evidence"] = verify_evidence(data.get("evidence", ""), full_text)

        data["source_url"] = post["link"]
        data["image_url"] = post["image_url"]
        return data

    except Exception as e:
        print(f"Ошибка ИИ (сервер упал или лимиты): {e}")
        return None


def send_to_sheet(ai_results):
    """Отправляет находки в Таблицу.

    Возвращает True только если Таблица подтвердила запись.

    Здесь была главная ошибка прошлой версии. requests.post не
    считает ошибкой ответ 401 или 500 — исключения не будет.
    Поэтому проверять надо три вещи по очереди:
      1. что запрос вообще дошёл (нет исключения),
      2. что код ответа 200,
      3. что внутри ответа написано "success", а не "error".
    Если хоть что-то не так — возвращаем False, и бот НЕ пометит
    посты прочитанными. Через сутки он попробует их снова."""

    if not WEBHOOK_URL:
        print("WEBHOOK_URL не задан — отправлять некуда.")
        return False

    if not WEBHOOK_KEY:
        print("WEBHOOK_KEY не задан. Таблица отклонит запрос без пароля.")
        return False

    posylka = {"key": WEBHOOK_KEY, "items": ai_results}

    try:
        r = requests.post(WEBHOOK_URL, json=posylka, timeout=60)
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
        print("развёрнут неправильно или требует входа в аккаунт Google.")
        print(f"Ответ: {r.text[:300]}")
        return False

    if otvet.get("status") != "success":
        print(f"Таблица отказалась записывать: {otvet}")
        return False

    print(f"Таблица приняла. Добавлено: {otvet.get('dobavleno')}, "
          f"пропущено: {otvet.get('propuscheno')}")
    return True


def main():
    seen_list = load_seen_posts()
    seen_posts = set(seen_list)          # проверка вхождения стала мгновенной

    posts = fetch_all_sources(seen_posts)
    print(f"Новых постов для проверки: {len(posts)}")

    if not posts:
        print("Проверять нечего. Выходим, ничего не меняя.")
        return

    keywords = ["found", "location", "map", "collectible", "hidden", "secret",
                "stash", "wyman", "coordinate", "x:", "y:", "guide", "easter egg"]

    ai_results = []
    successfully_processed_ids = []

    for post in posts:
        content = (post["title"] + " " + post["text"]).lower()

        if any(word in content for word in keywords):
            print(f"Анализируем: {post['title'][:80]}")
            result = analyze_with_ai(post)

            if result is None:
                # ИИ выдал ошибку. Пост НЕ помечаем прочитанным —
                # вернёмся к нему в следующий запуск.
                print("  Ошибка ИИ. Откладываем пост до следующего запуска.")
                continue

            elif result is False:
                print("  Отбраковано ИИ (мусор или слух).")
                successfully_processed_ids.append(post["id"])

            else:
                print(f"  ✅ Кандидат: {result.get('name', 'без названия')}")
                ai_results.append(result)
                successfully_processed_ids.append(post["id"])

            time.sleep(2)
        else:
            successfully_processed_ids.append(post["id"])

    # --- отправка ---
    if ai_results:
        print(f"\nОтправляю кандидатов: {len(ai_results)}")
        if not send_to_sheet(ai_results):
            print("Отправка не удалась. Список прочитанного НЕ сохраняю,")
            print("чтобы эти находки не потерялись — попробуем в следующий раз.")
            return
    else:
        print("\nКандидатов нет — отправлять нечего.")

    seen_list.extend(successfully_processed_ids)
    save_seen_posts(seen_list)
    print(f"Готово. В списке прочитанного: {len(seen_list[-2000:])}")


if __name__ == "__main__":
    main()
