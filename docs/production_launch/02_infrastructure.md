# Infrastructure Plan

## Objective

Production uchun ishonchli runtime, storage, and deployment surface tayyorlash.

## Tasks

- Production PostgreSQL instance ajratish.
- Production Redis instance ajratish.
- Application env values deployment platform orqali berish.
- Secrets manager yoki secure env store ishlatish.
- `APP_SECRET_KEY`, `ADMIN_API_TOKEN`, `DB_PASSWORD` majburiyligini tekshirish.
- `api_host`, `api_port`, `health` routes, and startup config productionga moslash.
- Logs uchun structured and centralized output yoqish.
- Session storage uchun restricted persistent volume ishlatish.

## Exit criteria

- App production environmentda restartdan keyin barqaror ishga tushadi.
- `/health/live` va `/health/ready` deployment health check sifatida ishlaydi.
- Infra credentials va secrets source hujjatlangan.

