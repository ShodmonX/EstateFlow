# EstateFlow Manual Test Checklist

Natijani har band uchun PASS, FAIL yoki N/A deb yozing.

## 0. Test xavfsizligi

- [ ] Alohida Telegram test user, test kanal va test notification chat tayyor.
- [ ] Production userlar uchun maxfiy ma'lumot test kanaliga yuborilmaydi.
- [ ] Haqiqiy notification yuborishdan oldin notification flag o'chirilgan.

## 1. Kod va konfiguratsiya

~~~powershell
Set-Location C:\Users\shodmon\projects\EstateFlow
docker compose config --quiet
.venv\Scripts\ruff.exe check src apps services scripts tests
.venv\Scripts\python.exe -m pytest -q
~~~



## 2. Image build va migration

~~~powershell
docker compose build estateflow-api admin-api internal-api bot frontend listener worker-pre-ai worker-ai worker-dedup worker-notification-matcher worker-notification
docker compose --profile migrations run --rm migrate
.venv\Scripts\python.exe scripts\migration_smoke.py
~~~

- [ ] Har bir servis o'z Dockerfile'idan build bo'ldi.
- [ ] worker-ai image'da boto3 bor.
- [ ] Migration muvaffaqiyatli ishladi.
- [ ] Migration qayta ishlatilganda duplicate yoki destructive error yo'q.

## 3. Infra va containerlar

~~~powershell
docker compose up -d
docker compose ps
docker compose events --since 10m
~~~

- [ ] postgres, rabbitmq va redis healthy.
- [ ] estateflow-api, admin-api, internal-api, bot, frontend, listener va barcha workerlar Up.
- [ ] Restart loop yo'q.
- [ ] Loglarda Traceback, ConnectionRefused yoki ModuleNotFoundError yo'q.

## 4. Health va dependency

~~~powershell
$urls = @(
  'http://127.0.0.1:8000/health/live',
  'http://127.0.0.1:8000/health/ready',
  'http://127.0.0.1:8001/health/live',
  'http://127.0.0.1:8002/health/live',
  'http://127.0.0.1:8080/',
  'http://127.0.0.1:8080/api/health/live'
)
foreach ($url in $urls) {
  $response = Invoke-WebRequest -UseBasicParsing -Uri $url
  "$url $($response.StatusCode)"
}
~~~

- [ ] Barcha yuqoridagi endpointlar 200.
- [ ] PostgreSQL to'xtatilganda ready 503, live esa ishlaydi.
- [ ] Redis to'xtatilganda ready 503.
- [ ] Dependencylar qayta ko'tarilgach ready yana 200.

## 5. RabbitMQ

~~~powershell
docker compose exec rabbitmq rabbitmq-diagnostics -q ping
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers
$env:RABBITMQ_SMOKE_URL = 'amqp://USER:PASSWORD@127.0.0.1:5672/'
.venv\Scripts\python.exe scripts\rabbitmq_smoke.py
Remove-Item Env:RABBITMQ_SMOKE_URL
$env:RABBITMQ_TEST_URL = 'amqp://USER:PASSWORD@127.0.0.1:5672/'
.venv\Scripts\python.exe -m pytest -q tests\test_rabbitmq_queue.py
Remove-Item Env:RABBITMQ_TEST_URL
~~~

- [ ] ingestion.raw_announcements mavjud.
- [ ] ai.processing.raw_announcements mavjud.
- [ ] dedup.post_ai.structured_announcements mavjud.
- [ ] announcement.persisted mavjud.
- [ ] notification.delivery mavjud.
- [ ] Har bir queue'da kerakli consumer bor.
- [ ] DLQ'lar normal testda bo'sh.
- [ ] Ack, retry va DLQ smoke-test o'tdi.
+

## 6. API va Mini App

- [ ] http://127.0.0.1:8000/docs ochiladi.
- [ ] http://127.0.0.1:8001/docs ochiladi.
- [ ] Public search bo'sh natijada valid JSON qaytaradi.
- [ ] Auth'siz protected endpoint 401 yoki 403 qaytaradi.
- [ ] Test Telegram user bilan POST /webapp/auth ishlaydi.
- [ ] GET /webapp/me ishlaydi.
- [ ] GET /webapp/search/announcements ishlaydi.
- [ ] /webapp/filters create, list, update va delete ishlaydi.
- [ ] Desktop browser, Chrome mobile emulation, Android va iPhone'da Mini App ochiladi.
- [ ] Refresh, back, slow network va API error holatlari to'g'ri ko'rinadi.
- [ ] HTTPS va mixed-content xatosi yo'q.

## 7. Bot access va flags

- [ ] Admin token'siz admin endpoint 401 yoki 403.
- [ ] Admin token bilan GET /admin/release/flags ishlaydi.
- [ ] bot_access=false bo'lganda yangi user /start bilan kira olmaydi.
- [ ] Test user allowlist'ga qo'shiladi:

