# Sprint 3 - To'liq Deduplication va Database Finalizatsiyasi: AI Agent Promptlari

## Qollash tartibi

Promptlarni `3.1` dan `3.4` gacha tartib bilan yuboring. Bu sprint Sprint 1 Pre-AI filterini almashtirmaydi; u AI'dan keyingi, ko'p signalga asoslangan yakuniy deduplication hamda productionga tayyor schema'ni qo'shadi.

## Umumiy kontekst

```text
Sen EstateFlow loyihasining lead data/backend muhandisisan. Sprint 3 maqsadi: PostgreSQL schema va migratsiyalarni MVP uchun yakunlash, AI'dan keyingi weighted deduplication engine'ni yaratish va dublikatlarni parent-child modelida xavfsiz birlashtirish.

Avval repository va barcha docs fayllarini o'qi. Qarorlar ustuvorligi: docs/real_estate_changelog.md > docs/real_estate_v3.md > docs/real estate.md. Base hujjatdagi oldingi "Phone Number Match = 100" qoidasi bekor qilingan: v3.1 bo'yicha telefon weight=15 va hech qachon yolg'iz holda duplicate qarorini bermaydi.

Mavjud migrationlarni qayta yozma yoki production ma'lumotini yo'qotadigan change qilma. Har schema o'zgarishi Alembic yoki mavjud migration vositasi orqali, upgrade/downgrade va rollback risklari bilan bajarilsin. Search faqat parent/canonical announcementsni ko'rsatadi; child/source e'lonlari audit uchun saqlanadi.

Har task tugagach changed files, migration qarorlari, test natijalari va risklarni hisobot qil.
```

## Prompt 3.1 - Production-ready schema va safe migrations

```text
Yuqoridagi umumiy kontekstga amal qil. Mavjud database model va migrationlarini audit qilib, EstateFlow MVP uchun kerakli schema'ni backward-compatible tarzda yakunla.

Vazifa:
1. Current schema bilan docs talablarini diff qil. Agar Sprint 1-2 da vaqtinchalik jadvallar yaratilgan bo'lsa, ularni saqlagan holda evolyutsion migration yoz; table drop/recreate bilan data yo'qotma.
2. Core entitylar uchun required persistenceni yakunla: channels/sources, listener assignmentga kerakli metadata, announcements, announcement_media, users, user_filters, notifications, parsing_logs va processing_jobs. FK, UUID/BIGINT strategiyasi, timestamps, soft-delete/status va indexlar mavjud konvensiyaga mos bo'lsin.
3. `announcements`ga price_period, price_basis, price_normalized_monthly, audience_tags, audience_excluded_tags, parent relationship hamda kelajak uchun nullable `is_promoted`/`promoted_until` imkonini qo'sh. `listing_type`ni hujjat ochiq savol qoldirgani uchun bu sprintda o'zboshimchalik bilan ixtiro qilma.
4. `users`ga referral_code, referred_by, premium_until va active_referral_count; `source_suggestions` va `referral_events` jadvallarini qo'sh. Uniqueness va idempotency constraintlarini real business eventlarga mos belgilab qo'y.
5. Flat `audience_tag_types` jadvalini yarat: tag_key, display_name_uz, status, usage_count, timestamps. `parent_tag_id` qo'shma. Seed data faqat yakuniy flat ro'yxatga mos bo'lsin.
6. Search, dedup candidate query va foreign keylar uchun indexlar qo'sh. JSON/array index kerak bo'lsa PostgreSQL uchun asoslangan tanlov qil; over-indexing qilma.
7. Empty database upgrade, model metadata check va representative data migration testlarini yoz. Downgrade ma'lumot yo'qotishi mumkin bo'lsa uni xavfsiz chekla va runbookda qayd qil.

Qabul mezonlari:
- Latest migration clean database'da ishlaydi.
- Changelogdagi v3.2-v3.6 schema qarorlari aks etgan.
- Audience tags hierarchy'siz.
- Mavjud data va migration tarixini buzadigan destructive shortcut yo'q.
```

## Prompt 3.2 - Post-AI weighted deduplication engine

