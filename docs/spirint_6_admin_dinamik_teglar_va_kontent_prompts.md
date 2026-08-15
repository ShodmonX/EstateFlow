# Sprint 6 - Admin, Dinamik Teglar va Kanal Kontenti: AI Agent Promptlari

## Qollash tartibi

Promptlarni `6.1` dan `6.5` gacha ketma-ket yuboring. Bu sprintda admin interfeys minimal bo'lishi kerak: mavjud backend/bot konvensiyasiga tayaning, yangi alohida frontend yoki design systemni zaruratsiz boshlamang.

## Umumiy kontekst

```text
Sen EstateFlow loyihasining lead platform va operations muhandisisan. Sprint 6 maqsadi: foydalanuvchi manba takliflarini boshqarish, audience targetingni dynamic flat tag modeliga o'tkazish, minimal admin review imkonlarini berish va Telegram kanaliga avtomatik statistik/top-offer kontent yaratish.

Avval repository va docs/real_estate_sprint_plan.md, docs/real_estate_v3.md, docs/real_estate_changelog.md, docs/real estate.md ni o'qi. Qarorlar ustuvorligi: changelog > v3 > base document. Audience tags mutlaqo flat: `parent_tag_id` yo'q; `young_family` alohida tag emas, `family`ga normallashtiriladi. Amenities hozircha structured tag emas.

Admin actionlari auditli, authorization bilan himoyalangan va idempotent bo'lishi shart. Content automation kuniga 1-2 postdan oshmasin. Real Telegram channelga credential bo'lmasa post yuborma; fake publisher/dry-run bilan test qil.

Har task tugagach changed files, qarorlar, test natijalari va bloklovchilarni yoz.
```

## Prompt 6.1 - Source suggestion user flow va review service

```text
Yuqoridagi umumiy kontekstga amal qil. Foydalanuvchi mavjud bo'lmagan Telegram kanal/guruhni taklif qila oladigan sodda, auditli source suggestion flow'ni implement qil.

Vazifa:
1. Sprint 3 `source_suggestions` schema va Sprint 4 bot menu/authorizationni audit qil. Mavjud table/service contract bilan ishlagin; parallel data model yaratma.
2. Botda "Kanal taklif qilish" flow yarat: foydalanuvchi username yoki Telegram channel linkini yuboradi; value normalizatsiya/validationdan o'tadi; canonical identifierga ko'ra duplicate active/pending suggestion oldi olinadi.
3. Suggestionni `pending` holatida persist qil va admin notification/review queuega yubor. Userga submit qilingani, avtomatik qo'shilmasligi va ko'rib chiqilish holati haqida aniq xabar ber.
4. Review service/API yarat: admin only approve yoki reject qila olsin, reviewer/time/reason audit bo'lsin. Approve qilinganda source registryga safe, idempotent creation/enable handoff qilinsin; listener assignment faqat source valid bo'lgandan keyin yuz bersin.
5. Hujjatda belgilangan mukofot policy'ni implement qil: tasdiqlangan source suggestion uchun foydalanuvchiga 1 haftalik Premium. Retry/ikki admin action premiumni ikki marta bermasin; reject bo'lsa mukofot bo'lmasin.
6. Testlarda malformed link, duplicate suggestion, pending->approved, pending->rejected, unauthorized reviewer, source creation idempotency va premium rewardni qamra.

Qabul mezonlari:
- Foydalanuvchi suggest qila oladi, ammo manbani o'zi avtomatik qo'sha olmaydi.
- Faqat admin review state'ni o'zgartira oladi.
- Approve oqimi source registry va premium reward bilan exactly-once ishlaydi.
- Barcha review actionlari audit qilinadi.
```

## Prompt 6.2 - Dynamic flat audience tag lifecycle

```text
Yuqoridagi umumiy kontekstga amal qil. AI extractiondan admin approvalgacha dynamic audience tag lifecycle'ini implement qil.

Vazifa:
1. `audience_tag_types` schema va Sprint 2 extraction promptini audit qil. Final seedlar: `family`, `family_with_children`, `students`, `single_male`, `single_female`, `group_of_girls`, `group_of_boys`, `foreigners`; ularning hammasi `approved` va flat bo'lsin.
2. AI request promptiga faqat usage_count bo'yicha eng ko'p ishlatiladigan 10-15 ta approved tagni ber. Agar mos tag topilmasa, model snake_case candidate taklif qila olsin. Bu candidate raw AI outputdan keyin backend orqali tekshirilsin.
3. Fuzzy matching/synonym normalizatsiyasi yarat: masalan `yosh oila` va `young_family` mavjud `family`ga tushishi kerak. Aniq mavjud tag topilsa usage_count oshsin; to'g'ri o'xshashlik bo'lmasa yangi candidate `pending` holatda yaratiladi.
4. Pending taglarni user-facing search filterga kiritma. Pending/rejected taglarni announcementga bog'lashning aniq policy'sini yoz: canonical approved tagga map qil yoki audit metadata'da saqla, ammo user filter semanticsini buzma.
5. Admin merge/approve/reject service yarat. Merge keyin mavjud announcement/filter referencesni transactional canonical tagga o'tkazsin; hard delete bilan foreign keylarni buzma. Har action auditli va idempotent bo'lsin.
6. Testlarda seed selection limit, synonym family mapping, exact existing tag, distinct pending tag, rejected tag, merge references va filter result semanticsini tekshir.

Qabul mezonlari:
- Audience tag hierarchy'si yo'q.
- `young_family` alohida approved tagga aylanmaydi.
- AI prompt hajmi taglar soni o'sganda nazoratsiz oshmaydi.
- Pending taglar foydalanuvchi qidiruvini noto'g'ri o'zgartirmaydi.
```