~~~powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8001/admin/release/beta-allowlist -Headers @{ 'x-admin-token' = 'ADMIN_TOKEN' } -ContentType 'application/json' -Body '{"user_id":123456789}'
~~~

- [ ] Allowlist user /start bilan kira oladi.
- [ ] Allowlist'da yo'q user kira olmaydi.
- [ ] Flaglar restartdan keyin saqlanadi.
- [ ] Flag audit yozuvi yaratiladi.
- [ ] listener_sources, ai_processing, notifications, nlp va content_publishing alohida boshqariladi.
- [ ] Test tugagach xavfli flaglar o'chiriladi.

## 8. Bot va Mini App funksiyalari

- [ ] /start userni ro'yxatdan o'tkazadi.
- [ ] Uzbek, rus va aralash til search ishlaydi.
- [ ] Search natijasida narx, period, district, rooms va source ko'rinadi.
- [ ] Saved filter yaratish va o'chirish ishlaydi.
- [ ] Referral, notification va channel suggestion tugmalari ishlaydi.
- [ ] Mini App tugmasi to'g'ri HTTPS URL ochadi.
- [ ] Bot loglarida token, phone number yoki raw message text yo'q.

## 9. Admin operations

- [ ] GET /admin/source-suggestions/pending ishlaydi.
- [ ] Source suggestion approve va reject ishlaydi.
- [ ] GET /admin/manual-review/pending ishlaydi.
- [ ] Manual review action admin authorization'ni tekshiradi.
- [ ] Audience tag approve, reject va merge ishlaydi.
- [ ] GET /admin/metrics/beta ishlaydi.
- [ ] Metrics'da notification, retry, error va listener health ko'rinadi.
- [ ] POST /admin/adapters/reload ishlaydi.
- [ ] Listener status account va source health'ni ko'rsatadi.
- [ ] Admin actionlar audit jadvaliga yoziladi.

## 10. Telegram listener va text pipeline

- [ ] Listener account online.
- [ ] Test source enabled=true va status=active.
- [ ] Source listener account'ga assignment qilingan.
- [ ] POST /admin/listeners/refresh ishlaydi.
- [ ] Refresh'dan keyin subscription logda ko'rinadi.
- [ ] Test kanaliga text post yuboriladi.
- [ ] Post ingestion.raw_announcements queue'ga tushadi.
- [ ] Pre-AI worker keyingi queue'ga uzatadi.
- [ ] AI worker post-AI queue'ga uzatadi.
- [ ] Dedup worker announcement'ni bazaga yozadi.
- [ ] Queue backlog qolmaydi.
- [ ] announcements'da channel ID va message ID to'g'ri.
+

## 11. Telegram image va album pipeline

- [ ] Bitta PNG yoki JPEG rasmli e'lon yuboriladi.
- [ ] Raw payload'da media reference bor, binary queue'ga yozilmagan.
- [ ] AI worker Telegram media'ni yuklab oladi.
- [ ] MIME type va image signature mos.
- [ ] Resize/compression limitlari ishlaydi.
- [ ] announcement_media row'ida storage_url, object_key, MIME, size va hash mavjud.
- [ ] Spaces'dan object qayta o'qilganda SHA-256 bazadagi hash bilan mos.
- [ ] Spaces HEAD Content-Type va Content-Length mos.
- [ ] 2-5 ta rasmli album yuboriladi.
- [ ] Album bitta announcement sifatida yig'iladi.
- [ ] Near-duplicate bo'lmagan barcha rasmlar saqlanadi.
- [ ] Bir xil rasm pHash bilan takroran saqlanmaydi.
- [ ] Buzilgan yoki unsupported fayl worker'ni yiqitmaydi.
- [ ] Storage failure'da noto'g'ri URL persist qilinmaydi.
- [ ] Partial upload failure'da orphan objectlar o'chiriladi.

## 12. AI va dedup

- [ ] AI canonical listing qaytaradi.
- [ ] Rasmli renovation uchun Vision ishlaydi.
- [ ] Past confidence manual review yaratadi.
- [ ] Invalid JSON yoki OpenRouter error retry/DLQ'ga tushadi.
- [ ] Bir xil Telegram message duplicate announcement yaratmaydi.
- [ ] O'xshash listing parent-child dedup bilan birlashadi.
- [ ] Faqat canonical parent search'da ko'rinadi.
- [ ] Child duplicate notification yubormaydi.
- [ ] dedup_decisions va merge audit yozuvlari mavjud.

## 13. Notification end-to-end

- [ ] Test user saved filter yaratadi.
- [ ] Matching test listing yuboriladi.
- [ ] announcement.persisted event yaratiladi.
- [ ] Matcher bitta notification job yaratadi.
- [ ] notification.delivery queue'ga job tushadi.
- [ ] Test botga notification keladi.
- [ ] Listing ID va Mini App link to'g'ri.
- [ ] open, search va read callbacklari ishlaydi.
- [ ] Retry duplicate notification yubormaydi.
- [ ] Telegram 429 holatida retry va next_attempt_at ishlaydi.
- [ ] Bot bloklansa permanent failure yoziladi.
- [ ] Delivery attempt audit jadvaliga yoziladi.
- [ ] Haqiqiy foydalanuvchi o'rniga faqat test user ishlatiladi.

