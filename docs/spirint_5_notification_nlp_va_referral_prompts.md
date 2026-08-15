# Sprint 5 - Notification, NLP va Referral: AI Agent Promptlari

## Qollash tartibi

Promptlarni `5.1` dan `5.5` gacha tartibda bajartiring. Sprint 4 saved filter schema'si bu sprint uchun single source of truth; unga parallel, boshqa filter formatini yaratmang.

## Umumiy kontekst

```text
Sen EstateFlow loyihasining lead product/platform muhandisisan. Sprint 5 maqsadi: canonical e'lonlar uchun ishonchli notification oqimi, Uzbek/Russian/aralash natural-language search va 5 ta faol taklifga asoslangan referral Premium tizimini yaratish.

Avval repository va docs/real_estate_sprint_plan.md, docs/real_estate_v3.md, docs/real_estate_changelog.md, docs/real estate.md ni o'qi. Qarorlar ustuvorligi: changelog > v3 > base document. Premium foydalanuvchiga notification queue'da ustuvorlik beriladi; referral faqat taklif qilingan user kamida bitta qidiruv yoki saved filter yaratgandan keyin faol bo'ladi.

Notificationlar parent/canonical listing asosida, idempotent va userga spam bermaydigan bo'lishi kerak. NLP faqat Search servicega typed criteria beradi; LLM hech qachon bevosita SQL yoki authorization qarori chiqarmaydi. Telegram, OpenRouter va queue testlari fake adapterlar bilan bajarilishi mumkin.

Har task tugagach changed files, qarorlar, test natijalari va bloklovchilarni yoz.
```

## Prompt 5.1 - Filter matching va notification job yaratish

```text
Yuqoridagi umumiy kontekstga amal qil. Yangi canonical announcement paydo bo'lganda saved filterlarni topadigan va idempotent notification jobs yaratadigan matching engine'ni implement qil.

Vazifa:
1. Sprint 4 SearchCriteria va saved filter contractini qayta ishlat. Notification matching natijasi user qidiruvidagi filter semanticsidan farq qilmasin: price period/basis, district, rooms, renovation, flat audience tags va exclusionlar bir xil ishlasin.
2. Trigger faqat announcement canonical parent sifatida persisted bo'lgandan keyin ishlasin. Child duplicate, deleted/inactive listing yoki manual reviewda turgan listing user notification yubormasin.
3. Har `(user_id, filter_id, canonical_announcement_id)` uchun notification idempotency constraint yarat. Worker retry yoki parentga yangi child kelishi bir xil e'lonni qayta yubormasin.
4. Premium userlarning jobini high-priority queuega, boshqalarni standard queuega yubor. Priority qoidasi fairness/rate limitni chetlab o'tmasin.
5. User notification settings, disabled filter, stale filter va matching error holatlarini xavfsiz boshqar. Match metrics, queue delay va skipped reasons uchun structured Ops event/metric chiqar.
6. Unit/integration testlarda normal matching, audience exclusion, disabled filter, duplicate child, retry idempotency, premium priority va no-matchni tekshir.

Qabul mezonlari:
- Match qoidalari Search Engine bilan bir xil.
- Bitta filter/listing juftligi ko'pi bilan bitta pending/sent notificationga ega.
- Canonical duplicate relation userga notification spam bermaydi.
- Premium priority test bilan isbotlangan.
```

## Prompt 5.2 - Notification delivery worker va Telegram UX

```text
Yuqoridagi umumiy kontekstga amal qil. Notification joblarini Telegramga yetkazadigan chidamli delivery worker va foydalanuvchi xabar formatini implement qil.

Vazifa:
1. Queue consumer/job lifecycle'ni yarat: pending -> sending -> sent yoki retryable/permanent failure. Telegram API errorlari, rate limit va user botni bloklagan holatni aniq ajrat.
2. Message formatini canonical listingga bog'la: qisqa title, narx period/basis, tuman/xona/remont, sabab bo'lgan saved filter va detail/search action. Child source ma'lumotlari yoki noaniq sensitive fieldlarni ortiqcha chiqarma.
3. Telegram deliveryni idempotent qil. Worker crash yoki timeoutdan keyin xabar duplicate bo'lishi mumkin bo'lgan xavfni minimallashtir; final state va delivery attempt auditini saqla.
4. User va Telegram rate limitlarini qo'lla. Premium priority yuqori bo'lsa ham flood limitni buzmasin. Bulk failurelar Ops notifierga aggregate alert bo'lib chiqsin.
5. Notification state, click/read signalini kelajakdagi metric uchun capture qilishga tayyor action/callback contract yarat; bu signal yetkazib berilganini soxta isbotlamasin.
6. Fake Telegram bot bilan success, 429 retry, blocked user, duplicate retry, priority order va bulk error aggregation testlarini yoz.

Qabul mezonlari:
- Notification delivery holati audit qilinadi va retry-safe.
- User botni bloklasa queue cheksiz retry qilmaydi.
- Telegram rate limits respected.
- Keyingi Sprint 7 engagement metriclari uchun minimal event contract mavjud.
```

## Prompt 5.3 - Natural-language search input layer

