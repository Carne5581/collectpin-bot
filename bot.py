import os
import json
import requests
import feedparser
import time
from groq import Groq

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

client = Groq(api_key=GROQ_API_KEY)

def fetch_reddit_posts():
    """Читает объединенную RSS-ленту за один запрос (нет ошибки 429)"""
    # Знак + объединяет сабреддиты в одну ленту!
    url = "https://www.reddit.com/r/GTA6+GTA6_Mapping/new.rss"
    
    headers = {
        'User-Agent': 'CollectPin-ScannerBot/2.0'
    }
    
    posts = []
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            feed = feedparser.parse(response.content)
            for entry in feed.entries[:15]: # Берем 15 самых свежих со всех сабреддитов
                posts.append({
                    "title": entry.title,
                    "text": getattr(entry, 'summary', ''),
                    "link": entry.link
                })
        else:
            print(f"Reddit вернул ошибку: {response.status_code}")
    except Exception as e:
        print(f"Ошибка парсинга RSS: {e}")
            
    return posts

def analyze_with_ai(post):
    """Жесткий промпт для ИИ, чтобы не пропускал мусор"""
    prompt = f"""
    Ты — строгий модератор базы данных для интерактивной карты GTA 6. 
    Прочитай этот пост. Твоя задача — найти ТОЛЬКО реальные находки в игре (коллекционные предметы, секреты, редкий транспорт, тайники).
    
    Заголовок: {post['title']}
    Текст: {post['text']}
    
    ПРАВИЛА:
    1. Если это слух, теория, новость, обсуждение трейлера, мем или мнение (например, "Тревор появится в игре" или "Скидки на PS5") — ТЫ ОБЯЗАН ОТВЕТИТЬ ТОЛЬКО СЛОВОМ FALSE.
    2. Не выдавай JSON, если это не конкретная находка на карте!
    3. Если это РЕАЛЬНАЯ находка, ответь СТРОГО в формате JSON:
    {{
        "category": "тайник, транспорт, секрет или коллекционка",
        "name": "Название предмета",
        "hint": "Как найти (ориентиры)",
        "region": "Регион"
    }}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant", 
            temperature=0.0, # 0.0 делает ИИ максимально строгим и логичным
        )
        
        result_text = chat_completion.choices[0].message.content.strip()
        
        if result_text.startswith("```json"):
            result_text = result_text[7:-3]
        elif result_text.startswith("```"):
            result_text = result_text[3:-3]
            
        if "FALSE" in result_text.upper() or not result_text.startswith("{"):
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
        requests.post(WEBHOOK_URL, json=ai_results)
        print(f"Отправлено в таблицу.")
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
                print(f"ИИ подтвердил точку: {result['name']}")
                ai_results.append(result)
            else:
                print("ИИ отбраковал пост (мусор/слух).")
                
            time.sleep(2) 
            
    if ai_results:
        send_to_google_sheets(ai_results)
        print(f"Успешно отправлено {len(ai_results)} точек.")
    else:
        print("Новых точек не найдено.")

if __name__ == "__main__":
    main()