## 14. Referral va channel suggestion

- [ ] Referral link yaratiladi.
- [ ] Ikkinchi test user referral orqali kiradi.
- [ ] Invitee search yoki filter yaratganda referral active bo'ladi.
- [ ] Beshinchi valid referral'da Premium bir marta beriladi.
- [ ] Retry Premium'ni qayta bermaydi.
- [ ] User kanal taklif qiladi.
- [ ] Suggestion internal API orqali saqlanadi.
- [ ] Admin pending suggestion'ni ko'radi.
- [ ] Approve source registryga source qo'shadi.
- [ ] Reject user va audit statusini to'g'ri o'zgartiradi.

## 15. Failure va recovery

- [ ] AI worker to'xtatilganda message backlogda qoladi.
- [ ] AI worker qayta ko'tarilganda backlog qayta ishlanadi.
- [ ] Spaces endpoint yoki credential noto'g'ri bo'lsa in-memory fallback bo'lmaydi.
- [ ] Spaces upload error log yoki metric'da ko'rinadi.
- [ ] OpenRouter xatosi retry yoki manual-review holatini yaratadi.
- [ ] Telegram FloodWait account status'da ko'rinadi.
- [ ] Retry limit'dan keyin message DLQ'ga tushadi.
- [ ] DLQ message correlation ID bilan topiladi.
- [ ] Redis va PostgreSQL recovery'dan keyin servislar ready bo'ladi.
- [ ] Failure loglarida secret yoki raw PII yo'q.
+

## 16. 10 ta Telegram kanal smoke-test

- [ ] 10 ta source active va enabled.
- [ ] Har bir source account assignment'ga ega.
- [ ] Listener status'da 10 source ko'rinadi.
- [ ] Har bir kanalga bittadan text post yuboriladi.
- [ ] Har bir kanalga bittadan image post yuboriladi.
- [ ] Har bir kanal uchun channel ID va message ID to'g'ri saqlanadi.
- [ ] Har bir image Spaces'da alohida object key bilan paydo bo'ladi.
- [ ] Queue backlog doimiy o'smaydi.
- [ ] Worker restart loop'ga kirmaydi.
- [ ] DLQ'lar bo'sh yoki har bir xato hujjatlashtirilgan.
- [ ] Database announcement/media count yuborilgan xabarlarga mos.
- [ ] Spaces'da orphan va duplicate objectlar tekshiriladi.

## 17. Security va production safety

- [ ] Admin API public Internet'ga ochilmagan yoki firewall bilan himoyalangan.
- [ ] RabbitMQ management port public emas.
- [ ] PostgreSQL trust faqat local development'da.
- [ ] Production'da passwordli PostgreSQL ishlatiladi.
- [ ] .env, session va credentiallar git'da yo'q.
- [ ] HTTPS sertifikat valid.
- [ ] Bot polling yoki webhook faqat bitta active consumer bilan ishlaydi.
- [ ] Spaces bucket public/private siyosati aniq.
- [ ] Private bucket bo'lsa signed URL yoki proxy endpoint mavjud.
- [ ] Backup va restore test qilingan.

## 18. Rollback va yakuniy qabul

- [ ] Migration'dan oldin backup olindi.
- [ ] Rollback tartibi yozildi.
- [ ] content_publishing, notifications, nlp, ai_processing, listener_sources va bot_access flaglarini o'chirish sinovdan o'tdi.
- [ ] Queue backlog saqlangan holda workerlarni to'xtatish sinovdan o'tdi.
- [ ] User-facing outage xabari tayyor.
- [ ] Final docker compose ps saqlandi.
- [ ] Final health endpointlar 200.
- [ ] Final automated test o'tdi.
- [ ] Final queue va DLQ holati saqlandi.
- [ ] Final Telegram source/account status saqlandi.
- [ ] Final Spaces object/hash tekshiruvi saqlandi.
- [ ] Har bir FAIL uchun issue yoki tuzatish rejasi ochildi.

## Final evidence pack

~~~powershell
docker compose ps > test-evidence-compose.txt
docker compose logs --since 30m > test-evidence-logs.txt
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers > test-evidence-queues.txt
.venv\Scripts\python.exe -m pytest -q > test-evidence-pytest.txt
~~~

- [ ] Compose evidence saqlandi.
- [ ] Log evidence ichida secret yoki PII yo'q.
- [ ] Queue evidence saqlandi.
- [ ] Pytest evidence saqlandi.
- [ ] Real Telegram image uchun announcement ID, media row, object key va hash yozib olindi.
- [ ] VPS evidence fayllariga token, credential yoki private key yozilmadi.
