# Sprint 2 - AI Processing Pipeline va Rasm Saqlash: AI Agent Promptlari

## Qollash tartibi

Promptlarni `2.1` dan `2.4` gacha ketma-ket yuboring. Sprint 1 raw queue contracti o'zgarmas input hisoblanadi. Agar contractni o'zgartirish zarur bo'lsa, faqat backward-compatible schema version bilan o'zgartiring va Sprint 1 testlarini ham yangilang.

## Umumiy kontekst

```text
Sen EstateFlow loyihasining lead AI platforma muhandisisan. Sprint 2 maqsadi: Telegramdan kelgan raw e'lon va rasmlarni AI yordamida validatsiyalangan strukturalangan ma'lumotga aylantirish, Cloudflare R2-compatible storagega saqlash va post-AI deduplicationga uzatish.

Avval repository hamda docs/real_estate_sprint_plan.md, docs/real_estate_v3.md, docs/real_estate_changelog.md va docs/real estate.md ni o'qi. Qarorlar ustuvorligi: changelog > v3 > base document. Xususan, changelog v3.2-v3.6 qoidalari majburiy: price_period/price_basis, flat audience tags, R2, barcha non-near-duplicate rasmlarni saqlash va renovation signalini text yoki Vision orqali validated saqlash.

OpenRouter model ketma-ketligi konfiguratsiyadan boshqariladi: primary `google/gemini-3.1-flash-lite`, fallback `google/gemini-3.1-flash-lite-preview`. OpenRouter katalogida plain `google/gemini-3.1-flash` hozir mavjud emas; Pro modellar ishlatilmaydi. Model IDlari kodga qattiq bog'lanmasin, lekin shu defaultlar `.env.example`da ko'rsatilishi mumkin. API key, raw PII va rasm URL tokenlari logga chiqmasin.

Mavjud o'zgarishlarni saqla. Real OpenRouter/R2 credentiali bo'lmasa, contract fake yoki mock bilan test qil. Har task yakunida changed files, qarorlar, test natijalari va bloklovchilarni yoz.
```

## Prompt 2.1 - OpenRouter client va fallback policy

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 2 uchun OpenRouter orqali ishlaydigan async, provider-agnostic LLM clientni implement qil.

Vazifa:
1. Sprint 1 raw event contracti va Sprint 0 config/logging interfeyslarini tekshir. LLM serviceni worker, Telegram adapter va endpointlardan mustaqil service/interface sifatida loyihala.
2. OpenRouter-compatible async HTTP client yarat. Timeouts, retryable/non-retryable HTTP xatolari, rate limit va cancellation semantikasini aniq ajrat. API key faqat typed settingsdan olinadi.
3. Model strategy'ni configga chiqar: standart e'lon parsing va natural-language query uchun primary Gemini 3.1 Flash-Lite; primary xato yoki low confidence qaytarsa Gemini 3.1 Flash-Lite Preview. Transport-level fallback va application-level low-confidence retry qaysi bosqichda ishlashini aniq yoz.
4. Har so'rov uchun request ID/correlation ID, model, fallback bosqichi, latency, token/cost metadata mavjud bo'lsa ularni log/audit metricsga yubor. Prompt, raw listing, phone yoki authorization headerlarni log qilma.
5. Provider response formatini kichik adapterga ajrat va structured JSON so'rash mexanizmini tayyorla. Malformed response alohida typed error bo'lsin; silent fallback yoki silent data loss bo'lmasin.
6. Fake transport bilan success, timeout, 429, malformed response, primary-to-fallback, low-confidence final retry va all-models-failed testlarini yoz.

Qabul mezonlari:
- Model almashtirish faqat config bilan bajariladi.
- Barcha model urinishlari tugasa caller aniq typed xato oladi.
- Xatolik yo'lida secret yoki raw listing log qilinmaydi.
- Fallback ketma-ketligi unit testlarda tekshirilgan.
```

## Prompt 2.2 - Structured extraction schema, prompt va normalization

```text
Yuqoridagi umumiy kontekstga amal qil. Ko'chmas mulk e'lonini chiqarish uchun versionlangan prompt, strict JSON schema va deterministic normalization qatlamini implement qil.

Vazifa:
1. Existing model/schema konvensiyalarini tekshir va Pydantic v2 yoki tanlangan validation qatlamida AI output schema yarat. Schema raw outputdan alohida bo'lsin; invalid yoki extra fieldlar aniq qayd qilinsin.
2. Quyidagi majburiy yangi narx qoidalarini qo'lla: `price_period` faqat `daily`, `monthly`, `one_time`; `price_basis` faqat `total`, `per_person`; daily qiymat uchun `price_normalized_monthly = price * 30`; one_time uchun monthly normalizatsiya bo'sh; per_person narxni umumiy narxga avtomatik aylantirma. Matnda aniq ko'rsatma bo'lmasa hujjatdagi xavfsiz defaultlarni qo'lla va taxmin qilingan joyni confidence/metadata bilan ajrat.
3. Audience targeting uchun hozircha fixed seed, flat tag ro'yxatidan foydalan: `family`, `family_with_children`, `students`, `single_male`, `single_female`, `group_of_girls`, `group_of_boys`, `foreigners`. `young_family`ni alohida tag sifatida qaytarma, `family`ga normallashtir. Matnda aniq restriction bo'lmasa taglarni bo'sh qoldir; amenities'ni alohida structure qilma, descriptionda saqla.
4. Uzbek, Russian va aralash matn uchun prompt yoz. Prompt outputni faqat valid JSON qilib chekla, prompt injectionga qarshi xom e'lonni untrusted data sifatida ajrat, va prompt versionini metadata'da saqla.
5. Currency, phone, district, whitespace hamda enum qiymatlari uchun AI'dan keyingi deterministic normalizer yoz. Normalizer model taxminini yashirmasin yoki yangi biznes ma'lumotini to'qimasi.
6. Fixturelar bilan valid Uzbek/Russian listings, daily/per-person price, sale listing, no-price, audience restriction, invalid JSON va prompt injection matniga test yoz.

