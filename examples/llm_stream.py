"""LLM-style answer that is typed out live by editing one message (PATCH)."""
import asyncio
import os

from margybot import Bot, Dispatcher, F, Message

bot = Bot(os.environ["MARGY_KEY"], proxy=os.getenv("PROXY"))
dp = Dispatcher()


async def fake_llm(prompt: str):
    for word in f"Это потоковый ответ на «{prompt}». Каждое слово приходит отдельно.".split():
        await asyncio.sleep(0.3)
        yield word + " "


@dp.message(F.text, ~F.command)
async def ask(m: Message):
    await m.bot.stream_message(m.chat_id, fake_llm(m.text), interval=2.0)


asyncio.run(dp.start_polling(bot, offset_file="offset.txt"))
