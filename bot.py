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
    # Храним только последние 1000 ID
    with open(LOG_FILE, 'w') as f:
        json.dump(seen_list[-1000:], f)

def extract_image_from_html(html_content):
    match = re.search(r'<img[^>]+src="([^">]+)"', html_content)
    return match.group(1) if match else ""

def fetch_all_sources(seen_posts):
    """Качает данные с Reddit, YouTube и Google News"""
    urls = [
        "https://www.reddit.com/r/GTA6+GTA6_Mapping/new.rss?limit=50",
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC_nK_i3kOyyA_K3sA_4WnQg",
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCWBA1-H9A5IldSb3JC5Tq1w",
        "https://news.google.com/rss/search?q=GTA+6+location+OR+found+OR+map&hl=en-US&gl=US&ceid=US:en"
    ]
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) CollectPin-Scout/4.0'}
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

def analyze_with_ai(post):
    prompt = f"""
    Ты — строгий аналитик данных для интерактивной карты GTA 6. 
    Прочитай этот текст. Ищи ТОЛЬКО реальные находки (коллекционки, секреты, транспорт, пасхалки).
    
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
            # ВЕРНУЛИ МОДЕЛЬ, КОТОРАЯ ЖЕЛЕЗНО РАБОТАЕТ
            model="llama-3.1-8b-instant", 
            temperature=0.0,
        )
        result_text = chat_completion.choices[0].message.content.strip()
        
        if "FALSE" in result_text.upper() and "{" not in result_text:
            return None
            
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
    posts = fetch_all_sources(seen_posts)
    
    # === ИСКУССТВЕННЫЙ ПОСТ ===
    test_post = {
        "id": "TEST_POST_003",
        "title": "I found a hidden stash in Port Gellhorn!",
        "text": "Hey guys, coordinates are X: 1540, Y: 200. It's right under the old bridge.",
        "image_url": "https://example.com/test.jpg",
        "link": "https://collectpin.com/test"
    }
    if test_post["id"] not in seen_posts:
        posts.append(test_post)
    # ==========================

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
            time.sleep(2) 
            
    if ai_results:
        try:
            requests.post(WEBHOOK_URL, json=ai_results)
            print(f"Отправлено {len(ai_results)} точек в таблицу.")
        except Exception as e:
            print(f"Ошибка вебхука: {e}")
            
    seen_posts.extend(new_seen_ids)
    save_seen_posts(seen_posts)

if __name__ == "__main__":
    main()
