import asyncio
import logging
import os

from margybot import Bot, Button, Callback, Command, Comment, Dispatcher, F, Follow, Message
from margybot import APIError

logging.basicConfig(level=logging.INFO)

# socks5://user:pass@host:1080 | socks5h:// | socks4:// | http://host:3128 | https://...
# or a list of proxies for automatic fail-over
PROXY = os.getenv("PROXY")  # e.g. "socks5://127.0.0.1:9050"
bot = Bot(os.environ["MARGY_KEY"], proxy=PROXY)
dp = Dispatcher()


@dp.on_startup
async def startup(bot: Bot):
    await bot.set_commands([("start", "Начать"), ("help", "Что я умею")])
    print("I am", (await bot.me()).get("nick"))


@dp.message(Command("start"))
async def start(m: Message):
    await m.answer(
        f"Привет, {m.from_user.display}!",
        buttons=[[Button("Монетка", data="coin")]],
    )

@dp.message(Command("help"))
async def help_(m: Message):
    await m.answer("Напиши что угодно — я повторю.")


@dp.message(F.text, ~F.command)
async def echo(m: Message):
    await m.react("👍")
    await m.answer("Ты написал: " + m.text)


@dp.button(F.data == "coin")
async def coin(cb: Callback):
    await cb.edit_message("Орёл!")


@dp.comment()
async def on_comment(c: Comment):
    await c.reply("Спасибо за комментарий!")


@dp.error()
async def on_error(event, exc):
    if isinstance(exc, APIError):
        print("API said:", exc.message)
@dp.follow()
async def on_follow(f: Follow):
    await f.follow_back()


if __name__ == "__main__":
    # offset_file keeps the position between restarts
    asyncio.run(dp.start_polling(bot, offset_file="offset.txt"))
