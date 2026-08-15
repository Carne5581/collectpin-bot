import os
import json
import requests
import feedparser
import time
from groq import Groq

# Забираем секретные ключи
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# Настраиваем ИИ (Groq)
client = Groq(api_key=GROQ_API_KEY)

def fetch_reddit_posts():
    """Читает RSS-ленту Reddit с безопасными паузами"""
    urls = [
        "https://www.reddit.com/r/GTA6/new.rss",
        "https://www.reddit.com/r/GTA6_Mapping/new.rss"
    ]
    
    headers = {
        'User-Agent': 'CollectPin-ScannerBot/1.1'
    }
    
    posts = []
    
    for url in urls:
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                feed = feedparser.parse(response.content)
                for entry in feed.entries[:10]:
                    posts.append({
                        "title": entry.title,
                        "text": getattr(entry, 'summary', ''),
                        "link": entry.link
                    })
            else:
                print(f"Reddit вернул ошибку: {response.status_code} для {url}")
        except Exception as e:
            print(f"Ошибка парсинга RSS: {e}")
            
        # Увеличили паузу до 30 секунд, обходим жесткую защиту Reddit
        time.sleep(30) 
            
    return posts

def analyze_with_ai(post):
    """Скармливает текст в актуальную модель Groq (Llama 3.1)"""
    prompt = f"""
    Ты — аналитик данных для интерактивной карты GTA 6. 
    Прочитай этот пост. Идет ли в нем речь о нахождении коллекционного предмета, секрета, редкого транспорта или тайника?
    
    Заголовок: {post['title']}
    Текст: {post['text']}
    
    Если это НЕ про коллекционки (новости, слухи) — ответь ровно одним словом: FALSE.
    Если это про коллекционку, ответь СТРОГО в формате JSON:
    {{
        "category": "тайник, транспорт, секрет или коллекционка",
        "name": "Краткое название того, что нашли",
        "hint": "Краткое описание, где это",
        "region": "Регион (если понятен, иначе пустая строка)"
    }}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            # ИСПОЛЬЗУЕМ НОВУЮ АКТУАЛЬНУЮ МОДЕЛЬ
            model="llama-3.1-8b-instant", 
            temperature=0.1,
        )
        
        result_text = chat_completion.choices[0].message.content.strip()
        
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
        print(f"Ошибка ИИ: {e}")
        return None

def send_to_google_sheets(ai_results):
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
                
            time.sleep(2) 
            
    if ai_results:
        send_to_google_sheets(ai_results)
        print(f"Успешно обработано и отправлено {len(ai_results)} точек.")
    else:
        print("Среди свежих постов ничего про точки на карте не найдено.")

if __name__ == "__main__":
    main()
