# Sprint 0 - Infratuzilma va Fundament: AI Agent Promptlari

## Qollash tartibi

Quyidagi promptlarni aynan berilgan ketma-ketlikda bajartiring. Bitta promptning acceptance criteria'lari bajarilmaguncha keyingisini yubormang. Bir xil agent taskida ishlasangiz, `Umumiy kontekst` blokini bir marta yuborish kifoya; yangi agent taski uchun esa uni har safar prompt bilan birga yuboring.

## Umumiy kontekst

```text
Sen EstateFlow loyihasining lead Python backend muhandisisan. Maqsad: O'zbekiston ko'chmas mulk e'lonlarini Telegram va boshqa manbalardan yig'ib, AI bilan strukturalash, dublikatlarni birlashtirish va Telegram botda qidirishga imkon beruvchi modular monolith MVP qurish.

Ishni boshlashdan avval repository holatini va quyidagi hujjatlarni o'qi:
- docs/real_estate_sprint_plan.md
- docs/real_estate_v3.md
- docs/real_estate_changelog.md
- docs/real estate.md

Qarorlar ustuvorligi: real_estate_changelog.md > real_estate_v3.md > real estate.md materiallar. 

Mavjud kod va foydalanuvchining uncommitted o'zgarishlarini saqla. Avval `git status`, loyiha daraxti va tegishli fayllarni tekshir; mavjud konvensiyani davom ettir. Python 3.12+, async FastAPI, PostgreSQL, Redis, Aiogram 3 va Telethon arxitekturasi ishlatiladi. Ishlaydigan credential bo'lmasa, real Telegram yoki cloud servisiga ulanma; test double va konfiguratsiya bilan tekshir.

Har task yakunida quyidagilarni qisqa hisobot qil: o'zgargan fayllar, asosiy qarorlar, bajarilgan test/buyruqlar va natijalari, qolgan bloklovchi omillar. Test ishlamasa, sababini aniq yoz; muvaffaqiyat deb taxmin qilma.
```

## Prompt 0.1 - Repository audit va application skeleton

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 0 ning birinchi qadami sifatida EstateFlow repository'sini audit qil va minimal, ishga tushadigan application skeletonini yarat.

Vazifa:
1. Avval mavjud fayllar, paket menejeri, test vositasi va git holatini tekshir. Mavjud yechim bo'lsa, qayta yaratma; uni izchil kengaytir.
2. Python 3.12+ uchun yagona dependency boshqaruvini tanla yoki mavjudini davom ettir. Kerakli runtime va development dependency'larni aniq versiya chegaralari bilan rasmiylashtir.
3. Modular monolith tuzilmasini yarat yoki moslashtir: application/core, api, bot, adapters, models, repositories, schemas, services, workers va tests qatlamlari. Dependency yo'nalishi yuqori qatlamdan past qatlamga bo'lsin; business logic endpoint yoki handler ichida qolmasin.
4. FastAPI application factory, `/health/live` uchun yengil endpoint va import-safe entry point yarat. Hech qanday business feature, Telegram ulanishi yoki real external call qo'shma.
5. `pydantic-settings` asosida typed Settings yarat. Konfiguratsiya `.env` dan o'qilsin, lekin secret qiymatlar repository'ga yozilmasin. To'liq `.env.example` qo'sh; production uchun xavfsiz bo'lmagan default secret bermagin va required secret'larni xatosi tushunarli bo'ladigan qilib validatsiya qil.
6. Minimal custom exception hamda API error response konvensiyasini yoz. Bu keyingi sprintlarda kengayishi mumkin, lekin hozircha ortiqcha framework yaratma.
7. Application importi va health endpoint uchun avtomatlashtirilgan test yoz.

Qabul mezonlari:
- Toza clone'da environment variables'ning faqat non-secret development qiymatlari bilan application import bo'ladi.
- `/health/live` database yoki Redisga bog'liq bo'lmagan holda 200 qaytaradi.
- Secretlar logga, `.env.example`ga yoki git-tracked faylga chiqmaydi.
- Tanlangan test va lint/type-check buyruqlari ishlaydi yoki aniq bloklovchi sabab qayd qilinadi.
```

## Prompt 0.2 - Docker Compose, PostgreSQL va Redis muhiti

```text
Yuqoridagi umumiy kontekstga amal qil. Mavjud Sprint 0 skeletoni ustiga local development uchun qayta tiklanadigan container muhitini amalga oshir.

