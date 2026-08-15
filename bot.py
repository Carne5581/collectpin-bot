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
    # Храним только последние 500 ID, чтобы файл не пух
    with open(LOG_FILE, 'w') as f:
        json.dump(seen_list[-500:], f)

def extract_image_from_html(html_content):
    """Вытаскивает ссылку на картинку из поста Reddit"""
    match = re.search(r'<img[^>]+src="([^">]+)"', html_content)
    return match.group(1) if match else ""

def fetch_reddit_posts(seen_posts):
    url = "https://www.reddit.com/r/GTA6+GTA6_Mapping/new.rss?limit=100"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) CollectPin-Scout/3.0'}
    
    posts = []
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            feed = feedparser.parse(response.content)
            for entry in feed.entries:
                post_id = entry.id
                if post_id in seen_posts:
                    continue # Пропускаем то, что уже видели
                
                posts.append({
                    "id": post_id,
                    "title": entry.title,
                    "text": getattr(entry, 'summary', ''),
                    "image_url": extract_image_from_html(getattr(entry, 'summary', '')),
                    "link": entry.link
                })
    except Exception as e:
        print(f"Ошибка RSS: {e}")
            
    return posts

def analyze_with_ai(post):
    prompt = f"""
    Ты — строгий аналитик данных для интерактивной карты GTA 6. 
    Прочитай этот пост с Reddit. Ищи ТОЛЬКО реальные находки (коллекционки, секреты, транспорт, пасхалки).
    
    Заголовок: {post['title']}
    Текст: {post['text']}
    
    ПРАВИЛА:
    1. Если это слух, мем, вопрос, обсуждение трейлера — ответь ТОЛЬКО словом FALSE.
    2. Если это РЕАЛЬНАЯ находка, найди координаты (обычно в формате X, Y или [X,Y]).
    3. Ответь СТРОГО в формате JSON без лишних слов:
    {{
        "category": "тайник / транспорт / секрет / коллекционка",
        "name": "Название предмета",
        "region": "Регион (если указан, иначе Unknown)",
        "coord_x": "Координата X (если есть, иначе пусто)",
        "coord_y": "Координата Y (если есть, иначе пусто)"
    }}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            # ИСПОЛЬЗУЕМ АКТУАЛЬНУЮ УМНУЮ МОДЕЛЬ GROQ
            model="llama-3.1-70b-versatile", 
            temperature=0.0,
        )
        
        result_text = chat_completion.choices[0].message.content.strip()
        
        if "FALSE" in result_text.upper() and "{" not in result_text:
            return None
            
        # Надежный парсинг JSON через Regex
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if not json_match:
            return None
            
        data = json.loads(json_match.group(0))
        data['source_url'] = post['link']
        data['image_url'] = post['image_url']
        return data
    except Exception as e:
        print(f"Ошибка ИИ: {e}")
        return None

def main():
    seen_posts = load_seen_posts()
    posts = fetch_reddit_posts(seen_posts)
    print(f"Новых постов для проверки: {len(posts)}")
    
    keywords = ['found', 'location', 'map', 'collectible', 'hidden', 'secret', 'stash', 'wyman', 'coordinate', 'x:', 'y:']
    ai_results = []
    new_seen_ids = []
    
    for post in posts:
        new_seen_ids.append(post['id'])
        content = (post['title'] + " " + post['text']).lower()
        
        if any(word in content for word in keywords):
            print(f"Анализируем: {post['title']}")
            result = analyze_with_ai(post)
            
            if result:
                print(f"✅ Найдено: {result['name']}")
                ai_results.append(result)
            time.sleep(1) # Пауза для лимитов API
            
    if ai_results:
        try:
            requests.post(WEBHOOK_URL, json=ai_results)
            print(f"Отправлено {len(ai_results)} точек в таблицу.")
        except Exception as e:
            print(f"Ошибка вебхука: {e}")
            
    # Сохраняем логи, чтобы не проверять эти посты снова
    seen_posts.extend(new_seen_ids)
    save_seen_posts(seen_posts)

if __name__ == "__main__":
    main()
