import asyncio
import feedparser
import os
import logging
import aiohttp
import re
from telegram import Bot
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# === Загрузка переменных окружения ===
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME')
TOGETHER_API_KEY = os.getenv('TOGETHER_API_KEY')
CHECK_INTERVAL = 600  # 10 минут
ADMIN_ID = os.getenv('ADMIN_ID')

if not BOT_TOKEN or not CHANNEL_USERNAME or not TOGETHER_API_KEY:
    raise RuntimeError("❌ Не загружены переменные из .env")

RSS_FEEDS = [
    'https://www.inform.kz/ru/politics_rss.xml',
    'https://tengrinews.kz/rss',
    'https://lsm.kz/rss',
]

bot = Bot(token=BOT_TOKEN)

async def notify_admin(message: str):
    if ADMIN_ID:
        try:
            await bot.send_message(chat_id=int(ADMIN_ID), text=f"⚠️ Ошибка:\n{message}")
        except Exception as e:
            logging.error(f"❌ Не удалось отправить уведомление админу: {e}")

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s — %(message)s',
    handlers=[
        logging.FileHandler('log_kz.txt', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

def load_sent_links():
    if not os.path.exists("sent_links.txt"):
        return set()
    with open("sent_links.txt", "r", encoding="utf-8") as f:
        return set(line.strip() for line in f)

def save_sent_link(link):
    with open("sent_links.txt", "a", encoding="utf-8") as f:
        f.write(link + "\n")

sent_links = load_sent_links()

def extract_image(entry):
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'url' in media:
                return media['url']
    if 'enclosures' in entry and entry.enclosures:
        for enc in entry.enclosures:
            if 'type' in enc and 'image' in enc['type']:
                return enc['href']
    summary = entry.get('summary', '')
    img_match = re.search(r'<img[^>]+src="([^"]+)"', summary)
    if img_match:
        return img_match.group(1)
    return None

async def generate_ai_text(summary):
    headers = {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json"
    }
    prompt = (
        "Ты — редактор казахстанского Telegram-канала. "
        "Создай краткий и красиво оформленный пост на основе новости. "
        "Пиши только о событиях, произошедших в Казахстане. Игнорируй другие страны. "
        "Укажи дату и локацию, если они есть. Не вставляй ссылки. Стиль — новостной. В конце добавь хештег #Казахстан."
    )
    data = {
        "model": "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": summary}
        ],
        "temperature": 0.7,
        "max_tokens": 512
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.together.xyz/v1/chat/completions", headers=headers, json=data) as resp:
            result = await resp.json()
            return result['choices'][0]['message']['content'].strip()

async def download_image(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception as e:
        logging.warning(f"⚠️ Ошибка скачивания изображения: {e}")
    return None

async def fetch_and_send():
    logging.info("🔍 Проверка новостей...")

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    for url in RSS_FEEDS:
        logging.info(f"📡 Источник: {url}")
        feed = feedparser.parse(url)

        for entry in feed.entries[:5]:
            if entry.link in sent_links:
                continue

            published = entry.get('published_parsed')
            if published:
                published_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if published_dt < yesterday:
                    continue

            summary = entry.get('summary', '')
            if not any(k in summary.lower() for k in [
                "казахстан", "астана", "алматы", "шымкент", "караганда", "актау",
                "тараз", "петропавловск", "костанай", "кызылорда", "усть-каменогорск"
            ]):
                continue

            ai_text = await generate_ai_text(summary)
            image_url = extract_image(entry)
            photo_bytes = await download_image(image_url) if image_url else None

            try:
                if photo_bytes:
                    await bot.send_photo(
                        chat_id=CHANNEL_USERNAME,
                        photo=photo_bytes,
                        caption=ai_text,
                        parse_mode='HTML'
                    )
                else:
                    await bot.send_message(
                        chat_id=CHANNEL_USERNAME,
                        text=ai_text,
                        parse_mode='HTML',
                        disable_web_page_preview=True
                    )
                sent_links.add(entry.link)
                save_sent_link(entry.link)
            except Exception as e:
                logging.error(f"❌ Ошибка при отправке: {e}")

    logging.info(f"⏳ Пауза {CHECK_INTERVAL} сек...")

async def main_loop():
    while True:
        try:
            await fetch_and_send()
        except Exception as e:
            error_message = f"{type(e).__name__}: {str(e)}"
            logging.error(f"❗ Ошибка в fetch_and_send: {error_message}")
            await notify_admin(error_message)
        await asyncio.sleep(CHECK_INTERVAL)

# старый main_loop ниже был заменён

    while True:
        await fetch_and_send()
        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    asyncio.run(main_loop())
