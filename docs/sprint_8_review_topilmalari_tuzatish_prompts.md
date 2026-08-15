# Sprint 8 - Review Topilmalarini Tuzatish: AI Agent Promptlari

## Maqsad

Ushbu sprint code reviewda aniqlangan production bloklovchilarini yopadi:

- API va worker/bot uchun haqiqiy composition root yo'q.
- Migration runner faqat eski SQL fayllarning bir qismini ko'radi va smoke script noto'g'ri.
- `models/` bo'sh; database contract SQL fayllar hamda repository kodlari orasida tarqalib ketgan.
- Source suggestion endpointida foydalanuvchi identifikatori ishonchsiz request bodydan olinadi.
- Beta feature flaglari process xotirasida saqlanadi va restartdan keyin yo'qoladi.

## Qollash tartibi

Promptlarni `8.0` dan `8.6` gacha aynan ketma-ket bajartiring. Bitta promptning qabul mezonlari va testlari bajarilmaguncha keyingisini yubormang. Bu refactor production ma'lumotlarini yo'qotmasligi shart; `DROP TABLE`, schema reset yoki mavjud migrationlarni yashirin qayta yozish taqiqlanadi.

## Umumiy kontekst

```text
Sen EstateFlow loyihasining principal Python backend va database migration muhandisisan. Loyihada FastAPI, PostgreSQL, Redis, Aiogram, Telethon va background workerlar ishlatiladi. Hozirgi repositoryda `migrations/001...010_*.sql` legacy migrationlari bor, `src/estateflow/models/` deyarli bo'sh, Alembic esa o'rnatilmagan.

Avval quyidagi fayllarni o'qi:
- docs/real_estate_sprint_plan.md
- docs/real_estate_v3.md
- docs/real_estate_changelog.md
- docs/real estate.md
- docs/sprint_7_mvp_beta_handoff.md
- docs/sprint_7_release_readiness.md
- docs/production_launch/04_data_and_migrations.md
- reviewda ko'rsatilgan kodlar: src/estateflow/main.py, src/estateflow/api/app.py, scripts/migration_smoke.py, migrations/001...010_*.sql

Qarorlar ustuvorligi: changelog > v3 > base document > old prompts. Mavjud uncommitted o'zgarishlarni saqla. Avval `git status`, package config, database schema va testlarni tekshir. Ishni `models/` ichida SQLAlchemy 2.0 typed ORM modellari bilan, repositorylarda async SQLAlchemy session orqali davom ettir; Pydantic schema, domain dataclass va ORM modelni bir obyektga aralashtirma.

Alembic bo'yicha qat'iy qoidalar:
1. Fresh database `alembic upgrade head` bilan barcha schema'ni olishi shart.
2. Legacy SQL bilan avvaldan yaratilgan baza uchun schema inspection + documented bridge/stamp yo'li bo'lsin. Avval schema mosligi tekshirilmaguncha `stamp head` qilinmasin.
3. Production ma'lumotini yo'qotadigan reset, table drop yoki revision tarixini qayta yozish mumkin emas.
4. DDL va ORM metadata bir-biriga mosligini CI/test database'da tekshir.

Har task oxirida: o'zgargan fayllar, arxitektura/migration qarorlari, bajarilgan test buyruqlari va natijalari, qolgan risklarni qisqa hisobot qil. Real production database yoki Telegramga ulanma; local throwaway PostgreSQL yoki test double bilan isbotla.
```

## Prompt 8.0 - Baseline audit va xavfsiz migration strategiyasi