Vazifa:
1. Mavjud package va config konvensiyalarini tekshir; Dockerfile, docker-compose konfiguratsiyasi va kerak bo'lsa compose override'larni minimal tarzda qo'sh.
2. PostgreSQL va Redis servislarini healthcheck, named volume, aniq network va environment orqali boshqar. Application konfiguratsiyasi container ichidan ham, hostdan ham ulanishi mumkin bo'lsin.
3. API servisiga faqat kerakli portni expose qil. Development hamda production ehtiyojlarini aralashtirma; production secretlar yoki real Telegram session fayllarini image'ga COPY qilma.
4. `.env.example`ga DB, Redis, application va keyingi sprintlar uchun nomlangan placeholderlarni qo'sh. Masalan, Telegram account pool, OpenRouter va R2 uchun nomlar bo'lishi mumkin, ammo haqiqiy qiymat yoki real servisga bog'lanish bo'lmasin.
5. Startup dependency'lari va retry'larni Docker Compose darajasida haddan tashqari yashirma: application ready endpoint keyinroq alohida tekshiradi. Ma'lumotlar volume'lari va migration ishga tushirish tartibini README yoki runbookda aniq yoz.
6. Docker mavjud bo'lsa, compose config validation va local startup smoke testini bajar. Docker mavjud bo'lmasa, konfiguratsiyani statik tekshir va buni hisobotda aniq ayt.

Qabul mezonlari:
- `docker compose config` xatosiz o'tadi.
- PostgreSQL va Redis healthcheck'lari mustaqil ishlaydi.
- Local database ma'lumotlari named volume orqali container restartidan keyin saqlanadi.
- Gitga secret, session, database dump yoki build artifact kirmaydi.
```

## Prompt 0.3 - Structured logging, readiness va Ops notifier asosi

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 0 uchun observability asosini va alohida Ops Telegram notifier abstraksiyasini amalga oshir.

Vazifa:
1. Structured logging'ni sozla: developmentda o'qilishi oson console format, productionda JSON format. Har eventga timestamp, level, service, event name va correlation ID bog'lansin. So'rovlar uchun correlation ID middleware yarat; mavjud ID bo'lsa saqla, bo'lmasa yangi yarat.
2. PII va secret redaction siyosatini qo'lla. Telefon raqami, bot token, API key, Telegram session yoki to'liq xom e'lon matni default loglarda chiqmasin.
3. `/health/ready` endpointini yarat. U PostgreSQL va Redisni qisqa timeout bilan tekshiradi, dependency xatosini xavfsiz va tashxis uchun foydali formatda qaytaradi. `/health/live` bundan mustaqil qoladi.
4. Foydalanuvchi botidan mustaqil `OpsNotificationService` interfeysini yarat. U alohida OPS bot tokeni va ops chat/channel ID orqali critical eventlarni yubora olsin. Xabar formatida severity, qisqa sabab, vaqt va correlation ID bo'lsin; secret yoki xom PII yuborilmasin.
5. Ops alertlari uchun rate-limit/dedup mexanizmini qo'sh: bir xil xato qisqa muddatda takrorlansa individual spam emas, agregatsiyalangan signal yuborilsin. Credential yo'q bo'lsa servis no-op yoki aniq disabled holatda bo'lsin, application startup yiqilmasin.
6. Correlation ID, redaction, liveness/readiness va alert aggregation uchun unit yoki integration testlar yoz.

Qabul mezonlari:
- Bir API so'rovidagi loglar bitta correlation ID bilan bog'lanadi.
- Readiness DB/Redis muammosida noto'g'ri 200 qaytarmaydi va secret oshkor qilmaydi.
- Ops notifier user bot tokenidan foydalanmaydi.
- Takroriy xatolar alert kanalini spam qilmaydi.
```

## Prompt 0.4 - Sprint 0 integratsion tekshiruvi va handoff

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 0 natijasini integratsion audit qil, yetishmayotgan faqat fundament darajasidagi qismlarni to'ldir va keyingi sprint uchun aniq handoff tayyorla.

Vazifa:
1. Repository'ni boshidan tekshir: application factory, typed config, Docker Compose, PostgreSQL/Redis connectivity, liveness/readiness, structured logging va Ops notifier bir-biriga mos kelishini tasdiqla.
2. Mavjud testlar bilan minimal smoke flow yarat yoki kengaytir: app start, `/health/live`, dependency mavjud bo'lganda `/health/ready`, dependency yo'qolganda safe failure. Real Telegram, OpenRouter yoki R2 chaqiruvi qilinmasin.
3. Configuration keylari, portlar, service nomlari va package entry pointlar orasidagi nomuvofiqliklarni tuzat. Scope'ni Sprint 1 feature'lariga kengaytirma.
4. `docs/`da qisqa local bootstrap/runbook yoz yoki mavjudini yangila: environment tayyorlash, compose ishga tushirish, migration keyingi sprintda qayerga ulanashi, test hamda loglarni ko'rish buyruqlari.
5. Keyingi Sprint 1 uchun contractlarni qayd qil: source config qayerdan olinadi, queue interface qanday kengayadi va Ops event qanday yuboriladi. Bu contractlar koddagi aniq interfeyslar bilan mos bo'lsin.

Qabul mezonlari:
- Sprint 0 acceptance criteria'lari bitta takrorlanadigan smoke test yo'li bilan tekshirilgan.
- Runbook yangi dasturchi local muhitni tushunishi uchun yetarli.
- Handoff hujjati keyingi sprintda taxmin qilishga majbur qiladigan yashirin qaror qoldirmaydi.
```
