# Sprint 7 - Test, Beta va Metrikalar: AI Agent Promptlari

## Qollash tartibi

Promptlarni `7.1` dan `7.5` gacha ketma-ket yuboring. Bu sprintning maqsadi yangi feature qo'shish emas, MVP oqimlarini isbotlash, kuzatuvni yoqish va xavfsiz beta launchga tayyorlashdir.

## Umumiy kontekst

```text
Sen EstateFlow loyihasining lead quality, reliability va release muhandisisan. Sprint 7 maqsadi: asosiy oqimlar uchun unit/integration/E2E test qamrovini yakunlash, beta metrikalarini aniq hisoblash, soft-launch readinessni tekshirish va aniqlangan buglarni tuzatish.

Avval repository hamda docs/real_estate_sprint_plan.md, docs/real_estate_v3.md, docs/real_estate_changelog.md va docs/real estate.md ni o'qi. Qarorlar ustuvorligi: changelog > v3 > base document. Real Telegram account, real user yoki production channelga hech qanday xabar yuborma, deploy qilma yoki beta userlarni o'zgartirma, agar alohida vakolat berilmagan bo'lsa. Buning o'rniga launch checklist va dry-run tayyorla.

Beta metrikalari: activation rate (kamida bitta search yoki filter yaratgan userlar / ro'yxatdan o'tgan userlar), D7 retention (cohortdagi user 7-kunda yoki belgilangan oynada qaytgan), referral conversion (invited userlardan active bo'lganlari), notification engagement (notification action/click/read eventlari). Denominator, vaqt oralig'i, timezone va missing data qoidalari yozma ravishda aniq bo'lishi shart.

Har task tugagach changed files, test/buyruqlar natijasi, regressions va bloklovchilarni hisobot qil.
```

## Prompt 7.1 - Test foundation, fixtures va deterministik integration environment

```text
Yuqoridagi umumiy kontekstga amal qil. MVPning test poydevorini audit qil va mavjud bo'shliqlarni to'ldir.

Vazifa:
1. Hozirgi unit, integration va E2E testlarni inventory qil. Core flows bilan mapping qil: ingestion, Pre-AI filter, AI extraction/media/R2, post-AI dedup, search/saved filters, notification, NLP, referral, admin review va content scheduling.
2. Test environmentni deterministik qil: isolated test database, Redis/fake queue, fake Telethon/Aiogram/OpenRouter/R2/publisher adapterlari va vaqtni freeze qilish. Testlar real secret/networkga bog'lanmasin.
3. Anonim, representativ fixture dataset yarat: Uzbek, Russian va aralash Telegram posts; albumlar; exact/ambiguous duplicates; daily/monthly/per-person/one-time narxlar; audience exclusionlar. Haqiqiy user PII yoki private channel media'sini repositoryga qo'shma.
4. Fixture factory/seed helpers yarat, lekin testlar bir-biriga state qoldirmasin. Migration apply, transaction rollback va cleanup strategiyasini tekshir.
5. Test markerlari va bajarish buyruqlarini rasmiylashtir: unit tez, integration local container/fake dependency bilan, E2E full fake stack bilan. Flaky testni retry bilan yashirma; root causeni tuzat.
6. Test matrix va local/CI bajarish bo'yicha qisqa docs yoz.

Qabul mezonlari:
- Test suite real external credentiallarsiz ishlaydi.
- Core flowlar uchun fixturelar mavjud va xavfsiz.
- Testlar isolationga ega va orderga bog'liq emas.
- Qaysi oqim qaysi test qatlamida qoplangani ko'rinadi.
```

## Prompt 7.2 - Core end-to-end regression suite

```text
Yuqoridagi umumiy kontekstga amal qil. Asosiy MVP user va data lifecyclelari uchun E2E regression suite'ni implement qil yoki yakunla.

Vazifa:
1. E2E scenario: ikki fake listener account -> album/media -> raw queue -> Pre-AI filter -> fake AI/R2 -> structured persistence -> post-AI dedup -> bitta canonical parent. Exact duplicate AI'ni skip qilishini va phone-only source yangi listing bo'lib qolishini tekshir.
2. E2E scenario: bot user register bo'ladi -> FSM search qiladi -> canonical resultni ko'radi -> saved filter yaratadi -> yangi matching listing notification worker orqali fake Telegramga yuboriladi -> retry second notification yaratmaydi.
3. E2E scenario: natural-language query -> typed criteria summary -> search result; malformed/failing LLM -> wizard fallback. Uzbek/Russian/mixed fixturelarni ishlat.
4. E2E scenario: referral link -> invitee first search/filter -> active referral -> 5th activation premium -> priority notification; source suggestion/admin approval va tag review flowining kamida bitta happy-path regressionini qo'sh.
5. Failure coverage qo'sh: Redis outage/recovery, OpenRouter/R2 failure, Telegram 429, account FloodWait va concurrent dedup. System observable failure statega o'tsin, silent data loss bo'lmasin.
6. Test failurelaridan kelib chiqqan real buglarni tuzat. Feature scope'ni kengaytirma.

Qabul mezonlari:
- Projectning asosiy va qiymat beruvchi oqimlari full fake stackda isbotlangan.
- Regression suite duplicate, retry va authorizationga bog'liq xatolarni tutadi.
- E2E testlar repeatable va aniq failure xabarlariga ega.
```

## Prompt 7.3 - Product metrics va observability implementation