## Prompt 6.3 - Minimal secure admin review surface

```text
Yuqoridagi umumiy kontekstga amal qil. Mavjud texnologiya va deploymentga mos minimal admin review surface'ni implement qil.

Vazifa:
1. Repositoryda mavjud admin UI, FastAPI auth va Telegram admin handlerlarini tekshir. Agar frontend yo'q bo'lsa, yangi katta web frontend boshlama; protected FastAPI admin endpoints va/yoki mavjud Telegram admin UI orqali minimal operatsion flow ber.
2. Quyidagi navbatlarni list/detail/action ko'rinishida ber: pending source suggestions, pending audience tags, Manual Review Queue'dagi low-confidence/possible-duplicate e'lonlar.
3. Har action uchun explicit authorization policy, input validation, optimistic concurrency yoki state precondition, reviewer ID/time/reason va immutable audit record bo'lsin. URL/callbackdagi IDni user input deb hisobla.
4. Manual review actionlari duplicate decision, parent merge va AI confidence workflow bilan mavjud Sprint 3 contracti orqali ishlasin. Admin endpoint handlerida business logicni qayta yozma.
5. Pagination, filtering, empty state va error handling qo'sh. PII/raw listing kontentini faqat zarur darajada va admin scope'ida ko'rsat; token/session/loglarni hech qachon chiqarmagin.
6. Authorization, state transition, concurrent action, audit log va queue pagination uchun test yoz.

Qabul mezonlari:
- Admin bo'lmagan user review actionni bajara olmaydi.
- Review actionlari existing domain service orqali o'tadi va auditda qayd qilinadi.
- Minimal UI/API operations uchun yetarli, lekin alohida katta frontend scope'i yo'q.
- Pending tag/source/manual-review queue'lari ko'rinadi va boshqariladi.
```

## Prompt 6.4 - Telegram kanal kontenti uchun scheduled jobs

```text
Yuqoridagi umumiy kontekstga amal qil. Telegram rasmiy kanaliga avtomatik statistik, top-offer va shaffoflik postlarini tayyorlaydigan scheduled content pipeline'ni implement qil.

Vazifa:
1. Mavjud worker/scheduler/queue infratuzilmasini tekshir va unga mos cron/scheduled job qo'sh. Yangi alohida scheduler yaratma, agar mavjud worker buni qo'llab-quvvatlasa.
2. Database query/service orqali quyidagi avtomatlashtiriladigan kontentni hosil qil: kunlik top takliflar, tuman bo'yicha bozor narxi statistikasi, shaffoflik hisoboti (qabul qilingan e'lonlar va duplicate count). Faqat active canonical listinglar ishtirok etsin.
3. Top-offer rankingni explainable va konservativ qil. Noto'g'ri ma'lumot, noaniq price yoki per-person/one-time qiymatlarini nojo'ya qiyoslash orqali "eng yaxshi" deb va'da qilma.
4. Publisher interface yarat: dry-run preview, actual publish, idempotency key, schedule timezone, failure retry va daily cap. Kuniga 1-2 avtomatik postdan oshmasin; duplicate schedule run ikki marta post qilmasin.
5. Post matni max Telegram length, source attribution, user privacy va live-link xavfsizligini hurmat qilsin. AI-generated maslahat/muvaffaqiyat hikoyalarini bu automationga kiritma.
6. Fake publisher bilan query/ranking, dry-run, one-time publish, duplicate schedule, cap enforcement va failure retry testlarini yoz.

Qabul mezonlari:
- Kontent faqat reliable canonical data asosida hosil bo'ladi.
- Bir scheduled item qayta ishlansa ikki marta post qilinmaydi.
- Daily cap test bilan cheklangan.
- Credential bo'lmasa dry-run/test rejimi ishlaydi.
```

## Prompt 6.5 - Sprint 6 integration va operations handoff

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 6dagi source suggestion, dynamic tags, admin workflows va content jobsni birga tekshir hamda operations handoffini tayyorla.

Vazifa:
1. Integration test yoz: user channel taklif qiladi -> admin approve qiladi -> source registryga bitta source qo'shiladi -> premium bitta marta beriladi.
2. Integration test yoz: AI yangi audience candidate qaytaradi -> fuzzy match yoki pending tag yaratiladi -> admin approve/merge/reject qiladi -> search filterlar faqat approved canonical tagdan foydalanadi.
3. Integration test yoz: Manual Review Queue admin actioni parent-child/dedup holatini domain service orqali xavfsiz yangilaydi; admin bo'lmagan user rad etiladi.
4. Content job preview -> publish flowini fake channel bilan tekshir; duplicate cron run va daily cap ishini isbotla.
5. Ops runbookda admin role provisioning, pending queue monitoring, failed publish retry, tag merge impacti va source approve rollback policy'sini yoz. Faqat topilgan buglarni tuzat.

Qabul mezonlari:
- Admin workflows end-to-end auditli va authorization bilan himoyalangan.
- Dynamic tag qarorlari user qidiruv semanticsini buzmaydi.
- Content automation observability va operational runbook bilan topshirilgan.
- Sprint 7 beta testlari uchun boshqaruv nazorati tayyor.
```