```text
Yuqoridagi umumiy kontekstga amal qil. Botdagi erkin matn qidiruvini mavjud Search service ustidagi xavfsiz AI input layer sifatida implement qil.

Vazifa:
1. Bot UXda FSM wizardga parallel "erkin matnda qidirish" entry pointini qo'sh. Input maksimal 140 belgi bo'lsin; Uzbek, Russian va aralash matnni qabul qil. Long/empty input uchun aniq feedback ber.
2. LLMga faqat typed filter extraction schema so'ra: monthly budget, price basis preference, districtlar, rooms, renovation, audience tag va tan olinmagan qo'shimcha shartlar. LLM outputi strict validationdan o'tsin.
3. Extraction modeli Sprint 2 OpenRouter policy'dan foydalansin, ammo model xatosi yoki invalid JSONda userga wizard fallback taklif qil. Hech qachon LLM outputini to'g'ridan-to'g'ri SQL/ORM queryga yoki callbackga uzatma.
4. `young family`/`yosh oila`ni `family`ga normallashtir; flat audience tag qoidasini saqla. `metroga yaqin` kabi hozircha strukturaviy search maydoni bo'lmagan talabni foydalanuvchiga yashirmay, `unapplied_conditions` sifatida ko'rsat yoki xavfsiz ignore qil.
5. Foydalanuvchiga AI tomonidan tushunilgan filterlar summarysini ko'rsat va tasdiqlash/tahrirlash imkonini ber, keyin Search servicega uzat. Qidiruv qilingan criteria saved filter sifatida saqlanishga mos bo'lsin.
6. Testlarda Uzbek, Russian, mixed input, 140-char limit, invalid model JSON, unmodelled condition, audience normalization va wizard fallbackni qamra.

Qabul mezonlari:
- NLP Search yangi query engine emas, mavjud Search service input layeridir.
- LLM failure user qidiruvini butunlay to'xtatmaydi.
- Qo'llanmagan shartlar noto'g'ri va'da sifatida qo'llanilgandek ko'rsatilmaydi.
- Typed criteria xavfsiz validatsiyadan o'tadi.
```

## Prompt 5.4 - Referral va Premium activation domain

```text
Yuqoridagi umumiy kontekstga amal qil. Referral deep link, faol taklifni hisoblash va Premium activation uchun idempotent domain serviceni implement qil.

Vazifa:
1. Har user uchun collision-safe referral code/deep link yarat: Telegram start parameter formatini validatsiya qil. Self-referral, malformed code, bir userning qayta referral bilan biriktirilishi va circular referrallarni rad et.
2. Yangi user referral link bilan kirganda referral eventni bir marta persist qil. `referred_by` qiymati immutable yoki aniq policy bilan o'zgartiriladigan bo'lsin; replayed `/start` event countni oshirmasin.
3. Referred user birinchi marta qidiruv yoki saved filter yaratganda shu referral eventni active qil. Bu transition exactly-once bo'lsin va referrerning active_referral_count'ini transaction ichida oshirsin.
4. 5 ta faol taklifga yetganda 7 kunlik Premiumni avtomatik ber. Mavjud premium bo'lsa, muddati yo'qolmasligi uchun documented extension policy qo'lla; bir milestone/retry premiumni qayta-qayta bermasin. Referrerga tabrik notification yubor.
5. Basic abuse protections qo'sh: self-referral, duplicate Telegram user ID, repeated activation, rate limiting/audit. Murakkab device fingerprint yoki payment fraud mexanizmlarini bu sprintga kiritma.
6. Testlarda deep link parsing, self referral, concurrent activations, five-referral threshold, retry idempotency, existing premium extension va notificationni tekshir.

Qabul mezonlari:
- Referral activation faqat haqiqiy qidiruv/filter actionidan keyin bo'ladi.
- Bitta referral user countni bir marta oshiradi.
- 5-lik milestone premiumni aniq bir marta beradi.
- Premium notification priority bilan to'g'ri integratsiyalashadi.
```

## Prompt 5.5 - Sprint 5 end-to-end verification

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 5 featurelarini birlashtirib, regression va handoff sifat tekshiruvini bajar.

Vazifa:
1. E2E test yoz: user saved filter yaratadi -> matching canonical listing persist bo'ladi -> priorityga mos notification job yaratiladi -> fake Telegram delivery sodir bo'ladi -> retry duplicate yubormaydi.
2. Alohida E2E test yoz: user referral deep link orqali kiradi -> qidiruv yaratadi -> referral active bo'ladi -> beshinchi active referral premium beradi -> keyingi matching notification high priorityga tushadi.
3. NLP test yoz: erkin matn valid criteria'ga aylanadi -> user summaryni tasdiqlaydi -> Search service canonical results qaytaradi; model failure wizard fallbackiga o'tadi.
4. Cross-feature authorization, log redaction, metrics events va queue failurelar bo'yicha regression tekshiruv o'tkaz. Faqat aniqlangan buglarni tuzat.
5. `docs/`da notification idempotency keyi, referral milestone policy va NLP extraction contractini qayd qil. Sprint 6 Admin review/source suggestion uchun kerakli service APIlarni ko'rsat.

Qabul mezonlari:
- Notification, NLP va referral bir-birining contractini buzmaydi.
- E2E testlar real Telegram/OpenRouter credentialsiz ishlaydi.
- Sprint 6 admin workflows uchun auditli data va service boundarylar tayyor.
```
