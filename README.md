# Бот мониторинга акций «Магнит»

Telegram-бот, отслеживающий акции в избранных магазинах «Магнит»
(до 10 магазинов на пользователя) и присылающий уведомления
с фото товара, названием, скидкой и адресом магазина.

## Запуск на ноутбуке (автономно, 24/7)

Бот зарегистрирован в планировщике задач Windows как задача **MagnitBot**:
- стартует **автоматически при входе в систему** (после включения ноутбука)
- работает **в фоне без окна консоли**, логи — в `data/bot.log`
- **перезапускается сам** при падении (супервизор `bot/run_forever.py`)
- пока бот запущен, ноутбук **не засыпает от бездействия** (не влияет на явный сон и закрытие крышки)

Управление (PowerShell):
```powershell
Start-ScheduledTask MagnitBot          # запустить сейчас
Stop-ScheduledTask  MagnitBot          # остановить задачу
Stop-Process -Name pythonw             # убить процессы бота
Get-Content data\bot.log -Tail 20 -Wait # смотреть логи
```

## Локальный запуск

```bash
pip install -r requirements.txt
# создать .env с BOT_TOKEN=... (токен от @BotFather)
python -m bot.main
```

## Деплой на Render (бесплатно, 24/7)

1. Зарегистрируйтесь на https://dashboard.render.com/register
2. New → Web Service → **Public Git Repository** → URL этого репозитория
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python -m bot.main`
5. План: **Free**
6. Environment Variables:
   - `BOT_TOKEN` — токен Telegram-бота
   - `HF_TOKEN` — токен Hugging Face (роль Write)
   - `HF_DB_REPO` — например, `username/magnit-bot-data`
7. Чтобы сервис не засыпал — HTTP-пинг каждые 5 минут (UptimeRobot)

База данных автоматически бэкапится в приватный HF-датасет каждые 10 минут,
при изменениях и при остановке сервиса; восстанавливается при старте.

## Структура

- `bot/main.py` — точка входа: polling + HTTP health-сервер (порт из `PORT`)
- `bot/handlers.py` — команды и меню бота
- `bot/monitor.py` — фоновый опрос акций
- `bot/magnit_api.py` — клиент API акций magnit.ru
- `bot/store_search.py` — поиск магазинов (геокодирование + stores-facade API)
- `bot/database.py` — SQLite (aiosqlite): пользователи, магазины, отправленные акции
- `bot/hf_storage.py` — бэкап/восстановление БД на Hugging Face
