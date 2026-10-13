# Отчёты об ошибках → Telegram: развёртывание relay

Программа шлёт обезличенные отчёты на адрес из `ERROR_REPORT_URL` в `app.py`.
Этот адрес — маленький Cloudflare Worker (`relay_worker.js`). Он проверяет отчёт и пересылает его тебе в Telegram.
Токен бота хранится только в настройках воркера. В репозиторий его не класть.

## 1. Бот в Telegram
1. Открой [@BotFather](https://t.me/BotFather) → `/newbot` → придумай имя и username (должен заканчиваться на `bot`).
2. Скопируй токен вида `123456789:AA...`. Никому его не показывай.

## 2. Узнать chat_id
1. Напиши своему новому боту любое сообщение (например «привет»).
2. Открой в браузере `https://api.telegram.org/bot<ТОКЕН>/getUpdates`.
3. Найди `"chat":{"id":123456789,...}` — это число и есть `chat_id`.

## 3. Воркер
1. Зарегистрируйся на [dash.cloudflare.com](https://dash.cloudflare.com) (бесплатного тарифа хватает).
2. **Workers & Pages → Create → Create Worker**, имя например `sheqel-relay` → **Deploy**.
3. **Edit code** → удали шаблон, вставь целиком содержимое `relay_worker.js` → **Deploy**.

## 4. Переменные
**Settings → Variables and Secrets → Add**:

| Имя | Тип | Значение |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Secret | токен из шага 1 |
| `TELEGRAM_CHAT_ID` | Secret | число из шага 2 |
| `PER_INSTALL_PER_HOUR` | Text, необязательно | лимит отчётов в час с одной установки (по умолчанию 10) |
| `GLOBAL_PER_DAY` | Text, необязательно | общий лимит в сутки (по умолчанию 300) |

## 5. Лимиты, которые переживают перезапуск (рекомендуется)
1. **Storage & Databases → KV → Create**, имя `sheqel-relay-rate`.
2. В воркере: **Settings → Bindings → Add → KV namespace**, имя переменной `RATE_KV`, выбери созданное хранилище.

Без KV лимиты тоже работают, но сбрасываются, когда Cloudflare перезапускает воркер.

## 6. Проверка
Адрес воркера виден в его карточке, вида `https://sheqel-relay.<аккаунт>.workers.dev`.

```bash
curl -X POST https://sheqel-relay.<аккаунт>.workers.dev \
  -H 'content-type: application/json' \
  -d '{"install_id":"00000000-0000-4000-8000-000000000000","app_version":"test","os":"macOS","python":"3.11","where":"app.test","exc_type":"TestError","message":"проверка связи","log_tail":[],"job_id":null,"time":"2026-01-01T00:00:00Z"}'
```

Ответ `ok` — сообщение должно прийти в Telegram.

## 7. Включить в программе
1. Впиши адрес воркера в `ERROR_REPORT_URL` в `app.py`.
2. Подними `APP_VERSION` и задеплой обычной процедурой.

При первом запуске новой версии пользователи увидят одно уведомление о том, что отправляется и как это отключить.

## Если адрес начнут спамить
Адрес воркера публичен: он лежит в открытом `app.py`. От спама защищают лимиты.
Если их не хватит, уменьши `GLOBAL_PER_DAY` или переименуй воркер и пропиши новый адрес в `app.py`.