```text
Yuqoridagi umumiy kontekstga amal qil. Structured announcementlar uchun extensible, conservative weighted deduplication engine'ni implement qil.

Vazifa:
1. Candidate selectionni alohida repository/service qatlamiga ajrat. U source URL, image pHash, district, rooms, area, price va vaqt kabi arzon indexlangan signal bilan potensial nomzodlarni cheklasin; barcha announcementlarni O(n^2) solishtirma.
2. Har signal uchun alohida matcher va explainable score breakdown yarat. Base weightsni configda saqla: source URL 100, same image pHash 70, district 10, rooms 10, area difference <3 m2 10, price difference <5% 10, floor 5, similar address 15, phone 15. Telefon match faqat boshqa mustaqil signal bilan birga hisobga olinsin; phone-only score 15 bo'lib `new` qarorini berishi kerak.
3. Thresholdlarni configga chiqar: 100+ exact duplicate, 80-99 high-confidence duplicate, 60-79 possible duplicate/manual review, 60 dan pasti new. Score xavfsizligi uchun direct source/forward provenance matchini explicit exact signal sifatida qo'lla.
4. Price signalida `price_period` va `price_basis`ni hurmat qil: one-time, daily/monthly va per-person qiymatlarni noto'g'ri taqqoslab score oshirma. Missing data signal bermasin.
5. Engine duplicate qarori bilan birga score breakdown, threshold, candidate ID va versionlangan config reference qaytarsin. Bu audit, admin review va tuning uchun persist qilinsin.
6. Unit testlarda: same-phone-different-apartments, same-image-plus-phone, exact source URL, address similarity, per-person price mismatch, possible duplicate va no-candidate holatlarini tekshir.

Qabul mezonlari:
- Telefon hech qachon yakka duplicate sababi bo'lmaydi.
- Score qarori explainable va reproductionga yaroqli.
- Candidate querylar indexdan foydalanadigan, cheklangan bo'ladi.
- Possible duplicate avtomatik parent merge qilinmaydi.
```

## Prompt 3.3 - Parent-child linking va canonical merge

```text
Yuqoridagi umumiy kontekstga amal qil. Dedup decision'ni persistent parent-child modelga aylantir va user-facing canonical announcementni xavfsiz boshqar.

Vazifa:
1. Parent selection policy yarat. Default sifatida data completeness, trusted source score va first-seen tartibini deterministik ranking bilan qo'lla; policy config orqali almashtirilishi mumkin bo'lsin.
2. High-confidence/exact duplicate aniqlansa, yangi source announcementni mavjud parentga bog'la. Child e'lon va uning raw/source metadata'sini o'chirma; parentga source count, latest source va kerakli aggregate metadata'ni transactional yangila.
3. Parent va child orasidagi media, structured field va source link collisionlarini aniq hal qil. Untrusted child ma'lumot parentning yaxshi qiymatini ko'r-ko'rona almashtirmasin; merge conflict uchun audit trail bo'lsin.
4. Search repository contractini yangila: default query faqat active parent/canonical e'lonlarni qaytaradi. Admin/audit query childlarni ko'ra olishi mumkin, ammo user-facing natijada bitta obyekt ikki marta chiqmasin.
5. Concurrency xavfini hal qil: bir xil e'lon parallel workerlar tomonidan ishlansa, ikki parent yoki inconsistent link hosil bo'lmasin. Transaction, row lock yoki unique invariantdan asosli foydalan.
6. Test yoz: bir parentga bir necha channel childlari, parallel duplicate, richer-child data, parent selection determinism va user search queryning deduped natijasi.

Qabul mezonlari:
- Dublikatlar fizik o'chirilmaydi, parentga bog'lanadi.
- Parent tanlovi deterministik va audit qilinadi.
- Parallel processing duplicate parent hosil qilmaydi.
- Search defaultida childlar ko'rinmaydi.
```

## Prompt 3.4 - Manual review, source expansion va Sprint 3 verification

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 3ning yakuniy sifat qatlamini qo'sh va 3-5 manbaga kengayishga tayyorligini tekshir.

Vazifa:
1. Possible duplicate, low AI confidence va schema valid bo'lsa ham business quality past holatlar uchun Manual Review Queue model/service yarat. Itemga reason, score breakdown, related candidate va status (pending/approved/rejected/merged) kerak bo'lsin.
2. Admin actionlari keyinchalik Sprint 6 paneliga ulanadigan, idempotent service API orqali bajarilsin. Review qarori duplicate relation yoki canonical data'ni xavfsiz yangilasun va audit log yozsin.
3. Source registry/assignmentni 3-5 test source bilan tekshir. Bir xil obyekt turli source formatida kelganda post-AI dedup to'g'ri ishlashini integration testda isbotla.
4. Performance regressiondan saqla: candidate selection, migrations va parent link querylari uchun query-count yoki explain-based test/tekshiruv qo'sh. MVP darajasida qabul qilinadigan chegarani runbookda yoz.
5. Sprint 1 Pre-AI filter, Sprint 2 AI worker va yangi dedup engine bilan end-to-end test yoz: exact raw duplicate skip; ambiguous raw duplicate AIga o'tishi; post-AI multi-signal duplicate parentga birikishi; phone-only e'lon new bo'lishi.
6. Dedup score configi, manual review statuses va canonical query semanticsini `docs/`da hujjatlashtir.

Qabul mezonlari:
- 3-5 source uchun full flow testlangan.
- Ambiguous holat auditli manual reviewga tushadi.
- Oldingi Pre-AI konservativ qoidalar buzilmagan.
- Sprint 4 Search Engine yagona canonical result contractiga tayyor.
```
