import traceback
import asyncio
from threading import Thread
from telegram import Bot

from dotenv import load_dotenv
import os
load_dotenv()

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')

class TelegramChannelBot:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.bot = Bot(token=token)
        self.loop = asyncio.new_event_loop()
        self.thread = Thread(target=self._start_loop, daemon=True)
        self.thread.start()

    def _start_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _send(self, text):
        await self.bot.send_message(chat_id=self.chat_id, text=text)

    def send_message(self, text):
        asyncio.run_coroutine_threadsafe(self._send(text), self.loop)

if __name__ == "__main__":
    bot = TelegramChannelBot(TOKEN, CHANNEL_ID)

    async def main():
        await bot.send_message("Hello from your bot!")

    asyncio.run(main())
