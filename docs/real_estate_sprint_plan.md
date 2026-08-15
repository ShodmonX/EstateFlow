# Real Estate Super Aggregator — MVP Sprint Rejasi

**Hujjat versiyasi:** 1.0
**Bog'liq hujjatlar:** `real_estate_v3.md` (v3.1) + `real_estate_changelog.md` (v3.2–v3.6)
**Sana:** 2026-07-30

## Taxminlar

- Sprint uzunligi: **2 hafta**.
- Jamoa hajmi: kichik (1-3 dasturchi) deb taxmin qilingan. Agar jamoa boshqacha bo'lsa, sprint davomiyligi shunga mos ravishda o'zgartirilishi kerak.
- Har bir sprint oxirida **ishlaydigan, sinovdan o'tkazilishi mumkin bo'lgan** natija bo'lishi kerak (incremental delivery), "hammasi oxirida birlashadi" tarzida emas.
- Kontent strategiyasi (3.3-bo'lim, kanal postlari) va Ops monitoring kabi past-texnik-risk ishlar imkon qadar erta boshlanadi, chunki ular boshqa qismlarni debugging qilishda foydali bo'ladi.

---

## Sprint 0 — Infratuzilma va Fundament (1 hafta)

**Maqsad:** Loyihaning "skeleton"ini tayyorlash, birinchi haqiqiy funksional kod yozilishidan oldin.

- Repository tuzilishi (v2.0, 5-bo'lim, Project Structure asosida).
- Docker Compose: PostgreSQL, Redis konteynerlari.
- `.env` konfiguratsiya tuzilmasi (v2.0, 18.6-bo'lim).
- **Operatsion Monitoring Bot + Kanal** (v3.1, 10A-bo'lim) — birinchi navbatda ishga tushiriladi, chunki bu boshqa barcha sprintlarda debugging uchun foydali bo'ladi va o'zi juda arzon/tez amalga oshadi.
- Structured Logging asosiy infratuzilmasi (v2.0, 17.9-bo'lim).

**Natija:** Bo'sh, lekin ishlaydigan infratuzilma + Ops kanali faol.

---

## Sprint 1 — Listener va Ingestion (2 hafta)

**Maqsad:** Telegram'dan xom ma'lumot Queue'ga tushishi.

- **Listener Account Pool** (v3.1, FR-054, 10B-bo'lim) — MVP uchun kamida **2 ta** Telethon akkount bilan boshlanadi (yagona nuqta nosozligi xavfini kamaytirish uchun).
- Channel Assignment Service (oddiy versiya — kanal manbalarini akkountlar orasida taqsimlash).
- 1-2 ta test kanal uchun Adapter (v2.0, 7.9-bo'lim).
- Telethon Listener → Media Buffer → Debounce → Redis Queue oqimi (v2.0, 7.4–7.12-bo'limlar).
- **Pre-AI Deduplication Filter — AI'siz qismlar** (v3.1, 6-bo'lim, telefon signali v3.1'da tuzatilgan holatda):
  - Forward metadata tekshiruvi
  - Matn hash / near-duplicate moslashtirish
  - Telefon regex ajratib olish (faqat yordamchi signal sifatida, weight=15)
  - Rasm pHash hisoblash

**Natija:** Xabarlar 2 ta akkount orqali qabul qilinadi, aniq duplikatlar AI'ga yuborilmasdan chetlab o'tiladi, qolganlari Queue'da kutadi.

---

## Sprint 2 — AI Processing Pipeline va Rasm Saqlash (2 hafta)

**Maqsad:** Xom matn/rasm struktura JSON'ga aylanadi va saqlanadi.

- OpenRouter integratsiyasi: **Gemini 3.1 Flash-Lite** (asosiy) → **Gemini 3.1 Flash-Lite Preview** (fallback); Pro modellar ishlatilmaydi.
- Standard JSON Schema kengaytirilgan holda amalga oshiriladi:
  - `price_period` / `price_basis` / `price_normalized_monthly` (v3.2)
  - `renovation_level` uchun Vision qo'llab-quvvatlanadi, lekin text-derived qiymat ham
    saqlanadi; manual review faqat confidence/business check asosida ishlaydi
  - `audience_tags` / `audience_excluded_tags` — boshlang'ich bosqichda **oddiy fixed seed ro'yxat** bilan (dinamik enum jadvali Sprint 6'da qo'shiladi, hozircha soddalashtirilgan versiya yetarli)
- Rasm ishlov berish: siqish (kichraytirish) + near-duplicate pHash filtri (v3.5) — **rasm soni sun'iy cheklanmaydi**, faqat near-duplicate'lar olib tashlanadi.
- **Cloudflare R2** (S3-compatible) integratsiyasi — rasm yuklash va `storage_url` saqlash (v3.5).
- AI Worker: Prompt Building → LLM → JSON Validation → Normalization → Confidence Check (v2.0, 9.2-bo'lim).

**Natija:** Bitta test kanaldan kelgan e'lonlar to'liq struktura holatda, rasmlari R2'da saqlangan holda bazaga yoziladi.

---

## Sprint 3 — To'liq Deduplication Engine va Database Finalizatsiyasi (2 hafta)

**Maqsad:** AI'dan keyingi to'liq duplicate aniqlash va bazani production-ready holatga keltirish.

- To'liq Deduplication Engine: Weighted Scoring Strategy (v2.0, 10.11-bo'lim, **telefon weight=15 ga tuzatilgan holda**, v3.1).
- Parent-Child (Parent Announcement) bog'lash mantig'i (v2.0, 10.13-bo'lim).
- Database sxemasini to'liq shakllantirish — barcha yangi jadval/ustunlarni qo'shish:
  - `users` kengaytmasi (referral_code, referred_by, premium_until, active_referral_count)
  - `source_suggestions`, `referral_events` jadvallari
  - `announcements` kengaytmasi (price_period, price_basis, price_normalized_monthly, audience_tags, audience_excluded_tags, is_promoted/promoted_until — kelajak uchun bo'sh)
- Manba sonini **3-5 ta kanalgacha** kengaytirish.

**Natija:** Bir nechta kanaldan kelgan bir xil kvartiralar to'g'ri birlashtiriladi, baza sxemasi to'liq tayyor.

---

## Sprint 4 — Search Engine va Bot Asosi (2 hafta)

**Maqsad:** Foydalanuvchi birinchi marta botda qidiruv qila oladi.

- Search Engine: asosiy filtrlar (narx — `price_normalized_monthly` orqali, tuman, xona, remont) + auditoriya filtri (v3.1, 7A.6-bo'lim, soddalashtirilgan flat mantiq, v3.4).
- Telegram Bot — asosiy qism:
  - Asosiy menyu (v2.0, 13.7-bo'lim)
  - Bosqichma-bosqich qidiruv (FSM)
  - Saqlangan Filtrlar (Create/View/Update/Delete)

**Natija:** Foydalanuvchi botda real ma'lumot bo'yicha qidiruv qila oladi va filtr saqlay oladi.

---

## Sprint 5 — Notification, Tabiiy Tilda Qidiruv, Referral (2 hafta)

**Maqsad:** Bildirishnoma tizimi va o'sish mexanizmlari ishga tushadi.

- Notification Worker: Filter Matching → Notification Queue → Telegram Delivery (v2.0, 12-bo'lim).
- **Tabiiy Tilda Qidiruv** (v3.1, 7-bo'lim) — botga qo'shiladi, ikkinchi variant sifatida (FSM bilan bir qatorda).
- **Referral tizimi** (v3.1, FR-047, 9.2-bo'lim): noyob referral havola, faol taklif kuzatuvi, 5 ta taklifda avtomatik Premium faollashtirish.

**Natija:** Foydalanuvchi bildirishnoma oladi, erkin matnda qidira oladi, do'stlarini taklif qila oladi.

---

## Sprint 6 — Admin Funksiyalari, Dinamik Auditoriya Teglari, Kanal Kontenti (2 hafta)

**Maqsad:** Jamoa boshqaruvi va o'sish kontentini avtomatlashtirish.

- **Kanal Taklif Qilish** oqimi (v3.1, FR-048, 9.3-bo'lim) — oddiy, qo'lda admin tasdiqlash bilan.
- **Auditoriya Targeting'ni to'liq dinamik holatga o'tkazish** (v3.3/v3.4): `audience_tag_types` jadvali, fuzzy-matching orqali duplikat oldini olish, `pending`/`approved` holatlari.
- Minimal Admin Panel: pending source suggestions, pending audience tags, Manual Review Queue (past confidence e'lonlar, v2.0 9.12-bo'lim) uchun asosiy ko'rinish.
- Telegram kanal kontent strategiyasi (v3.1, 3.3-bo'lim) — kamida statistika va top-taklif postlarini avtomatlashtirish (scheduled job).

**Natija:** Jamoa yangi manba/teglarni boshqara oladi, kanal muntazam kontent bilan to'ldiriladi.

---

## Sprint 7 — Test, Beta Ishga Tushirish, Metrikalar (2 hafta)

**Maqsad:** MVP'ni real foydalanuvchilar bilan sinash va gipotezalarni raqamlar bilan tasdiqlash.

- Asosiy oqimlar uchun test qamrovi (v2.0, 19-bo'lim — kamida Integration va E2E darajasida asosiy stsenariylar).
- Kichik foydalanuvchi guruhi (masalan 50-100 kishi) bilan beta/soft-launch.
- Metrika kuzatuv infratuzilmasi ishga tushiriladi (avvalgi suhbatda belgilangan):
  - Activation rate (kamida 1 qidiruv/filtr yaratganlar ulushi)
  - 7-kunlik Retention
  - Referral conversion (taklif qilinganlardan necha foizi faol bo'ladi)
  - Notification engagement (bildirishnomalar necha foizi bosiladi/o'qiladi)
- Aniqlangan xatoliklarni tuzatish.

**Natija:** MVP validatsiya bosqichi uchun to'liq tayyor, real metrikalar bilan.

---

## Umumiy Jadval

| Sprint | Fokus | Asosiy Natija |
|---|---|---|
| 0 | Infratuzilma | Skeleton + Ops kanali faol |
| 1 | Listener + Ingestion | Xabarlar qabul qilinadi, aniq duplikat chetlab o'tiladi |
| 2 | AI Pipeline + Storage | To'liq struktura JSON + rasm R2'da |
| 3 | Dedup + DB | To'g'ri birlashtirilgan baza, 3-5 kanal |
| 4 | Search + Bot Asosi | Qidiruv ishlaydi |
| 5 | Notification + NLP + Referral | Bildirishnoma, erkin qidiruv, o'sish mexanizmi |
| 6 | Admin + Dinamik Teglar + Kontent | Boshqaruv va avtomatik kontent |
| 7 | Test + Beta + Metrikalar | Real foydalanuvchi validatsiyasi |

**Umumiy taxminiy muddat:** ~16 hafta (~4 oy), yuqoridagi taxminlar asosida.

---

## MVP "Exit Criteria" — Keyingi Bosqichga O'tish Mezonlari

Sprint 7 yakunida quyidagi ko'rsatkichlar ijobiy chiqsa, loyiha Roadmap'dagi keyingi bosqichga (Telegram Mini App, v3.1 12.2-bo'lim) o'tishga tayyor deb hisoblanadi:

- Notification engagement CTR: taxminiy chegara — 20%+
- 7-kunlik Retention: taxminiy chegara — 30%+
- Referral conversion: taxminiy chegara — sezilarli organik o'sish signali

*(Aniq raqamli chegaralar — bu boshlang'ich taxminlar, real bozor ma'lumotlari kelgach qayta ko'rib chiqilishi tavsiya etiladi.)*
