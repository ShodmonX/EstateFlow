# EstateFlow Telegram Mini App

EstateFlow foydalanuvchi tajribasi endi ikki qatlamga bo'linadi:

- Telegram bot: notification delivery, referral link, kanal taklifi va yordam.
- Telegram Mini App: qidiruv, e'lonlar, filterlar, saqlangan filterlar va profil.

## Local run

1. `.env` ichida `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `APP_SECRET_KEY` va
   `TELEGRAM_MINI_APP_URL` qiymatlarini belgilang.
2. Backend va frontend image'larini build qiling:

```powershell
docker compose build estateflow-api bot frontend
```

3. Migration va servislarni ishga tushiring:

```powershell
docker compose run --rm migrate
docker compose up -d redis rabbitmq estateflow-api bot frontend
```

Local Mini App `http://127.0.0.1:8080` manzilida ochiladi. Telegram production
Mini App uchun HTTPS domen kerak.

## Authentication

Frontend Telegram WebApp `initData`ni `POST /webapp/auth`ga yuboradi. Backend
Telegram HMAC signature, user ID va `auth_date`ni tekshiradi. Keyingi so'rovlar
Bearer token bilan ishlaydi. Telegram user ID frontend headeridan qabul qilinmaydi.

## API contract

- `POST /webapp/auth`
- `GET /webapp/me`
- `GET /webapp/search/announcements`
- `GET /webapp/filters`
- `POST /webapp/filters`
- `PATCH /webapp/filters/{filter_id}`
- `DELETE /webapp/filters/{filter_id}`

## Telegram setup

- BotFather orqali Mini App URL'ni HTTPS domen sifatida belgilang.
- `TELEGRAM_MINI_APP_URL` shu URL bilan bir xil bo'lsin.
- Reverse proxy frontend domenidagi `/api/*` so'rovlarini backendga yuborsin.
- `APP_SECRET_KEY` productionda majburiy va tasodifiy uzun secret bo'lsin.

## Responsive acceptance

Frontend 320px dan boshlab ishlaydi. Mobile safe-area, touch controls, Telegram
Desktop va laptop kengliklari uchun alohida CSS breakpointlar mavjud. Backend
API xatolari loading, empty va retry holatlari bilan ko'rsatiladi.

## Current limitation

Notification history Mini App ichida hali alohida list sifatida ko'rsatilmaydi;
notificationning o'zi Telegram bot orqali keladi va Mini App linki bilan ochiladi.