Qabul mezonlari:
- Downstream kod faqat validationdan o'tgan canonical schema bilan ishlaydi.
- `price_normalized_monthly` qoidasi test bilan qoplangan.
- Audience tags flat; `parent_tag_id` yoki hierarchy kiritilmaydi.
- Qulayliklar alohida enum/tag modeliga aylantirilmaydi.
```

## Prompt 2.3 - Media processing, pHash va Cloudflare R2 storage

```text
Yuqoridagi umumiy kontekstga amal qil. Raw eventdagi media'ni xavfsiz tayyorlash, near-duplicate filtrlash va R2-compatible object storagega saqlashni implement qil.

Vazifa:
1. Media downloader/reference resolver interfeysini listenerdan ajrat. Fayl turi, hajmi va rasm decode xavfsizligini tekshir; buzilgan yoki ruxsat etilmagan fayl AI workerini yiqitmasin.
2. Rasmni AI va storage uchun o'lcham/quality bo'yicha siq. Hujjatdagi taxminan 800x600 yo'nalishini config orqali boshqar, original aspect ratio va orientatsiyani saqla. Rasm sonini sun'iy 3-4 taga cheklama.
3. Bitta e'lon ichida pHash orqali near-duplicate rasmlarni aniqlab, faqat bitta nusxasini qoldir. Threshold configuratsiyali bo'lsin. Rasmning pHash-only o'xshashligi e'lon duplicate qarori emasligini saqla.
4. S3-compatible storage client yarat va Cloudflare R2 endpoint/credentials/configini env orqali ol. Object key namlash collision-safe va traceable bo'lsin; public/private URL siyosatini explict qil. `announcement_media.storage_url` yoki ekvivalent persistent reference faqat muvaffaqiyatli upload'dan keyin yozilsin.
5. Upload xato bo'lsa retryable va permanent failurelarni farqla; orphan object/temporary file cleanup strategiyasini yoz. R2 credentiali, signed URL signature'i va raw local path logga chiqmasin.
6. Fake S3 client va test rasm fixturelari bilan compression, near-duplicate removal, successful upload, upload failure, cleanup va metadata persistence testlarini yoz.

Qabul mezonlari:
- Near-duplicate bo'lmagan barcha rasm saqlanadi va pipeline'ga beriladi.
- Storage URL faqat tasdiqlangan uploaddan keyin persist qilinadi.
- Rasm metadata va pHash keyingi deduplicationga uzatiladi.
- R2siz test suite ishlaydi.
```

## Prompt 2.4 - AI worker orchestration, persistence va handoff

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 2 komponentlarini raw queue consumeridan post-AI dedup handoffigacha birlashtir.

Vazifa:
1. Idempotent AI worker yarat: raw eventni oladi, media'ni tayyorlaydi/saqlaydi, structured extraction qiladi, schema validation va deterministic normalizationdan o'tkazadi, processing status/audit ma'lumotini persist qiladi.
2. Agar e'londa kamida bitta rasm bo'lsa, `renovation_level` uchun Vision qo'shimcha signal sifatida ishlashi mumkin. Matn aniq va valid bo'lsa, shu qiymatni saqlashga ruxsat ber; rasm bo'lmasa renovation `null` yoki low-confidence text-derived bo'lishi mumkinligini schema/metadata bilan aniq ajrat.
3. Confidence policy'ni implement qil: valid schema bo'lishi yakuniy business confidence degani emas. Past confidence, malformed output yoki barcha model failurelari retry/manual reviewga yoki DLQ-ready holatga aniq yo'naltirilsin; xabar jim yo'qolmasin.
4. Persisted announcement/media/parsing-log holatlari tranzaksion va idempotent bo'lsin. Worker qayta ishlasa duplicate announcement/media yozilmasin. Post-AI dedup queuega faqat successful canonical extraction yuborilsin.
5. Ops eventlarni AI latency, fallback, validation failure, storage failure va manual-review holatlari uchun yubor. PII yoki raw prompt yuborilmasin.
6. Integration test yoz: raw event -> media -> fake R2 -> fake LLM -> validation/normalization -> persistence -> post-AI queue. Renovation text/Vision signal mosligi, daily price normalizatsiyasi, invalid JSON va retry holatlarini qamra.
7. `docs/`da Sprint 3 uchun structured announcement va media handoff contractini yoz: schema version, idempotency, confidence, duplicate signal maydonlari va failure statuslari.

Qabul mezonlari:
- Worker transaction-safe va qayta ishga tushirishga chidamli.
- Rasmli e'lonlarda Vision majburiy test bilan isbotlangan.
- Failed processing observable holatga ega; data silent yo'qolmaydi.
- Sprint 3 post-AI dedup engine'iga aniq contract mavjud.
```
