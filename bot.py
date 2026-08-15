import os
import json
import requests
import google.generativeai as genai
import time

# Забираем секретные ключи из настроек GitHub
GEMINI_API_KEY = os.environ.get("AQ.Ab8RN6LamAuPrNE5GnQe8hX8toSzvWGalwC2liTplsWPFsdsjg")
WEBHOOK_URL = os.environ.get("https://script.google.com/macros/s/AKfycbz8xMXfAgus8PeTn62j3TkKDwTGi3BZWctCybM_LMhG0cb-nyhiJJX4l82mB--1f6t9/exec")

# Настраиваем ИИ
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') # Быстрая и бесплатная модель

def fetch_reddit_posts():
    """Собирает свежие посты с Reddit без API-ключей, просто по JSON"""
    urls = [
        "https://www.reddit.com/r/GTA6/new.json?limit=20",
        "https://www.reddit.com/r/GTA6_Mapping/new.json?limit=10"
    ]
    headers = {'User-Agent': 'CollectPin-Scanner/1.0'}
    posts = []
    
    for url in urls:
        try:
            response = requests.get(url, headers=headers)
            data = response.json()
            for child in data['data']['children']:
                title = child['data'].get('title', '')
                text = child['data'].get('selftext', '')
                link = "https://www.reddit.com" + child['data'].get('permalink', '')
                posts.append({"title": title, "text": text, "link": link})
        except Exception as e:
            print(f"Ошибка парсинга Reddit: {e}")
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
        response = model.generate_content(prompt)
        result_text = response.text.strip()
        
        # Убираем возможные маркеры Markdown, которые ИИ иногда добавляет
        if result_text.startswith("```json"):
            result_text = result_text[7:-3]
            
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
    
    # Ключевые слова для первичной фильтрации, чтобы не гонять ИИ впустую
    keywords = ['found', 'location', 'map', 'collectible', 'hidden', 'secret', 'stash', 'wyman', 'нашел', 'тайник', 'секрет']
    
    ai_results = []
    
    for post in posts:
        content = (post['title'] + " " + post['text']).lower()
        if any(word in content for word in keywords):
            print(f"Анализируем подозрительный пост: {post['title']}")
            result = analyze_with_ai(post)
            if result:
                ai_results.append(result)
            time.sleep(2) # Пауза, чтобы не превысить лимиты API Gemini
            
    if ai_results:
        send_to_google_sheets(ai_results)
        print(f"Успешно обработано и отправлено {len(ai_results)} точек.")
    else:
        print("Ничего ценного не найдено.")

if __name__ == "__main__":
    main()