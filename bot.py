import os
import json
import re
import requests
import feedparser
import time
from groq import Groq

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
LOG_FILE = "seen_posts.json"

client = Groq(api_key=GROQ_API_KEY)

def load_seen_posts():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            return json.load(f)
    return []

def save_seen_posts(seen_list):
    with open(LOG_FILE, 'w') as f:
        json.dump(seen_list[-1000:], f)

def extract_image_from_html(html_content):
    match = re.search(r'<img[^>]+src="([^">]+)"', html_content)
    return match.group(1) if match else ""

def fetch_all_sources(seen_posts):
    urls = [
        "https://www.reddit.com/r/GTA6+GTA6_Mapping/new.rss?limit=50",
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC_nK_i3kOyyA_K3sA_4WnQg",
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCWBA1-H9A5IldSb3JC5Tq1w",
        "https://news.google.com/rss/search?q=GTA+6+location+OR+found+OR+map&hl=en-US&gl=US&ceid=US:en"
    ]
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) CollectPin-Scout/6.0'}
    posts = []
    
    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                feed = feedparser.parse(response.content)
                for entry in feed.entries:
                    post_id = getattr(entry, 'yt_videoid', getattr(entry, 'id', entry.link))
                    if post_id in seen_posts:
                        continue 
                    posts.append({
                        "id": post_id,
                        "title": entry.title,
                        "text": getattr(entry, 'summary', ''),
                        "image_url": extract_image_from_html(getattr(entry, 'summary', '')),
                        "link": entry.link
                    })
        except Exception as e:
            print(f"Ошибка RSS ({url}): {e}")
        time.sleep(2) 
            
    return posts

def verify_evidence(evidence, original_text):
    """ДЕТЕКТОР ЛЖИ: Проверяет, не выдумал ли ИИ цитату"""
    if not evidence:
        return ""
    # Убираем пробелы и пунктуацию для жесткой проверки совпадения
    clean_evidence = re.sub(r'\W+', '', evidence).lower()
    clean_original = re.sub(r'\W+', '', original_text).lower()
    
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
            response_format={"type": "json_object"} 
        )
        result_text = chat_completion.choices[0].message.content.strip()
        data = json.loads(result_text)
        
        if not data.get("is_candidate"):
            return False # ИИ успешно понял, что это мусор
            
        # Запускаем детектор лжи
        full_text = post['title'] + " " + post['text']
        data['evidence'] = verify_evidence(data.get('evidence', ''), full_text)
        
        data['source_url'] = post['link']
        data['image_url'] = post['image_url']
        return data
    except Exception as e:
        print(f"Ошибка ИИ (Сервер упал или лимиты): {e}")
        return None # Возвращаем None, чтобы скрипт понял, что произошла ОШИБКА

def main():
    seen_posts = load_seen_posts()
    posts = fetch_all_sources(seen_posts)

    print(f"Новых постов для проверки: {len(posts)}")
    
    keywords = ['found', 'location', 'map', 'collectible', 'hidden', 'secret', 'stash', 'wyman', 'coordinate', 'x:', 'y:', 'guide', 'easter egg']
    ai_results = []
    
    # Сюда мы будем добавлять ID постов только при УСПЕШНОЙ обработке
    successfully_processed_ids = []
    
    for post in posts:
        content = (post['title'] + " " + post['text']).lower()
        
        if any(word in content for word in keywords):
            print(f"Анализируем: {post['title']}")
            result = analyze_with_ai(post)
            
            if result is None:
                # КРИТИЧЕСКАЯ ЗАЩИТА: ИИ выдал ошибку (упал сервер). 
                # Мы НЕ добавляем пост в прочитанные. Бот попробует снова через 15 минут.
                print("Произошла ошибка ИИ. Откладываем пост до следующего запуска.")
                continue
                
            elif result is False:
                print("ИИ отбраковал пост (мусор/слух).")
                successfully_processed_ids.append(post['id'])
                
            else:
                print(f"✅ Кандидат найден: {result['name']}")
                ai_results.append(result)
                successfully_processed_ids.append(post['id'])
                
            time.sleep(2) 
        else:
            # Пост отбракован по ключевым словам (мусор)
            successfully_processed_ids.append(post['id'])
            
    if ai_results:
        try:
            requests.post(WEBHOOK_URL, json=ai_results)
            print(f"Отправлено {len(ai_results)} кандидатов в таблицу.")
        except Exception as e:
            print(f"Ошибка вебхука: {e}")
            # Если Гугл Таблица упала, мы не сохраняем successfully_processed_ids,
            # чтобы при следующем запуске отправить эти точки заново!
            return
            
    # Сохраняем логи ТОЛЬКО для тех постов, которые прошли успешно
    seen_posts.extend(successfully_processed_ids)
    save_seen_posts(seen_posts)

if __name__ == "__main__":
    main()
