import os
import json
import requests
import feedparser
from google import genai

# Забираем секретные ключи
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# Настраиваем ИИ (Новая библиотека 2026 года)
client = genai.Client(api_key=GEMINI_API_KEY)

def fetch_reddit_posts():
    """Читает RSS-ленту Reddit, чтобы обойти блокировку IP-адресов GitHub"""
    urls = [
        "https://www.reddit.com/r/GTA6/new.rss",
        "https://www.reddit.com/r/GTA6_Mapping/new.rss"
    ]
    
    # Притворяемся обычным браузером Windows
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    posts = []
    
    for url in urls:
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                # Парсим XML/RSS
                feed = feedparser.parse(response.content)
                for entry in feed.entries[:15]: # Берем 15 самых свежих
                    posts.append({
                        "title": entry.title,
                        "text": entry.summary,
                        "link": entry.link
                    })
            else:
                print(f"Reddit вернул ошибку: {response.status_code} для {url}")
        except Exception as e:
            print(f"Ошибка парсинга RSS: {e}")
            
    return posts

def analyze_with_ai(post):
    """Скармливает текст ИИ и просит выдать строгий JSON"""
    prompt = f"""
    Ты — аналитик данных для интерактивной карты GTA 6. 
    Прочитай этот пост с форума. Твоя задача — понять, идет ли в нем речь о нахождении коллекционного предмета, секрета, редкого транспорта или тайника.
    
    Заголовок: {post['title']}
    Текст: {post['text']}
    
    Если это НЕ про коллекционки (а про новости, графику, мнения) — ответь ровно одним словом: FALSE.
    Если это про коллекционку, ответь СТРОГО в формате JSON без лишнего текста:
    {{
        "category": "тайник, транспорт, секрет или коллекционка",
        "name": "Краткое название того, что нашли",
        "hint": "Краткое описание, где это (ориентиры из текста)",
        "region": "Регион (если понятен из текста, иначе пустая строка)"
    }}
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )
        result_text = response.text.strip()
        
        # Убираем возможные маркеры Markdown
        if result_text.startswith("```json"):
            result_text = result_text[7:-3]
        elif result_text.startswith("```"):
            result_text = result_text[3:-3]
            
        if result_text == "FALSE":
            return None
            
        data = json.loads(result_text)
        data['source_url'] = post['link']
        return data
    except Exception as e:
        print(f"Ошибка ИИ или парсинга JSON: {e}")
        return None

def send_to_google_sheets(ai_results):
    """Отправляет готовые результаты в твою Гугл Таблицу"""
    if not ai_results:
        return
    try:
        response = requests.post(WEBHOOK_URL, json=ai_results)
        print(f"Отправлено в таблицу. Ответ: {response.text}")
    except Exception as e:
        print(f"Ошибка отправки в таблицу: {e}")

def main():
    print("Начинаем сканирование...")
    posts = fetch_reddit_posts()
    print(f"Собрано постов для проверки: {len(posts)}")
    
    # Ключевые слова для первичной фильтрации
    keywords = ['found', 'location', 'map', 'collectible', 'hidden', 'secret', 'stash', 'wyman', 'нашел', 'тайник', 'секрет', 'here']
    
    ai_results = []
    
    for post in posts:
        content = (post['title'] + " " + post['text']).lower()
        if any(word in content for word in keywords):
            print(f"Анализируем пост: {post['title']}")
            result = analyze_with_ai(post)
            if result:
                print(f"ИИ нашел точку: {result['name']}")
                ai_results.append(result)
            
    if ai_results:
        send_to_google_sheets(ai_results)
        print(f"Успешно обработано и отправлено {len(ai_results)} точек.")
    else:
        print("Среди свежих постов ничего про точки на карте не найдено.")

if __name__ == "__main__":
    main()
