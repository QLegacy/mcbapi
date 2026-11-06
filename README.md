# margybot

Асинхронная библиотека для ботов [Margy Chat](https://chat.narez.xyz) в стиле aiogram.
Документация API: <https://chat.narez.xyz/docs/bots.html>

- long polling (`/bot/updates`) с сохранением offset между перезапусками
- **прокси**: `socks5`, `socks5h`, `socks4`, `socks4a`, `http`, `https`, логин/пароль в URL, список прокси с автопереключением
- роутеры, фильтры (`Command`, магический `F`), middleware, `on_startup/on_shutdown`, обработчики ошибок
- автоповтор при 429 / 5xx / Cloudflare-заглушке (403/503 не в JSON), сетевых сбоях
- события приходят объектами с методами: `m.answer()`, `cb.edit_message()`, `c.reply()`, `f.follow_back()`
- потоковый вывод нейросети через редактирование сообщения: `bot.stream_message()`

## Установка

```bash
pip install .            # из папки проекта (нужен Python 3.9+)
```

## Минимальный бот

```bash

python examples/echo.py
```

## Прокси

```python
Bot(key, proxy="socks5://127.0.0.1:1080")
Bot(key, proxy="socks5h://user:pass@host:1080")   # DNS на стороне прокси
Bot(key, proxy="socks4://host:1080")
Bot(key, proxy="http://user:pass@host:3128")
Bot(key, proxy="https://host:443")                # HTTP-прокси с CONNECT
Bot(key, proxy=["socks5://a:1080", "http://b:3128"])  # при сбое берётся следующий
```

Спецсимволы в логине/пароле кодируйте как в обычном URL (`@` → `%40`).
`https://` означает HTTP-прокси, к которому подключаются по обычному TCP, а HTTPS-трафик
идёт через туннель CONNECT (так работает почти любой «https-прокси»); TLS до самого
прокси не поддерживается. Пароли в логи не попадают.

## События и обработчики

| Декоратор | Событие | Объект | Что умеет |
|---|---|---|---|
| `@dp.message(...)` | написали в личку | `Message` | `text`, `command`, `command_args`, `media`, `from_user`, `answer()`, `react()` |
| `@dp.button(...)` | нажали кнопку | `Callback` | `data`, `answer()`, `edit_message()`, `delete_message()` |
| `@dp.comment(...)` | ответили под постом бота | `Comment` | `text`, `post_id`, `root_id`, `reply()` |
| `@dp.follow(...)` | подписались | `Follow` | `from_user`, `follow_back()` |
| `@dp.on("kind", ...)` | любое другое | `Event` | `raw` |

Фильтры:

```python
from margybot import F, Command

@dp.message(Command("start", "help"))
@dp.message(F.text == "привет")
@dp.message(F.text.startswith("/echo"))
@dp.message(F.text.regexp(r"^\d+$"))
@dp.message(F.media)                    # есть фото
@dp.message(F.text, ~F.command)         # обычный текст, не команда
@dp.button(F.data.in_({"yes", "no"}))
@dp.message(lambda m: m.from_user.nick == "anna")   # любая функция (в т.ч. async)
```

Обработчик может объявить `bot`, `dispatcher`, `event` и свои данные: `Dispatcher(db=db)` →
`async def h(m, db): ...`.

Роутеры, middleware, ошибки:

```python
from margybot import Router
router = Router(); dp.include_router(router)

@dp.middleware
async def timing(handler, event, data):
    return await handler(event, data)

@dp.error()
async def on_error(event, exc): ...

@dp.on_startup
async def up(bot): ...
```

События одного чата обрабатываются по очереди (порядок сохраняется), разных чатов — параллельно.

## Методы `Bot`

`me`, `set_commands`, `send_message`, `edit_message`, `delete_message`, `react`, `get_chats`,
`get_messages`, `stream_message`, `create_post` (ответы, опросы, markdown, цитаты, каналы),
`get_posts`, `get_post`, `delete_post`, `upload_photo`, `get_followers`, `follow`,
`get_updates`, и низкоуровневый `request(method, path, ...)` для всего остального.

Фото: в `media=[...]` можно передать id, а также `bytes` или `Path` — они загрузятся сами.

```python
from pathlib import Path
from margybot import Button, Poll

await bot.send_message(chat_id, "Смотри", media=[Path("cat.jpg")],
                       buttons=[[Button("Да", data="y"), Button("Нет", data="n")]])
await bot.create_post("Какой мессенджер лучше?", poll=Poll(["Телега", "Макс", "Марги чат"]))
await bot.create_post("**жирный** текст", markdown=True)
```

## Ошибки

`MargyError` → `NetworkError`, `APIError` → `BadRequest (400)`, `Unauthorized (401)`,
`Forbidden (403)`, `NotFound (404)`, `RateLimited (429, .retry_after)`, `ServiceUnavailable`.

## Что важно знать про сервис

- Первым в личку бот писать не может; только отвечать на `message`.
- Общий лимит 180 запросов/мин, посты 1 в 30 сек. Правки сообщений для стриминга делайте реже раза в пару секунд.
- События хранятся 7 дней — поэтому используйте `offset_file` (или `skip_updates=True`).
- Ключ держите только на своём сервере.
- Удалите эту соц сеть.

## Проверка

`python tests/smoke.py` поднимает фейковый сервер и локальные SOCKS5/HTTP-прокси и проверяет весь стек.