```text
Yuqoridagi umumiy kontekstga amal qil. Beta qarorlarini qabul qilish uchun privacy-aware product metrics va technical observabilityni implement qil.

Vazifa:
1. Har metric uchun written specification yarat: event nomi, trigger, unique identifier, numerator/denominator, timezone, cohort, delay, dedup va missing-data policy. Activation, D7 retention, referral conversion va notification engagement barcha uchun bo'lsin.
2. Minimal immutable analytics event yoki aggregate modelni mavjud PostgreSQL architecturega mos qo'sh. Eventlar idempotency keyga ega bo'lsin, business transactiondan keyin xavfsiz yozilsin va failure main user flowni buzmasin.
3. Quyidagi eventlarni instrument qil: user registration, search completed, saved filter created, referral accepted/activated, notification sent, notification delivery failure, notification action/read. "Read"ni Telegramdan aniq olinmasa, uni read deb noto'g'ri yozma; action/click signalini alohida nomla.
4. Metrics query/service yoki protected dashboard endpoint yarat. U hourly/daily agregatlar, cohort calculation va date filteringni tushunarli beradi. PII minimization va retention siyosatini hujjatlashtir.
5. Technical metrics bilan bog'la: queue delay, AI fallback/validation failure, dedup rate, notification retry/error, listener health. Product metric va technical metricni bir nomga aralashtirma.
6. Time-frozen tests yoz: denominator correctness, repeated event dedup, D7 cohort boundary, referral activation, sent vs action engagement va timezone edge case.

Qabul mezonlari:
- To'rtta beta metric aniq formula hamda testlangan event sourcega ega.
- Metrikalar PII yig'ishni talab qilmaydi.
- Notification sent va user engagement chalkashmaydi.
- Dashboard/query authorization bilan himoyalangan.
```

## Prompt 7.4 - Beta/soft-launch readiness va rollout controls

```text
Yuqoridagi umumiy kontekstga amal qil. Real launchni amalga oshirmasdan, 50-100 userlik beta uchun release readiness va rollback controlsni tayyorla.

Vazifa:
1. Configuration/feature flagsni audit qil. Agar mavjud bo'lmasa, minimal, typed flag mechanism yarat: bot access/beta allowlist, listener source enablement, AI processing, notifications, NLP va content publishing alohida disable qilinishi mumkin bo'lsin. Flaglar auditli, default-safe va testlangan bo'lsin.
2. Beta cohort/allowlist policy yarat: explicit Telegram user IDs yoki admin-approved cohort; mavjud user accessini tasodifan o'zgartirma. No new real usersni qo'shma.
3. Launch checklist va rollback runbook yoz: migrations, backups, env validation, health/readiness, queue/DLQ, alert channel, source account health, feature flag rollback, incident owner va user-facing outage message.
4. Load/smoke testni representative fake load bilan bajar: queue backlog, worker concurrency, search latency va notification rate. Faqat o'lchangan natijani yoz; assumed capacity e'lon qilma.
5. Security/operational review o'tkaz: secret leak, admin authorization, session file permissions, log redaction, migration rollback, external dependency failure. Aniqlangan xavflarni prioritetlab, beta oldidan kerak bo'lganini tuzat.
6. Dry-run release procedure yoz va test environmentda bajar. Real production deploy/push/Telegram xabari yuborilmasin.

Qabul mezonlari:
- Beta access va xavfli featurelar tez qaytarib olinishi mumkin.
- Rollback va incident qadamlar yozma va dry-run bilan tekshirilgan.
- Real launch bo'lmagan holda beta readiness evidence mavjud.
- Known risks owner/mitigation bilan qayd qilingan.
```

## Prompt 7.5 - Final quality gate, bug fixes va MVP handoff

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 7 yakuniy quality gate'ni o'tkaz, faqat aniqlangan MVP buglarini tuzat va validation handoffini tayyorla.

Vazifa:
1. Unit, integration, E2E, migration va static quality checksni to'liq ishga tushir. Natijalarni test guruhi bo'yicha qayd qil; flaky yoki skipped testlarni yashirma.
2. Core acceptance criteria audit qil: 2-account ingestion, Pre-AI conservative dedup, required Vision, R2 media references, parent-only search, saved filters/notification idempotency, NLP fallback, referral premium, admin review, dynamic flat tags, content cap va beta metrics.
3. Faqat test/quality gate orqali topilgan bugs yoki hujjat-kod nomuvofiqliklarini tuzat. Arxitekturani qayta yozish, Mini App, payment yoki yangi featurelarni qo'shma.
4. `docs/`da MVP beta handoff yarat yoki yangila: supported workflows, feature flags, metric definitions, dashboard/query joyi, runbooks, known limitations va Sprint 7 exit criteria. Exit criteria ichida target sifatida notification engagement 20%+, D7 retention 30%+ va referral conversiondagi organik signal qayd qilinsin; bular hozirgi natija emas, validatsion threshold ekanini aniq yoz.
5. Qolgan release blockersni severity/owner bilan ro'yxatla. Agar blocker bo'lmasa, evidence bilan "beta-ready" tavsiyasini ber; real launchni o'zing amalga oshirma.

Qabul mezonlari:
- Natija test evidence va documented risklarga asoslangan.
- MVP featurelari bir-biriga regressiya qilmaydi.
- Beta uchun operation, monitoring va rollback handoff mavjud.
- Future roadmap featurelari scope'ga tortilmagan.
```