```text
Yuqoridagi umumiy kontekstga amal qil. Kod yozishdan avval current database/migration holatini audit qil va SQLAlchemy + Alembicga o'tish uchun aniq, data-safe implementation planini repositoryga yoz.

Vazifa:
1. `migrations/001...010_*.sql` fayllarini, barcha `repositories/` va `services/` database contractlarini, test fixturelarni hamda docker/deployment runbookni inventory qil.
2. Har legacy SQL fayl yaratadigan/alter qiladigan table, column, index, constraint va seed data ro'yxatini tuz. `source_suggestions`, notifications, analytics, feature flags, admin audit, content posts va listener/dedup jadvallari qamrab olinganini alohida tekshir.
3. Hozirda production-like database bo'lishi mumkin bo'lgan ikki holat uchun migration plan yoz:
   - fresh database: Alembic `upgrade head`;
   - legacy SQL orqali qisman/to'liq yaratilgan database: schema fingerprint/inspection, moslik tasdiqlansa bridge revision yoki `stamp`, mos kelmasa explicit forward migration.
4. Qaysi eski `migrations/*.sql` fayllari arxiv/referens bo'lib qolishi, qaysilari Alembic revisionga ko'chirilishi va qaysi scriptlar olib tashlanishi/deprecated qilinishi kerakligini yoz. Yangi va eski ikki migration engine'ni bir vaqtda production source of truth qilib qoldirma.
5. Hozirgi review xatolarini reproduksiya qiluvchi red tests yoz: default app search endpointining 503 holati, migration smoke'da `is_promoted = null` xatosi, restartdan keyin in-memory flag yo'qolishi va bodydagi arbitrary `user_id` bilan source suggestion submission.
6. Reja tasdiqlanganidan keyingina keyingi promptga o't. Bu promptda schema reset, ommaviy refactor yoki production DBga DDL qo'llama.

Qabul mezonlari:
- Legacy schema inventory to'liq va review qilinadigan hujjatga ega.
- Fresh va legacy database uchun alohida, xavfsiz migratsiya yo'li bor.
- Keyingi promptlar uchun red testlar mavjud.
- Data-loss shortcut ishlatilmagan.
```

## Prompt 8.1 - SQLAlchemy 2.0 ORM model qatlami

```text
Yuqoridagi umumiy kontekstga amal qil. `src/estateflow/models/`ni EstateFlow database uchun yagona SQLAlchemy 2.0 typed ORM model qatlamiga aylantir.

Vazifa:
1. `pyproject.toml`ga SQLAlchemy 2.x va Alembic dependencylarini qo'sh. Async PostgreSQL uchun SQLAlchemy async engine/session maker ishlat; sync engine faqat Alembic migration contexti uchun bo'lsin.
2. `models/base.py`da yagona DeclarativeBase, naming convention va metadata yarat. Migrationlar uchun barcha model modullari import qilinadigan aniq registry ber.
3. Har persistent jadval uchun `Mapped[...]` va `mapped_column()` ishlatadigan ORM model yarat. Kamida: listener accounts, ingestion sources, channel assignments/audit, users, announcements, announcement media, user filters, notifications/delivery attempts, parsing logs, processing jobs, referrals, source suggestions, audience tag types, dedup decisions/merge audit/manual review, admin audit, content posts, analytics events, technical metric events va persistent feature flags.
4. SQL types, UUID/BIGINT FKlar, status enum/check constraintlar, unique/indexlar, server defaults va timezone-aware timestamp'larni legacy schema va hujjatlarga moslashtir. `announcements` parent-child relationshipi, media, filters, notification hamda referral relationshiplarini explicit belgilab qo'y.
5. Changelog qoidalarini saqla: phone dedup weight=15 configda; flat audience tags, `parent_tag_id` yo'q; `price_period`, `price_basis`, `price_normalized_monthly`; R2 media reference; `is_promoted` nullable emas va default false.
6. ORM modelni API Pydantic response yoki AI extraction dataclass sifatida ishlatma. Kerak bo'lsa domain-to-ORM mapping/repository adapter yoz, lekin service public contractlarini birdan buzma.
7. Model metadata uchun tests yoz: table/column/constraint/index inventory, relationship integrity va legacy SQL inventory bilan moslik. `mypy --strict` hamda Ruff toza bo'lsin.

Qabul mezonlari:
- `models/` barcha persistent tablelarning SQLAlchemy source of truthiga aylangan.
- Domain/Pydantic/ORM qatlamlari ajratilgan.
- Model metadata Alembic autogenerate uchun deterministic import qilinadi.
- Yangi model layer existing unit testlarni regressiya qilmaydi.
```

## Prompt 8.2 - Alembic setup va legacy SQLdan xavfsiz o'tish

```text
Yuqoridagi umumiy kontekstga amal qil. Alembicni joriy et, barcha schema evolutionni Alembic revisionlar orqali boshqar va legacy SQL migration holatini xavfsiz bridge qil.

Vazifa:
1. Repository rootda Alembic config, `alembic/env.py`, revision directory va SQLAlchemy metadata integrationini yarat. DB URL/secretni typed Settingsdan ol, passwordni logga chiqarmagin.
2. Fresh database uchun `alembic upgrade head` bilan Sprint 0-8 talab qilgan barcha table, constraint, index va seed taglar yaratilishini ta'minla. Revisionlar deterministik, transaction-safe va PostgreSQLga mos bo'lsin.
3. Legacy `001...010.sql` holati bilan qanday muomala qilinishini implement qil:
   - schema mavjud emas bo'lsa, Alembic boshidan yaratadi;
   - legacy schema metadata bilan mos bo'lsa, faqat explicit inspectiondan keyin revisionni stamp/bridge qiladi;
   - qisman yoki farqli schema bo'lsa, additiv forward revision orqali tuzatadi.
   Hech qachon `stamp head`ni schema tekshiruvisiz avtomatik bajarma.
4. Oldingi `scripts/migration_smoke.py` va `.psql` scriptini Alembicga o'tkaz yoki deprecated qil. U `001-010`ning faqat bir qismini qo'llab, yolg'on "success" bermasin. `is_promoted` non-null defaultini hurmat qiladigan test data ishlat.
5. `alembic upgrade head`, `alembic current`, fresh DB migration smoke va legacy-bridge schema validation uchun script/Make target yoki documented commands yarat. Docker compose'da migrationni API container ichida parallel yashirin ishga tushirma; alohida one-shot migration job yoki release step bo'lsin.
6. Revisionlar uchun downgrade policy yoz. Destructive rollback o'rniga backup/restore talabi bo'lsa uni revision docstring/runbookda ochiq qayd qil.
7. Throwaway PostgreSQL database/schema bilan real integration test yoz: upgrade head, expected tables/columns/indexlar, second upgrade idempotency, current revision va `is_promoted` default insert. Testda legacy SQL scriptni ishlatib bo'lmaydigan yo'l qolmasin.

Qabul mezonlari:
- Fresh database `alembic upgrade head`dan keyin to'liq schema oladi.
- Migration tool `004-010` featurelarini tashlab ketmaydi.
- Legacy database uchun xavfsiz, tekshirilgan bridge mavjud.
- Eski smoke scriptdagi `null is_promoted` xatosi yo'q.
```

## Prompt 8.3 - Application composition root, DB lifecycle va runtime entrypointlar

```text
Yuqoridagi umumiy kontekstga amal qil. API, bot va workerlarning haqiqiy runtime composition rootini SQLAlchemy async sessions, Redis va external adapterlar bilan yakunla.

Vazifa:
1. `create_app`ga FastAPI lifespan qo'sh. Startupda async SQLAlchemy engine/session maker va Redis client/poolni yarat; shutdownda ularni deterministik yop. Health/readiness shu haqiqiy dependencylarni tekshirsin.
2. SQLAlchemy repository implementatsiyalarini yarat yoki mavjud asyncpg repositorylarni bosqichma-bosqich SQLAlchemy AsyncSessionga o'tkaz. Search, saved filter, source suggestion, audience tags, manual review, content automation, analytics/technical metrics va persistent feature flags uchun real repository/service instance'lari app state/DI orqali ulanib tursin.
3. Default `estateflow.main:app` real service compositionni ishlatsin. `GET /search/announcements` service yo'q degan 503 emas, mavjud bo'sh database uchun 200 + empty result qaytarsin.
4. API, Telegram bot va worker uchun alohida, aniq entrypointlar yarat. Ularning barchasi bitta shared composition factorydan foydalanishi mumkin, ammo API process Telethon listener/workerlarni implicit boshlamasin. Graceful shutdown va feature flag wrappersni haqiqiy runtime dependencylariga ulab qo'y.
5. Queue, OpenRouter, R2 va Telegram credentiallari yo'q muhitda service startup fail-fast yoki documented disabled mode bo'lsin. Test muhitida fake adapter injection saqlansin.
6. Docker Compose/README/runbookni update qil: migrate one-shot step, API, worker va bot ishga tushirish tartibi, health endpoints va local test flow. PostgreSQL `trust` konfiguratsiyasi local-development-only deb aniq ajratilsin; productionda password/secret talab qilinsin.
7. Real PostgreSQL/Redis test container yoki local throwaway Docker stack bilan integration test yoz: Alembic upgrade -> API startup -> search empty result -> shutdown resource cleanup. Default appda critical admin service'lar ham 503 bo'lmasligini tekshir.

Qabul mezonlari:
- `estateflow.main:app` real repository/service graph bilan ishga tushadi.
- Search va admin/metrics/content servislarining default 503 "not configured" holati yo'q.
- DB/Redis connectionlar lifespan davomida boshqariladi.
- API, bot va worker operationally alohida ishga tushiriladi.
```

## Prompt 8.4 - Source suggestion authentication va audit integrity

```text
Yuqoridagi umumiy kontekstga amal qil. Source suggestion oqimidagi user identity spoofing xatosini tuzat va admin audit actorini ishonchli qil.

Vazifa:
1. `POST /admin/source-suggestions` endpointini audit qil. User-facing source suggestion endpointi `user_id`ni request bodydan qabul qilmasin.
2. Telegram bot flow uchun user ID faqat `message.from_user.id`/trusted controller contextdan o'tsin. Agar REST endpoint kerak bo'lsa, avval Telegram WebApp initData yoki loyiha tanlagan authenticationni server-side validate qil, so'ng user identityni auth contextdan ol. Secure auth mavjud bo'lmasa endpointni public expose qilma yoki internal-only route qil.
3. Source suggestion submit, reopen, approve/reject va premium reward oqimlarida authenticated actor va target user farqini to'g'ri persist qil. Admin actor `admin_user_id`ni client bodydan ishonchli fact sifatida qabul qilma; token/session claim yoki server-side admin mappingdan ol.
4. Admin API token compare uchun security best practice qo'lla; admin authorization, action authorization va audit identityni alohida concern sifatida saqla.
5. Rate limiting va duplicate source submission protectionni authenticated user/source identifier asosida qo'lla. Impersonation yoki replay bilan boshqa userning suggestionini qayta ochib bo'lmasin.
6. Tests yoz: arbitrary body user_id rad etiladi/ignore qilinadi, valid bot identity ishlaydi, invalid/missing auth rad etiladi, non-admin approval qila olmaydi, audit recordda server-derived actor turadi va reward exactly-once qoladi.

Qabul mezonlari:
- Client user ID orqali boshqa user nomidan source suggestion yubora olmaydi.
- Admin action auditidagi actor ishonchli server-side identitydan keladi.
- Bot flow funksionalligini saqlagan holda REST surface xavfsizlashgan.
- Authentication tests regressiyani tutadi.
```

## Prompt 8.5 - Persistent feature flags va beta controls

```text
Yuqoridagi umumiy kontekstga amal qil. In-memory feature flag store o'rniga PostgreSQL-backed, auditli va multi-process-safe release controlni implement qil.

Vazifa:
1. SQLAlchemy model va Alembic revisionda persistent feature flag state hamda immutable audit eventlarini yarat. Feature nomi unique bo'lsin; enabled state, version/updated timestamp va actor/reason auditda saqlansin.
2. SQLAlchemy repository bilan `ReleaseControlService`ni persistent storega o'tkaz. Startda `.env`dan safe default/seed qilish mumkin, ammo admin orqali qilingan runtime flag o'zgarishi restartdan keyin yo'qolmasin va environment defaulti uni yashirin bosib ketmasin.
3. Concurrent admin update uchun optimistic concurrency/version yoki transaction lock ishlat. API processlar va worker/bot processlari flaglarning yangi holatini ko'ra olsin; qisqa cache ishlatilsa invalidation/TTL policy yoz.
4. Beta allowlistning persistence va source-of-truth siyosatini aniq qil. User access qarori bot entrypointda ham, feature-gated listener/notification/content adapterlarida ham haqiqiy composition root orqali qo'llanayotganini tekshir.
5. Admin endpointdan actor/reason/expected-version bilan flag o'zgartirishni qo'lla. Server restart, ikkinchi service instance va concurrent update holatlarida audit hamda final state test qilinsin.
6. Rollback runbookni yangila: flagni o'chirish, audit ko'rish, cache propagation kutish va featurega tegishli queue/joblarni xavfsiz to'xtatish qadamlari bo'lsin.

Qabul mezonlari:
- Flag o'zgarishi restart va processlar orasida saqlanadi.
- Concurrent update silent overwrite qilmaydi.
- Beta access va feature gate'lar real runtimega ulangan.
- Audit actor/reason/timestamp bilan immutable saqlanadi.
```

## Prompt 8.6 - To'liq verification va beta-ready quality gate

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 8 yakunida barcha review topilmalarini real evidence bilan qayta tekshir; faqat aniqlangan buglarni tuzat va yakuniy handoff tayyorla.

Vazifa:
1. Clean/throwaway PostgreSQLda `alembic upgrade head` bajar, metadata/schema parityni tekshir va legacy bridge scenario'ni alohida sinovdan o'tkaz. `alembic current` head revisionni ko'rsatishini isbotla.
2. API integration testda default composition root bilan quyilarni tekshir: liveness/readiness, empty searchning 200 javobi, source suggestion auth policy, protected admin endpointlar, metrics/content/admin service availability.
3. API, worker va bot uchun startup/shutdown smoke testni credentialsiz disabled/fake-adapter rejimida bajar. Real Telegram, OpenRouter yoki R2ga murojaat qilma.
4. Persistent feature flag testini bajar: flag update -> yangi app/service instance -> qiymat/audit saqlangan; concurrent update conflict; bot allowlist va gated adapterlar yangi holatni ko'radi.
5. To'liq quality checksni bajar: pytest, Ruff, MyPy, Alembic migration smoke va Docker Compose config. Agar real PostgreSQL Docker test ishlatilgan bo'lsa, command hamda natijasini handoffga yoz.
6. `docs/sprint_8_remediation_handoff.md` yarat: architecture composition diagram/izoh, Alembic workflow, legacy migration bridge, auth contract, persistent flags policy, bajarilgan test evidence va qolgan known risklar. Oldingi noto'g'ri "beta-ready" xulosasini faqat barcha P1 topilmalar yopilgan bo'lsa yangila.

Qabul mezonlari:
- Reviewdagi barcha P1/P2 topilmalar test yoki operational evidence bilan yopilgan.
- Alembic va SQLAlchemy models production schema uchun yagona source of truth.
- Default runtime asosiy endpointlarni 503 bilan yiqitmaydi.
- Beta readiness xulosasi faktlarga asoslangan.
```
