# Real Estate Super Aggregator — Loyiha Hujjati

**Hujjat Versiyasi:** 3.1.0
**Status:** Draft (Validation + MVP Architecture)
**Loyiha Nomi:** Real Estate Super Aggregator
**Oxirgi Yangilanish:** 2026-07-29
**O'zgarish Tarixi:**
- v2.0 (Production Architecture)
- v3.0 (Bozor Validatsiyasi + Biznes Model + AI Cost Strategy qo'shildi)
- v3.1 (Telefon signal tuzatildi, Auditoriya Targeting, Mini App Roadmap, Operatsion Monitoring Kanali, Listener Account Pool qo'shildi)

---

## Hujjat haqida

Ushbu hujjat v2.0 texnik arxitekturasi asosida quriladi va unga quyidagi yangi bo'limlarni qo'shadi:

- Bozor validatsiyasi va raqobatchi tahlili
- Bosqichma-bosqich biznes model (ishonch → monetizatsiya)
- O'sish strategiyasi (referral, kanal taklif qilish, kontent marketing)
- AI xarajatlarini optimallashtirish strategiyasi (OpenRouter, model tanlash)
- AI'dan oldingi arzon deduplication filtri
- Tabiiy tilda qidiruv funksiyasi (Natural Language Search)
- Telegram kanal kontent strategiyasi

To'liq texnik arxitektura tafsilotlari (Database Schema, REST API spetsifikatsiyasi, Security, Testing, Monitoring) v2.0 hujjatida saqlanadi va bu yerda faqat o'zgargan yoki yangi qo'shilgan qismlar batafsil yoritiladi, qolganlari qisqacha referens sifatida keltiriladi.

---

# 1. Yangilangan Vizyon va Muammo Bayoni

## 1.1 Muammo (o'zgarishsiz, tasdiqlangan)

O'zbekiston ko'chmas mulk bozori Telegram kanallari, guruhlari va veb-saytlarga fragmentatsiyalashgan. Bitta kvartira bir necha marta, turli manbalarda, turli formatda e'lon qilinadi. Foydalanuvchi vaqtini yo'qotadi, dublikatlarni qo'lda ajratadi.

## 1.2 Bozor Validatsiyasi (YANGI)

Loyihani boshlashdan oldin quyidagi bozor tekshiruvi o'tkazildi:

### 1.2.1 Raqobatchi Tahlili — @Uybor_bot

UyBor.uz platformasining rasmiy Telegram boti sinovdan o'tkazildi. Natija:

- Bot foydalanuvchidan tuman, xona soni, narx, egasi/makler kabi filtrlarni so'roq shaklida yig'adi.
- Biroq bot **faqat UyBor.uz saytining o'z (yopiq) bazasidagi** e'lonlar bo'yicha qidiradi.
- Telegram kanallar, guruhlar yoki boshqa saytlardagi e'lonlarni umuman qamrab olmaydi.
- Real sinovda foydalanuvchi so'roviga mos natija topilmadi ("Нет данных").

**Xulosa:** Bozorda "hamma manbalarni birlashtiruvchi" haqiqiy agregator hali mavjud emas. Bu — loyihaning asosiy differensiatsiya nuqtasi.

### 1.2.2 Norasmiy "Qo'lda Baza" Xizmatlari — Moliyaviy Signal

Bozorda notarial ravishda tashkil etilmagan, shaxsiy tashabbuskorlar tomonidan yuritiladigan xizmatlar aniqlandi:

- Ayrim shaxslar Telegram kanallar/guruhlarni qo'lda kuzatib, o'z "yopiq bazalarini" yig'ishadi.
- Bu bazaga kirish uchun foydalanuvchilardan **60,000–70,000 so'm** miqdorida bir martalik to'lov olishadi.
- Ba'zilari "uy topmasangiz pulingizni qaytaramiz" kabi kafolat va'da qilishadi (ishonchlilik darajasi tekshirilmagan).

**Xulosa:** Bu — bozorda **real pul aylanmasi** mavjudligini ko'rsatuvchi kuchli moliyaviy signal. Odamlar hatto tizimsiz, shaffofsiz xizmat uchun ham to'lashga tayyor. Avtomatlashtirilgan, shaffof va arzonroq yechim ushbu segmentni ochiq ravishda egallashi mumkin.

### 1.2.3 Founder-Market Fit

Loyiha asoschisi shaxsan ushbu muammoni boshdan kechirgan — uy qidirish jarayonida yuqoridagi cheklovlarga duch kelgan va muqobil yechim topa olmagan. Bu sifat jihatidan qimmatli signal, ammo statistik dalil emasligi hisobga olinadi — keyingi bosqichda foydalanuvchi validatsiyasi orqali kengroq tasdiqlanishi kerak.

### 1.2.4 Validatsiya Xulosasi

| Ko'rsatkich | Holat |
|---|---|
| Muammo real ekanligi | ✅ Tasdiqlangan (bozor tadqiqoti + shaxsiy tajriba) |
| Mavjud yechimlar yetarliligi | ❌ Yetarli emas — yetakchi o'yinchi ham cheklangan |
| Moliyaviy signal (to'lovga tayyorlik) | ✅ Qisman tasdiqlangan (norasmiy xizmatlar orqali) |
| Ommaviy talab hajmi | ⚠️ Hali o'lchanmagan — MVP validatsiyasi kerak |

---

# 2. Biznes Model va Monetizatsiya Strategiyasi (YANGI)

## 2.1 Falsafa: Ishonch Birinchi, Foyda Keyin

Boshlang'ich bosqichda loyihaning maqsadi foyda emas, balki **foydalanuvchi ishonchini qozonish va muammoni haqiqatan hal qilish**. Bu strategiya quyidagi bosqichlarga bo'linadi.

## 2.2 Bosqich 1 — Ishonch Qurish (0-500 faol foydalanuvchi)

### To'lov tizimi integratsiyasi yo'q

O'zbekistonda to'lov tizimlari (Click, Payme) bilan integratsiya boshida amalga oshirilmaydi. Sabablari:

- Texnik va huquqiy murakkablik hali oqlanmagan (foydalanuvchi bazasi tasdiqlanmagan).
- To'lov to'sig'i (payment friction) erta bosqichda o'sishni sekinlashtiradi.

### Referral-based Premium Model

- Foydalanuvchi **5 ta do'stini botga taklif qilsa**, 1 haftalik Premium obuna bepul beriladi.
- Premium imkoniyati: foydalanuvchining Saved Filter'iga mos e'lon aniqlanganda, **darhol** (real-time) bildirishnoma yuboriladi (oddiy foydalanuvchilarga nisbatan tezroq yoki kengroq qamrovda).

### Kanal Manba Taklif Qilish Dasturi

- Foydalanuvchi botda hali mavjud bo'lmagan Telegram kanal/guruhni taklif qilishi mumkin (oddiy: admin'ga xabar yozish orqali, MVP bosqichida avtomatlashtirilmaydi).
- Admin kanalning faolligini (obunachi soni, so'nggi post sanasi, kontent sifati) qo'lda baholaydi.
- Tasdiqlansa, taklif qilgan foydalanuvchiga 1 haftalik Premium obuna beriladi.
- **MVP bosqichida qat'iy avtomatlashtirish shart emas** — jarayon oddiy: foydalanuvchi kanalni yozadi → admin ko'rib chiqadi → tasdiqlaydi/rad etadi. Kelajakda avtomatik oldindan-filtrlash (obunachi soni, faollik chegarasi) qo'shilishi mumkin (qarang: 20-bo'lim, Roadmap).

### Telegram Kanal orqali Auditoriya Yig'ish

Loyihaning rasmiy Telegram kanali quyidagi ikkilamchi maqsadlarga xizmat qiladi:

- Bepul obunadan foydalanib uy topgan foydalanuvchilar ketishi tabiiy holat — ammo ular kanalga obuna bo'lib qolishlari mumkin, bu esa kanal auditoriyasini o'stiradi.
- Katta auditoriya (masalan 10,000+ obunachi) kelajakda Telegram Ad Platform orqali qo'shimcha (garchi katta bo'lmasa-da) daromad manbai bo'lishi mumkin.
- Eng muhimi — kanal "brend eslab qolish" vositasi bo'lib xizmat qiladi (qarang: 5-bo'lim, Kanal Kontent Strategiyasi), shunda foydalanuvchi kelajakda (masalan 1 yildan keyin) yana uy qidirganda birinchi eslaydigan joyi shu bo'ladi.

## 2.3 Bosqich 2 — Monetizatsiyaga O'tish (500+ faol foydalanuvchi, ishonch shakllangandan keyin)

### Foydalanuvchi (Tenant/Buyer) Obunasi

- Haftalik va oylik Premium obuna narxlari joriy etiladi.
- Narx belgilashda bozordagi norasmiy "qo'lda baza" xizmatlari (60,000–70,000 so'm bir martalik) narx nuqtasi sifatida hisobga olinadi — umumiy qiymat taqqoslanadi (masalan oylik obuna narxi shu diapazonga yaqin yoki past bo'lishi, lekin doimiy xizmat sifatida taqdim etilishi tavsiya etiladi).
- A/B narx testi orqali optimal narx nuqtasi aniqlanadi.

### Ikki Tomonlama Bozor (Two-Sided Marketplace) — Asosiy Uzoq Muddatli Monetizatsiya

Bu — loyihaning eng barqaror daromad manbai bo'lishi kutilmoqda, chunki qidiruvchilar (bir martalik foydalanuvchi, uy topgach ketadi) ga nisbatan uy egalari/agentlar **doimiy, takroriy mijoz** hisoblanadi:

- Uy egalari va agentlar o'z e'lonlarini "Tasdiqlangan" yoki "Tezkor ko'rinish" (promoted/featured) sifatida belgilash uchun to'lov qilishlari mumkin.
- Qisqa muddatda sotish/ijaraga berishni xohlaydigan egalar uchun reklama joylashtirish xizmati.
- Bu model OLX, UyBor kabi barcha yetakchi platformalarning asosiy daromad manbai bo'lib, bozorda o'zini oqlagan.

### Monetizatsiya Bosqichlari Xulosasi

| Bosqich | Foydalanuvchi soni | Asosiy fokus | Daromad manbai |
|---|---|---|---|
| 1 — Ishonch | 0–500 | Referral, kanal taklif, kontent marketing | Yo'q (investitsiya bosqichi) |
| 2 — Dastlabki monetizatsiya | 500–5,000 | Premium obuna (haftalik/oylik) | Foydalanuvchi obunasi |
| 3 — Barqaror o'sish | 5,000+ | Ikki tomonlama bozor | Egalar/agentlar reklama to'lovi (asosiy) + obuna (qo'shimcha) |

---

# 3. O'sish Strategiyasi (YANGI)

## 3.1 Growth Loop — Referral Mexanizmi

```text
Yangi foydalanuvchi botga kiradi
        ↓
Bepul qidiruv/filtr yaratadi
        ↓
5 ta do'stni taklif qiladi
        ↓
1 haftalik Premium oladi
        ↓
Real-time bildirishnoma orqali uy topadi
        ↓
Bot haqida ijobiy taassurot qoladi
        ↓
Do'stlariga tabiiy ravishda tavsiya qiladi (organik viral loop)
        ↓
Kanalga obuna bo'lib qoladi (uzoq muddatli brend aloqasi)
```

## 3.2 Referral Tizimi — Amalga Oshirish Talablari

- Har bir foydalanuvchi uchun noyob referral havola/kod generatsiya qilinadi (Telegram Deep Link: `t.me/botname?start=ref_<user_id>`).
- Taklif qilingan foydalanuvchi kamida bitta qidiruv/filtr yaratgandan so'ng "faol taklif" deb hisoblanadi (soxta/o'lik akkountlar orqali firibgarlikni oldini olish uchun).
- 5 ta faol taklif to'planganda avtomatik ravishda Premium holat yoqiladi va foydalanuvchiga xabar beriladi.

## 3.3 Kanal Kontent Strategiyasi

Rasmiy Telegram kanali "bir martalik qidiruv vositasi"dan "doimiy qiziqarli manba"ga aylantiriladi, shunda foydalanuvchi botni unutmaydi va uzoq muddatda qaytib kelish ehtimoli oshadi.

### Kontent Turlari va Chastotasi

| Kontent turi | Tavsif | Chastota |
|---|---|---|
| Kunlik top taklif | Bazadan eng yaxshi narx/maydon nisbatiga ega e'lonlar | Kuniga 1 marta |
| Bozor narxlari statistikasi | Tuman bo'yicha o'rtacha narx, haftalik o'zgarish | Haftada 1 marta |
| Tuman qiyoslash | Masalan "Yunusobod vs Chilonzor: qaysi arzon?" | Haftada 1 marta |
| Amaliy maslahatlar | Kvartira ko'rish, makler bilan ishlash bo'yicha tavsiyalar | Haftada 1–2 marta |
| Muvaffaqiyat hikoyalari | Foydalanuvchining uy topish tajribasi (ruxsat bilan) | 2 haftada 1 marta |
| Shaffoflik hisoboti | "Bugun N ta e'lon tekshirildi, M tasi dublikat aniqlandi" | Kuniga yoki haftada 1 marta |

### Avtomatlashtirish

Statistika, top-taklif va shaffoflik hisoboti kabi kontent turlari mavjud AI Processing Pipeline va PostgreSQL bazasidan **avtomatik skript** (scheduled job) orqali generatsiya qilinadi — qo'shimcha inson resursi talab qilinmaydi. Faqat maslahat va muvaffaqiyat hikoyalari kabi "inson qo'li" kerak bo'ladigan kontent qo'lda tayyorlanadi.

### Nashr Chastotasi Bo'yicha Ogohlantirish

Kuniga 1-2 postdan oshmaslik tavsiya etiladi — aks holda foydalanuvchilar kanalni spam deb hisoblab, ovozini o'chirib qo'yishi (mute) mumkin, bu esa reach'ni pasaytiradi.

---

# 4. Yangi Funksional Talablar (FR — YANGI QO'SHILGAN)

v2.0 hujjatidagi 46 ta Functional Requirement'ga quyidagilar qo'shiladi:

## FR-047 — Referral Tizimi

Har bir foydalanuvchi uchun noyob referral kod/havola generatsiya qilinadi. Tizim faol takliflarni (kamida 1 qidiruv yaratgan) kuzatadi va 5 taga yetganda avtomatik Premium holatni faollashtiradi.

## FR-048 — Manba Taklif Qilish (Source Suggestion)

Foydalanuvchi botga hali mavjud bo'lmagan Telegram kanal/guruhni taklif qilishi mumkin. MVP bosqichida bu — admin'ga to'g'ridan-to'g'ri xabar yuborish shaklida oddiy amalga oshiriladi (murakkab avtomatlashtirish shart emas). Admin tasdiqlasa, foydalanuvchiga Premium mukofot beriladi.

## FR-049 — Tabiiy Tilda Qidiruv (Natural Language Search)

Foydalanuvchi o'z so'rovini erkin matnda (o'zbek, rus yoki aralash tilda) yozishi mumkin (masalan: "budjetim $400, metroga yaqin, yosh oilaga, 2 xonali, Yunusobod yoki Mirzo Ulug'bek"). AI ushbu matndan struktura filtrlarni (byudjet, tuman, xona soni, qo'shimcha shartlar) avtomatik ajratib oladi va mavjud Search Engine'ga uzatadi. Batafsil: 7-bo'lim.

## FR-050 — Pre-AI Deduplication Filter

AI Parsing bosqichiga yuborishdan oldin, yangi kelgan e'lon arzon va tez usullar (forward metadata, matn hash, telefon regex, rasm pHash) yordamida oldindan tekshiriladi. Yuqori ishonchli duplicate aniqlansa, AI chaqiruvi butunlay chetlab o'tiladi. Batafsil: 6-bo'lim.

## FR-051 — Premium Real-Time Notification

Premium foydalanuvchilar uchun Saved Filter'ga mos e'lon aniqlanganda bildirishnoma yuborish tezligi oshiriladi (standart Notification Queue'da ustuvorlik beriladi — qarang v2.0 hujjatidagi 4.9.6 Priority Queue mexanizmi).

## FR-052 — Egalar/Agentlar uchun Promoted Listing (Kelajak, Bosqich 2-3)

Uy egalari va agentlar o'z e'lonlarini to'lov evaziga "Tasdiqlangan"/"Tezkor ko'rinish" sifatida belgilashi mumkin bo'ladi. MVP bosqichida amalga oshirilmaydi, Database Schema'da kengaytirish uchun joy qoldiriladi (`is_promoted`, `promoted_until` kabi maydonlar kelajakda `announcements` jadvaliga qo'shilishi mumkin).

## FR-053 — Auditoriya Targeting

Tizim e'londa aniq ko'rsatilgan mo'ljallangan va rad etilgan auditoriya teglarini ajratib oladi va qidiruvda hisobga oladi. Batafsil: 7A-bo'lim.

## FR-054 — Listener Account Pool

Tizim bir nechta mustaqil Telethon akkount orqali manbalarni kuzatadi, akkountlar sog'lig'ini alohida monitoring qiladi va muammoli akkount kanallarini avtomatik qayta taqsimlaydi. Batafsil: 10B-bo'lim.

---

# 5. AI Processing Strategiyasi va Xarajatlarni Optimallashtirish (YANGI/YANGILANGAN)

## 5.1 Provider Tanlash — OpenRouter Orqali

v2.0 hujjatida AI Provider Abstraction tamoyili (ADR-005, DD-番) allaqachon rejalashtirilgan edi. Bu tamoyil endi aniq amaliy qarorlar bilan to'ldiriladi.

### Tanlangan Provider Infratuzilmasi: OpenRouter

OpenRouter bitta API kaliti orqali Gemini modellariga kirish imkonini beradi; model almashtirish faqat konfiguratsiya darajasida bo'ladi, kod o'zgarmaydi.

### Model Tanlash Strategiyasi — Ko'p Bosqichli Yondashuv

| Rol | Model | Narx (Input/Output, $/1M token) | Ishlatilish holati |
|---|---|---|---|
| **Asosiy (Primary)** | Gemini 3.1 Flash-Lite | — | Barcha standart e'lon parsing va tabiiy tilda qidiruv so'rovlari |
| **Fallback (Retry)** | Gemini 3.1 Flash-Lite Preview | — | Primary model xato qaytarsa yoki confidence past chiqqanda |

### OpenRouter Konfiguratsiyasida Fallback

OpenRouter'ning `models` parametri orqali ustuvorlik ro'yxati `["google/gemini-3.1-flash-lite", "google/gemini-3.1-flash-lite-preview"]` tarzida belgilanadi; plain `google/gemini-3.1-flash` OpenRouter katalogida mavjud emas va Pro modellar ishlatilmaydi.

### Muhim Eslatma — Model Hayot Sikli

Model IDlari konfiguratsiya orqali boshqariladi; hozirgi production ketma-ketligi Gemini 3.1 Flash-Lite → Gemini 3.1 Flash-Lite Preview.

## 5.2 Rasm Optimizatsiyasi — Xarajatni Kamaytirish

### Siqish (Compression)

AI'ga yuborishdan oldin barcha rasmlar siqiladi va o'lchami kamaytiriladi (tavsiya etilgan: ~800×600 piksel atrofida). Vision modellar rasmni "tile" (bo'lak)larga bo'lib token hisoblaganligi sababli, kichikroq o'lcham to'g'ridan-to'g'ri kamroq token, demak kamroq xarajat degani.

### Rasm Sonini Cheklash

Bitta e'londa odatda bir nechta rasm bir xil xonani turli burchakdan ko'rsatadi. Barcha rasmlarni (masalan 10 tagacha) emas, eng informativ 3–4 tasini (umumiy ko'rinish, oshxona, hammom, fasad) tanlab yuborish orqali rasm bilan bog'liq xarajat sezilarli kamayadi, sifatga deyarli ta'sir qilmaydi.

### Ikki Bosqichli Vision Ishlatish

1. Avval faqat matn Flash-Lite bilan qayta ishlanadi (juda arzon).
2. Vision (rasm tahlili) faqat matnda yetishmayotgan ma'lumot bo'lsa (ta'mirlash holati, mebel mavjudligi) yoki confidence past chiqqanda ishga tushiriladi — har doim emas.

## 5.3 Prompt Caching

Tizim prompti (schema, ko'rsatmalar) har bir so'rovda deyarli bir xil bo'lganligi sababli, provider prompt caching'ni qo'llab-quvvatlasa (Gemini bunday imkoniyatga ega), bu qism kesh orqali ~90% arzonroq hisoblanadi. Bu ayniqsa Tabiiy Tilda Qidiruv funksiyasida (7-bo'lim) foydali, chunki bu funksiya botning eng tez-tez ishlatiladigan qismi bo'lishi kutilmoqda.

## 5.4 Taxminiy Xarajat Hisob-Kitobi

### E'lon Parsing (matn + 4 ta siqilgan rasm, Flash-Lite)

| Parametr | Qiymat |
|---|---|
| Kirish tokenlari (matn + 4 rasm) | ~1,700–2,000 |
| Chiqish tokenlari (JSON) | ~300 |
| Narx (1 e'lon) | ~$0.0003 (~4 so'm) |
| Narx (10,000 e'lon) | ~$3 (~38,000 so'm) |

### Tabiiy Tilda Qidiruv So'rovi (matn, rasm yo'q)

| Parametr | Qiymat |
|---|---|
| Kirish tokenlari | ~350–400 |
| Chiqish tokenlari | ~150 |
| Narx (1 so'rov) | ~$0.0001 (~1.3 so'm) |
| Narx (10,000 so'rov) | ~$1 (~12,700 so'm) |

**Xulosa:** AI xarajati loyihaning hozirgi (MVP/validatsiya) bosqichida amaliy jihatdan cheklovchi omil emas. Hatto o'rtacha production hajmida ham xarajat foydalanuvchi obuna daromadidan sezilarli darajada past bo'lib qoladi.

---

# 6. Pre-AI Deduplication Filter (YANGI ARXITEKTURA QATLAMI)

## 6.1 Maqsad

v2.0 hujjatidagi Deduplication Engine (10-bo'lim) AI tomonidan struktura qilingan ma'lumotlar ustida ishlaydi — ya'ni AI Parsing **keyin** ishga tushadi. Bu yangi qatlam esa AI Parsing'dan **oldin** joylashadi va quyidagi maqsadlarga xizmat qiladi:

- AI chaqiruvlar sonini kamaytirish (xarajat va vaqtni tejash).
- Aniq duplicate holatlarini millisekundlarda, AI'siz aniqlash.

## 6.2 Signal Turlari — Barchasi AI'siz, Arzon va Tez

### a) Forward Metadata Tekshiruvi

Telethon orqali har bir xabarda `fwd_from` maydoni mavjud bo'lishi mumkin. Agar kanal forward funksiyasidan foydalansa (matnni qo'lda qayta yozmasa), bu maydonda asl kanal ID va xabar ID saqlanadi. Ikki xabarning `fwd_from.channel_id` + `channel_post` bir xil bo'lsa — bu **100% ishonchli** duplicate signal, hech qanday qo'shimcha tahlil shart emas.

**Cheklov:** Ba'zi kanal adminlari matnni qo'lda nusxa ko'chirib qayta joylashtiradi — bunda `fwd_from` bo'sh bo'ladi, keyingi signallar kerak bo'ladi.

### b) Matn Hash (Aniq Nusxalar Uchun)

Matn normallashtiriladi (kichik harf, ortiqcha probel/emoji/hashtag olib tashlanadi), so'ng SHA256 hash hisoblanadi. Ikki xabarning normallashtirilgan hash'i bir xil bo'lsa — bu aynan bir xil matn, faqat boshqa kanalga joylashtirilgan.

### c) Near-Duplicate Matn Moslashtirish

Biroz tahrirlangan nusxalar (narx yangilangan, emoji qo'shilgan) uchun SimHash yoki MinHash + Jaccard Similarity usullari qo'llaniladi — bular AI chaqirmasdan, millisekundlarda 85-90%+ matn o'xshashligini aniqlay oladi.

### d) Telefon Raqami — FAQAT Kuchsiz Yordamchi Signal (v3.1'da tuzatildi)

**Muhim tuzatish:** v3.0'da telefon raqami "kuchli duplicate signal" sifatida belgilangan edi. Bu **noto'g'ri** yondashuv, chunki O'zbekiston bozorida bitta makler/agentlik odatda **bitta shaxsiy raqamni o'nlab turli, bir-biriga aloqasi yo'q kvartira e'lonlarida** ishlatadi. Agar telefon raqami yolg'iz o'zi "duplicate" degan qarorga asos bo'lsa, tizim turli mulklarni notoʻgʻri ravishda bitta Parent Announcement'ga birlashtirib, ma'lumotlar bazasini jiddiy buzadi (false positive).

**Yangi qoida:** Regex orqali ajratib olingan telefon raqami **hech qachon yolg'iz holda** duplicate qarori uchun ishlatilmaydi — na Pre-AI Filter bosqichida (avtomatik AI'ni chetlab o'tish uchun), na yakuniy Decision Engine'da (v2.0, 10.11-bo'lim, Weighted Scoring). Telefon mosligi faqat quyidagi shartlar bilan birga bo'lgandagina qo'shimcha vazn sifatida hisobga olinadi:

- Rasm (pHash) mosligi **VA** telefon mosligi — ikkalasi birga kuchli signal.
- Manzil/tuman + xona soni + maydon mosligi **VA** telefon mosligi — qo'shimcha tasdiq sifatida.

v2.0 hujjatidagi 10.11-bo'lim (Weighted Scoring Strategy) jadvalidagi "Phone Number Match — Weight 100" qiymati **v3.1'da bekor qilinadi** va o'rniga quyidagi past vazn beriladi:

| Signal | Yangi Weight (v3.1) | Izoh |
|---|---|---|
| Phone Number Match | 15 | Faqat qo'shimcha tasdiq sifatida, yolg'iz holda yetarli emas |

Bundan tashqari, C.3-bo'limdagi (Appendix C Test Cases) TC-001 ("Same Phone Number → Duplicate") va TC-013/TC-015/TC-016 kabi telefon-asosli test case'lar ushbu yangi mantiqqa mos ravishda qayta ko'rib chiqilishi kerak — "faqat telefon bir xil" holati endi **"New Announcement"** natijasini berishi kerak, "Duplicate" emas.

### e) Rasm pHash (AI Emas — Lokal Kutubxona Orqali)

pHash (perceptual hash) hisoblash AI chaqiruvi emas — Python'da `Pillow` + `imagehash` kabi kutubxonalar orqali lokal, bepul va tez amalga oshiriladi. Rasm AI'ga yuborilishidan oldin uning pHash'i hisoblanadi va mavjud bazadagi so'nggi kunlar rasmlari bilan solishtiriladi. Hamming Distance kichik bo'lsa (masalan <8), yuqori duplicate ehtimoli.

## 6.3 Yangilangan Pipeline

```text
Adapter → Queue → Worker
                    │
                    ▼
         Pre-AI Filter (AI'siz, arzon, tez)
         ├── Forward metadata check
         ├── Text hash / near-duplicate check
         ├── Phone regex extraction
         └── Image pHash check
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
  Yuqori ishonchli         Noaniq / Yangi
   Duplicate                    │
        │                       ▼
        ▼                  AI Parsing
  To'g'ridan-to'g'ri            │
  Parent'ga bog'lash            ▼
  (AI chaqirilmaydi)      To'liq Deduplication Engine
                          (v2.0, 10-bo'lim — Weighted
                           Similarity Score bilan)
                                │
                                ▼
                            Database
```

## 6.4 Konservativlik Tamoyili

Pre-AI Filter **faqat juda yuqori ishonch** bo'lganda (masalan `fwd_from` mos kelganda yoki matn hash'i 100% bir xil bo'lganda) AI'ni butunlay chetlab o'tishga qaror qiladi. Noaniq holatlarda (masalan faqat pHash biroz o'xshash, lekin matn boshqacha) — baribir AI'ga yuborilib, to'liq tahlil qilinadi va v2.0'dagi mavjud Weighted Similarity Score orqali yakuniy qaror qabul qilinadi. Bu **false positive** (yangi e'lonni noto'g'ri "duplicate" deb belgilash) xavfini oldini oladi — bu AI xarajatidan ko'ra jiddiyroq muammo, chunki bazaning sifatini pasaytiradi.

**Alohida qoida — Telefon Raqami Pre-AI Filter'da avtomatik "skip AI" trigger sifatida HECH QACHON ishlatilmaydi** (qarang 6.2.d). U faqat AI Parsing'dan keyingi to'liq Deduplication Engine bosqichida, boshqa signallar bilan birga, past vazn (weight 15) bilan hisobga olinadi. Sabab — maklerlar bitta raqamni ko'plab turli kvartiralarda ishlatadi, shuning uchun telefon mosligi yolg'iz holda hech qachon "yuqori ishonch" darajasiga yetmaydi.

## 6.5 Kutilayotgan Ta'sir

O'zbekiston Telegram bozorida bir xil bazani turli kanallarga tarqatish keng tarqalgan amaliyot ekanligini hisobga olsak (v2.0 hujjatining 1.2-bo'limida ta'kidlangan "Duplicate Listings" muammosi), kiruvchi postlarning sezilarli qismi (taxminan 30-50%) aslida duplicate bo'lishi mumkin. Bu degani:

- AI chaqiruvlarining muhim qismi umuman kerak bo'lmaydi.
- Xarajat qo'shimcha ravishda kamayadi (5-bo'limda ko'rsatilgan raqamlarga nisbatan ham).
- Tezlik oshadi — AI chaqiruvi (1-3 soniya) o'rniga hash/regex/pHash tekshiruvi millisekundlarda tugaydi.

---

# 7. Tabiiy Tilda Qidiruv (Natural Language Search) — YANGI FUNKSIYA

## 7.1 Foydalanuvchi Tajribasi

Foydalanuvchi bosqichma-bosqich filtr tanlash (District → Rooms → Price) o'rniga, o'z so'rovini erkin matnda yozishi mumkin:

> "budjetim $400, metroga yaqin joylardan yosh oilaga yaxshi remontli uy, mirzo ulug'bek, yunusobod, mirobod rayonlaridan, 2 xonali bo'lsa yaxshi"

Tizim bu matndan quyidagi struktura filtrlarni avtomatik ajratib oladi:

- Byudjet: $400
- Metroga yaqinlik: talab qilinadi
- Auditoriya: yosh oila
- Remont: yaxshi
- Tumanlar: Mirzo Ulug'bek, Yunusobod, Mirobod
- Xonalar soni: 2

## 7.2 Texnik Pipeline

```text
Foydalanuvchi tabiiy matni (o'zbek/rus/aralash, max 140 belgi)
        ↓
AI (Gemini 2.5 Flash-Lite) — Filter Extraction
        ↓
Structured Filter JSON
        ↓
Mavjud Search Service (v2.0, 11-bo'lim)
        ↓
Natijalar
```

Bu — alohida yangi pipeline emas, balki mavjud Search API'ga qo'shiladigan oddiy "kirish qatlami" (input layer). Texnik murakkablik minimal, chunki Search Engine, Repository Pattern va Filter Composition allaqachon v2.0'da to'liq loyihalashtirilgan.

## 7.3 Narx

Bu funksiya rasm o'z ichiga olmagani sababli, e'lon parsing'ga nisbatan sezilarli arzonroq (~1.3 so'm/so'rov, qarang 5.4-bo'lim). Prompt caching qo'llanilganda narx yana ham pasayishi mumkin.

## 7.4 Sifat Bo'yicha Eslatma

Ushbu funksiyaning muvaffaqiyati narxga emas, balki AI'ning noaniq/xira ifodalarni (masalan "yosh oilaga yaxshi" iborasini tegishli struktura maydonga moslashtirish) qanchalik to'g'ri tushunishiga bog'liq. Ishga tushirishdan oldin turli xil real foydalanuvchi iboralari bilan sinov o'tkazish va prompt'ni shunga mos ravishda takomillashtirish tavsiya etiladi.

---

# 7A. Auditoriya Targeting (Kimlar Uchun) — YANGI FUNKSIYA

## 7A.1 Muammo

Ko'plab e'lonlarda uy egasi/makler kvartira kimga mo'ljallanganini aniq ko'rsatadi (masalan "yosh oilaga", "talabalarga", "faqat qizlarga"), ba'zilari esa buni ko'rsatmaydi. Bundan tashqari, ba'zi e'lonlarda **inkor** shaklida cheklov bo'ladi ("talabalarga berilmaydi", "bolali oilalarga mos emas"). Bu ma'lumot hozirgi Search Engine (v2.0, 11-bo'lim) filtrlarida umuman qamrab olinmagan — natijada foydalanuvchi (masalan talaba) o'ziga mos kelmaydigan e'lonlarni ham ko'rishi mumkin.

## 7A.2 Yechim — Ikki Yo'nalishli Teg Tizimi

Auditoriya ma'lumoti bitta enum maydon emas, balki **ikkita mustaqil, ko'p qiymatli (multi-value) teg ro'yxati** sifatida modellashtiriladi:

- **`audience_tags`** (inclusion) — e'lon aniq mo'ljallangan auditoriyalar ro'yxati. Masalan: `["young_family", "students"]`.
- **`audience_excluded_tags`** (exclusion) — e'lon aniq rad etgan auditoriyalar ro'yxati. Masalan: `["students"]`.

### Standart Qiymat Qoidasi

Agar ikkala ro'yxat ham bo'sh bo'lsa — e'lon **hamma uchun ochiq** deb hisoblanadi (default). Bu — AI matnda aniq ko'rsatma topa olmagan holatlarda qo'llaniladigan xavfsiz standart.

## 7A.3 Tavsiya Etilgan Standart Teglar

| Teg | Tavsif |
|---|---|
| `young_family` | Yosh oila |
| `family_with_children` | Bolali oila |
| `students` | Talabalar |
| `single_male` | Yolg'iz erkak |
| `single_female` | Yolg'iz ayol |
| `group_of_girls` | Qizlar guruhi |
| `group_of_boys` | Yigitlar guruhi |
| `foreigners` | Xorijliklar |

Ro'yxat kelajakda kengaytirilishi mumkin, lekin yangi teg qo'shish AI Prompt Schema'sini yangilashni talab qiladi (Appendix B, B.5 Enumerated Values tamoyiliga mos).

## 7A.4 AI Extraction — Standart JSON Schema'ga Qo'shimcha

v2.0 hujjatining Appendix B (B.3 Standard JSON Schema) quyidagi ikki maydon bilan kengaytiriladi:

```json
{
  "audience_tags": ["young_family"],
  "audience_excluded_tags": ["students"]
}
```

AI ushbu maydonlarni faqat matnda **aniq va bevosita** ko'rsatma bo'lgandagina to'ldiradi (masalan "yosh oilaga" yoki "talabalarga berilmaydi" kabi ochiq ifodalar). Taxmin qilish yoki matndan bilvosita xulosa chiqarish (masalan faqat narx yoki xona soniga qarab auditoriyani "taxmin qilish") **qat'iyan taqiqlanadi** — bu v2.0'dagi B.2 General Rules tamoyiliga ("Taxmin qilinmaydi") to'liq mos keladi.

## 7A.5 Ma'lumotlar Bazasiga Ta'siri

`announcements` jadvaliga ikkita yangi ustun qo'shiladi (v2.0, 6.7-bo'lim ERD'ga qo'shimcha):

| Column | Type | Description |
|---|---|---|
| audience_tags | TEXT[] yoki JSONB | Mo'ljallangan auditoriya teglari ro'yxati |
| audience_excluded_tags | TEXT[] yoki JSONB | Rad etilgan auditoriya teglari ro'yxati |

PostgreSQL'da `TEXT[]` (array) yoki `JSONB` formatida saqlanishi tavsiya etiladi, ustiga GIN Index qo'yilishi mumkin (v2.0, 6.22-bo'lim Index Strategy tamoyiliga mos), bu esa `audience_tags @> ARRAY['students']` kabi qidiruvlarni tez bajarish imkonini beradi.

## 7A.6 Search Engine'ga Ta'siri

v2.0'dagi Supported Filters ro'yxatiga (11.5-bo'lim) yangi filtr qo'shiladi: **Auditoriya** (`audience`). Qidiruv mantig'i:

```text
Agar foydalanuvchi auditoriya = "students" bilan qidirsa:

    KO'RSATILADI, agar:
        "students" audience_excluded_tags ichida BO'LMASA
        VA (
            audience_tags bo'sh (hammaga ochiq)
            YOKI "students" audience_tags ichida bor
        )

    KO'RSATILMAYDI, agar:
        "students" audience_excluded_tags ichida bor
```

Bu mantiq Tabiiy Tilda Qidiruv (7-bo'lim) funksiyasi bilan ham integratsiya qilinadi — masalan foydalanuvchi "men talabaman, uy izlayapman" desa, AI buni `audience = students` filtriga avtomatik moslashtiradi.

## 7A.7 Yangi Functional Requirement

**FR-053 — Auditoriya Targeting.** Tizim AI Parsing bosqichida e'londan mo'ljallangan va rad etilgan auditoriya teglarini ajratib oladi (agar matnda aniq ko'rsatilgan bo'lsa) va Search Engine orqali foydalanuvchiga mos filtrlash imkonini beradi. Aniq ko'rsatma bo'lmagan holatda e'lon "hamma uchun ochiq" deb hisoblanadi.

---

# 8. Yangilangan Ma'lumotlar Bazasi Sxemasi — Qo'shimcha Jadval/Maydonlar

v2.0 hujjatidagi to'liq ERD (6-bo'lim) asosiy bo'lib qoladi. Quyidagi qo'shimchalar yangi funksiyalarni qo'llab-quvvatlash uchun zarur.

## 8.1 `users` Jadvaliga Qo'shimcha Maydonlar

| Column | Type | Description |
|---|---|---|
| referral_code | VARCHAR | Foydalanuvchining noyob referral kodi |
| referred_by | BIGINT (FK → users.id) | Kim tomonidan taklif qilingani |
| premium_until | TIMESTAMP | Premium obuna tugash sanasi (NULL — oddiy foydalanuvchi) |
| active_referral_count | SMALLINT | Faol (kamida 1 qidiruv yaratgan) takliflar soni |

## 8.2 Yangi Jadval — `source_suggestions`

| Column | Type | Description |
|---|---|---|
| id | UUID | Primary Key |
| suggested_by | BIGINT (FK → users.id) | Taklif qilgan foydalanuvchi |
| channel_identifier | VARCHAR | Taklif qilingan kanal username/link |
| status | ENUM | pending, approved, rejected |
| reviewed_by | BIGINT | Ko'rib chiqqan admin (nullable) |
| reviewed_at | TIMESTAMP | Ko'rib chiqilgan vaqt |
| created_at | TIMESTAMP | Taklif qilingan vaqt |

## 8.3 Yangi Jadval — `referral_events`

| Column | Type | Description |
|---|---|---|
| id | UUID | Primary Key |
| referrer_id | BIGINT (FK → users.id) | Taklif qilgan foydalanuvchi |
| referred_user_id | BIGINT (FK → users.id) | Taklif qilingan foydalanuvchi |
| is_active | BOOLEAN | Taklif qilingan foydalanuvchi kamida 1 qidiruv yaratganmi |
| created_at | TIMESTAMP | Taklif qabul qilingan vaqt |

## 8.4 `announcements` Jadvaliga Kelajakdagi Kengaytirish (Bosqich 2-3 uchun, hozircha NULL/ishlatilmaydi)

| Column | Type | Description |
|---|---|---|
| is_promoted | BOOLEAN | Egasi/agent to'lov qilib "tasdiqlangan" belgisini olganmi |
| promoted_until | TIMESTAMP | Promotion tugash sanasi |

---

# 9. Yangilangan Telegram Bot Oqimi

v2.0 hujjatidagi Bot Architecture (13-bo'lim) va UX Flows (Appendix D) asosiy bo'lib qoladi. Quyidagi yangi handler/flow'lar qo'shiladi.

## 9.1 Yangi Asosiy Menyu Elementlari

```text
/start
    │
    ▼
Main Menu
    │
    ├────────► Qidiruv (oddiy filtr YOKI tabiiy tilda yozish)
    │
    ├────────► Saqlangan Filtrlar
    │
    ├────────► Bildirishnomalar
    │
    ├────────► Do'stlarni Taklif Qilish (Referral)
    │
    ├────────► Kanal Taklif Qilish (Source Suggestion)
    │
    ├────────► Sozlamalar
    │
    └────────► Yordam
```

## 9.2 Referral Oqimi

```text
"Do'stlarni Taklif Qilish" bosiladi
        ↓
Bot noyob referral havolani ko'rsatadi
        ↓
Foydalanuvchi havolani ulashadi
        ↓
Yangi foydalanuvchi havola orqali kiradi (referred_by belgilanadi)
        ↓
Yangi foydalanuvchi kamida 1 qidiruv yaratadi → is_active = true
        ↓
Referrer'ning active_referral_count += 1
        ↓
5 taga yetganda → premium_until avtomatik yangilanadi (+7 kun)
        ↓
Referrer'ga tabriknoma xabari yuboriladi
```

## 9.3 Kanal Taklif Qilish Oqimi (MVP — Soddalashtirilgan)

```text
"Kanal Taklif Qilish" bosiladi
        ↓
Foydalanuvchi kanal username/link'ini yozadi
        ↓
Tizim source_suggestions jadvaliga yozadi (status = pending)
        ↓
Admin'ga xabar yuboriladi (yangi taklif)
        ↓
Admin qo'lda ko'rib chiqadi (obunachi soni, faollik)
        ↓
    ┌───────┴───────┐
    ▼               ▼
Tasdiqlansa     Rad etilsa
    │               │
    ▼               ▼
Kanal channels   Foydalanuvchiga sabab
jadvaliga        bilan xabar beriladi
qo'shiladi
    │
    ▼
Foydalanuvchiga Premium
mukofot beriladi (+7 kun)
```

**Eslatma:** Bu jarayon MVP bosqichida qat'iy avtomatlashtirilmaydi — admin qo'lda ko'rib chiqadi. Avtomatik oldindan-filtrlash (masalan minimal obunachi soni chegarasi) kelajakda qo'shilishi mumkin, qarang 20-bo'lim.

## 9.4 Tabiiy Tilda Qidiruv Oqimi

```text
"Qidiruv" bosiladi
        ↓
Bot ikkita variant taklif qiladi:
  a) Bosqichma-bosqich filtr (mavjud FSM, v2.0)
  b) Erkin matn yozish (yangi)
        ↓
(b) tanlansa: foydalanuvchi erkin matn yozadi
        ↓
AI Filter Extraction (7-bo'lim)
        ↓
Struktura filtrlar foydalanuvchiga tasdiqlash uchun ko'rsatiladi
        ↓
Tasdiqlansa → Search Service'ga uzatiladi
        ↓
Natijalar
```

---

# 10. Yangilangan Arxitektura Diagrammasi (Yuqori Darajali)

```text
                         +----------------------+
                         | Telegram Channels    |
                         +----------+-----------+
                                    │
                         +----------v-----------+
                         | Telethon Listener    |
                         +----------+-----------+
                                    │
                             Redis Event Queue
                                    │
                         +----------v-----------+
                         | Pre-AI Filter (YANGI)|
                         | Forward/Hash/pHash    |
                         +----------+-----------+
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
          Yuqori ishonchli Duplicate        Noaniq / Yangi
          (AI chetlab o'tiladi)                    │
                    │                               ▼
                    │                    +---------v---------+
                    │                    | AI Worker          |
                    │                    | (OpenRouter:       |
                    │                    | Flash-Lite → Flash |
                    │                    | fallback)          |
                    │                    +---------+---------+
                    │                              │
                    └──────────────┬───────────────┘
                                   ▼
                         +---------v---------+
                         | Deduplication      |
                         | Engine (v2.0)      |
                         +---------+---------+
                                   ▼
                         +---------v---------+
                         | PostgreSQL         |
                         +---------+---------+
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     +--------v--------+  +--------v--------+  +--------v--------+
     | FastAPI REST API|  | Telegram Bot     |  | Kanal Kontent    |
     |                 |  | (NLP Search,     |  | Scheduler (YANGI)|
     |                 |  | Referral, Source |  |                  |
     |                 |  | Suggestion)      |  |                  |
     +-----------------+  +-----------------+  +-----------------+
```

---

# 10A. Operatsion Monitoring Kanali va Bot (YANGI)

## 10A.1 Maqsad

Foydalanuvchi botiga tizim/xatolik xabarlarini aralashtirish tavsiya etilmaydi — bu foydalanuvchi tajribasini buzadi va muhim signal foydalanuvchiga tegishli bo'lmagan "shovqin" bilan aralashib ketadi. Shuning uchun **alohida, faqat jamoa (admin/dasturchilar) uchun mo'ljallangan** Telegram bot va kanal tashkil etiladi.

Bu yondashuv v2.0 hujjatidagi 17.12-bo'lim (Alerting) va G-Appendix (Error Codes Reference) talablarini amaliy darajada, aniq kanal orqali amalga oshiradi.

## 10A.2 Arxitektura

```text
Tizim hodisasi (Deduplication, AI xatosi, Worker Failure va h.k.)
        ↓
Structured Log Event (Correlation ID bilan, v2.0 17.9-bo'lim)
        ↓
Ops Notification Service
        ↓
Ops Telegram Bot (foydalanuvchi botidan mustaqil, alohida token)
        ↓
Ops Telegram Kanali (faqat jamoa a'zolari obuna)
```

## 10A.3 Xabar Formati — Duplicate Detection Misoli

Foydalanuvchi bergan namuna asosida standart shablon:

```text
🔁 Takrorlanish aniqlandi

Ishonchlilik darajasi: 100%
Sabab: Metadata'da forwarded_from mavjud (aynan mos kanal + post ID)

Asl nusxa: [link]
Takroriy: [link]

Vaqt: 2026-07-29 14:32:10
Correlation ID: f4a2b781
```

## 10A.4 Qamrab Olinadigan Hodisa Turlari

| Hodisa turi | Misol |
|---|---|
| Duplicate aniqlandi (Pre-AI yoki to'liq Engine) | Yuqoridagi namuna |
| AI Parsing xatosi | Model javob bermadi / Invalid JSON / Confidence juda past |
| Worker/Queue xatosi | Retry limiti tugadi, Dead Letter Queue'ga tushdi |
| Listener akkount muammosi | FloodWait, akkount vaqtincha bloklandi (qarang 10B) |
| Source Health muammosi | Kanal N kundan beri yangi post bermayapti |
| Kanal Taklif Qilish (Source Suggestion) | Yangi taklif kelganda admin xabardor qilinadi (9.3-bo'lim) |

## 10A.5 Amaliy Eslatma

Xabarlar chastotasi nazorat qilinishi kerak — masalan bir xil turdagi xato qisqa vaqt ichida ko'p marta takrorlansa (masalan bitta manba doimiy xato bersa), individual xabar o'rniga **agregatsiya qilingan xulosa** yuborilishi tavsiya etiladi ("Oxirgi 10 daqiqada X manbadan 47 marta xato qaytdi"), aks holda kanal spam bilan to'lib, muhim signal ko'zdan yo'qoladi.

---

# 10B. Listener Account Pool — Ko'p Akkountli Telegram Tinglash (YANGI)

## 10B.1 Muammo

Bitta Telethon akkounti orqali barcha manbalarni (yuzlab kanal/guruh) kuzatish quyidagi risklarni yaratadi:

- **Yagona nuqta nosozligi (Single Point of Failure):** akkount vaqtinchalik bloklansa yoki FloodWait cheklovi tushsa, **barcha** manbalardan signal to'xtaydi.
- **Yetkazib berish nomuvofiqligi:** ba'zi holatlarda bitta akkountga ba'zi xabarlar boshqalariga nisbatan kechroq yoki umuman kelmasligi kuzatilishi mumkin — bu Telegram'ning ichki cheklovlari bilan bog'liq.
- **Spam sifatida belgilanish xavfi:** juda ko'p kanalga a'zo bo'lgan, doimiy faol bitta akkount Telegram tomonidan avtomatik tizim sifatida aniqlanish ehtimoli yuqori.

## 10B.2 Yechim — Listener Account Pool

Bitta akkount o'rniga **bir nechta mustaqil Telethon akkount** (turli telefon raqamlar bilan ro'yxatdan o'tgan) ishlatiladi. Har biriga manbalarning bir qismi taqsimlanadi, barchasi bitta umumiy PostgreSQL bazasiga yozadi.

```text
                    channels jadvali (barcha manbalar)
                              │
                    Channel Assignment Service
                              │
        ┌─────────────┬───────┴───────┬─────────────┐
        ▼             ▼               ▼             ▼
   Listener #1    Listener #2    Listener #3    Listener #N
   (Akkount A)    (Akkount B)    (Akkount C)    (Akkount N)
   Kanal 1-50     Kanal 51-100   Kanal 101-150  ...
        │             │               │             │
        └─────────────┴───────┬───────┴─────────────┘
                              ▼
                      Redis Event Queue
                     (umumiy, barcha uchun bitta)
```

## 10B.3 Muhim Texnik Aniqlik — `api_id`/`api_hash` va Akkount Farqi

Bu ikkisi bir xil narsa emas, shuning uchun "credential rotation" strategiyasini aniqlashtirish kerak:

- **`api_id`/`api_hash`** — my.telegram.org orqali olingan **ilova darajasidagi** ro'yxatdan o'tish ma'lumoti. Buni "rotate" qilish yangi ilova ro'yxatdan o'tkazishni anglatadi.
- **Akkount (session)** — haqiqiy telefon raqami bilan bog'langan Telegram foydalanuvchi hisobi, u orqali kanallarga a'zo bo'linadi va xabarlar o'qiladi.

**Telegram cheklovlari (FloodWait, spam belgilash) asosan akkount darajasida qo'llaniladi**, `api_id`/`api_hash` darajasida emas. Shuning uchun asosiy himoya chorasi — **turli, mustaqil akkountlar** ishlatish, faqat bitta ilovaning `api_id`sini "aylantirish" emas.

**Tavsiya etilgan amaliyot:**
- Har bir Listener akkounti uchun alohida `api_id`/`api_hash` **va** alohida telefon raqami/session ishlatilishi tavsiya etiladi (ikkalasini alohida qilish qo'shimcha izolyatsiya beradi).
- Yangi ro'yxatdan o'tgan (yosh) akkountlar ko'proq cheklovlarga uchrashi mumkin — akkountlarni "isitish" (kichik faollikdan boshlab, asta-sekin kanal sonini oshirish) tavsiya etiladi.
- Har bir akkountga taqsimlangan kanal soni cheklangan bo'lishi kerak (aniq son amaliy sinov orqali aniqlanadi), bitta akkountga haddan tashqari yuklama berilmasligi kerak.

## 10B.4 Akkount Sog'ligini Kuzatish (Health Monitoring)

v2.0 hujjatidagi FR-003 (Source Health Monitoring) tamoyili endi **akkount darajasida** ham qo'llaniladi:

| Ko'rsatkich | Tavsif |
|---|---|
| Last Successful Event | Akkount so'nggi marta qachon xabar qabul qilgani |
| FloodWait Count | So'nggi 24 soatda nechta marta FloodWait uchragani |
| Connection Status | Online / Reconnecting / Banned |
| Assigned Channels Count | Akkountga biriktirilgan kanallar soni |

Agar akkount muammoga uchrasa (FloodWait yoki uzoq vaqt javob bermasa), 10A-bo'limdagi Ops Kanaliga darhol xabar yuboriladi va **Channel Assignment Service** avtomatik ravishda o'sha akkountga tegishli kanallarni boshqa sog'lom akkountga vaqtincha qayta taqsimlaydi.

## 10B.5 Dedup bilan Sinergiya

Bir nechta akkount bir xil ochiq kanalga (masalan ikkita akkount ham bir xil public kanalga a'zo bo'lgan holatda, ehtiyot chorasi sifatida) a'zo bo'lgan taqdirda, bir xil xabar ikki marta Queue'ga tushishi mumkin. Bu holat 6-bo'limdagi Pre-AI Filter (xususan `channel_id` + `channel_post` ID kombinatsiyasi orqali) tomonidan avtomatik tutib qolinadi — qo'shimcha maxsus logika kerak emas, chunki bu allaqachon forward/post ID solishtirish mexanizmi doirasiga kiradi.

## 10B.6 Yangi Functional Requirement

**FR-054 — Listener Account Pool.** Tizim bir nechta mustaqil Telethon akkount orqali manbalarni kuzatishi, akkountlar sog'lig'ini alohida kuzatishi va muammoli akkount kanallarini avtomatik boshqa akkountga qayta taqsimlashi kerak.

---

# 11. O'zgarmagan Bo'limlar — Referens

Quyidagi v2.0 hujjatidagi bo'limlar ushbu versiyada o'zgarishsiz saqlanadi va to'liq tafsilotlar uchun v2.0 hujjatiga murojaat qilinadi:

| Bo'lim | v2.0 Manzili | Holat |
|---|---|---|
| Non-Functional Requirements | 3-bo'lim | O'zgarishsiz |
| System Architecture — Core Principles | 4.1–4.3 | O'zgarishsiz |
| Event Flow | 4.4 | O'zgarishsiz (Pre-AI Filter qo'shimcha bosqich sifatida integratsiya qilinadi, 6-bo'lim) |
| Worker Architecture | 4.8 | O'zgarishsiz |
| Queue Architecture | 4.9 | O'zgarishsiz |
| Project Structure | 5-bo'lim | O'zgarishsiz (yangi service'lar: `referral_service.py`, `source_suggestion_service.py` qo'shiladi) |
| Database Design — Core ERD | 6-bo'lim | Asosiy qism o'zgarishsiz, qo'shimchalar 8-bo'limda |
| Telegram Listener Pipeline | 7-bo'lim | O'zgarishsiz (Pre-AI Filter qo'shimcha bosqich) |
| Website Scraper Pipeline | 8-bo'lim | O'zgarishsiz |
| AI Processing Pipeline — Texnik Asos | 9-bo'lim | Asosiy pipeline o'zgarishsiz, Provider strategiyasi 5-bo'limda yangilangan |
| Deduplication Engine — Asosiy Algoritm | 10-bo'lim | O'zgarishsiz, Pre-AI Filter bilan to'ldiriladi (6-bo'lim) |
| Search Engine | 11-bo'lim | O'zgarishsiz, NLP Search kirish qatlami sifatida qo'shiladi (7-bo'lim) |
| Notification System | 12-bo'lim | O'zgarishsiz, Premium ustuvorlik FR-051 orqali qo'shiladi |
| Telegram Bot Architecture — Texnik Asos | 13-bo'lim | Asosiy qism o'zgarishsiz, yangi handler'lar 9-bo'limda |
| REST API Specification | 14-bo'lim | O'zgarishsiz, kelajakda `/referrals`, `/source-suggestions` endpoint'lari qo'shiladi |
| Worker & Queue System | 15-bo'lim | O'zgarishsiz |
| Security | 16-bo'lim | O'zgarishsiz |
| Monitoring & Logging | 17-bo'lim | O'zgarishsiz |
| Docker & Deployment | 18-bo'lim | O'zgarishsiz |
| Testing Strategy | 19-bo'lim | O'zgarishsiz, yangi funksiyalar uchun test case'lar qo'shilishi tavsiya etiladi |

---

# 12. Yangilangan Kelajak Yo'l Xaritasi (Roadmap)

## 12.1 Qisqa Muddat (MVP / Validatsiya Bosqichi)

- 3-5 ta eng katta Telegram kanalni qamrab oluvchi minimal parsing.
- Listener Account Pool (FR-054, 10B-bo'lim) — MVP'da hatto 2 ta akkount bilan boshlash tavsiya etiladi, chunki yagona nuqta nosozligi xavfi erta bosqichda ham muhim.
- Pre-AI Deduplication Filter (6-bo'lim, telefon signali tuzatilgan holda) — birinchi navbatda amalga oshiriladi, chunki xarajat va murakkablikni kamaytiradi.
- Operatsion Monitoring Kanali (FR bo'lmasa ham, 10A-bo'lim) — MVP'ning birinchi kunidanoq ishga tushirilishi tavsiya etiladi, chunki debugging uchun juda arzon va foydali.
- Referral tizimi (FR-047) va Kanal Taklif Qilish (FR-048, qo'lda admin tasdiqlash bilan).
- Tabiiy Tilda Qidiruv (FR-049) — foydalanuvchi tajribasini sezilarli yaxshilaydigan, arzon funksiya sifatida ustuvor.
- Auditoriya Targeting (FR-053, 7A-bo'lim).
- Telegram kanal kontent strategiyasini boshlash (3.3-bo'lim).

## 12.2 MVP'dan Keyingi Birinchi Ustuvor Yo'nalish — Telegram Mini App

MVP validatsiyadan o'tgandan so'ng (asosiy funksionallik bot orqali tasdiqlangandan keyin), **eng birinchi** ishlab chiqiladigan katta qism sifatida **Telegram Mini App** belgilanadi — to'lov tizimi yoki boshqa monetizatsiya funksiyalaridan ham oldin. Sabab: foydalanuvchi tajribasi (UX) botning matn/tugma interfeysiga nisbatan sifat jihatidan sezilarli sakrash qiladi:

- Rasm galereyasi va to'liq ekranli ko'rish.
- Filtrlarni tezroq va vizual tarzda sozlash (slider, checkbox va h.k.).
- Kelajakda xarita orqali qidiruv (v2.0, 20-bo'limda "Map Search" Future Improvement sifatida eslatilgan) uchun tabiiy platforma.
- Bot buyruqlariga qaraganda tezroq navigatsiya.

Mini App mavjud REST API (v2.0, 14-bo'lim) ustida quriladi — backend arxitekturasida qo'shimcha o'zgarish talab qilinmaydi, faqat yangi frontend qatlami qo'shiladi.

## 12.3 O'rta Muddat (Bosqich 2 — Dastlabki Monetizatsiya)

- Haftalik/oylik Premium obuna narxlarini A/B test orqali belgilash.
- To'lov tizimi integratsiyasi (Click/Payme).
- Kanal Taklif Qilish jarayonini qisman avtomatlashtirish (obunachi soni/faollik bo'yicha oldindan filtr).
- Referral fraud'ni oldini olish mexanizmlarini kuchaytirish.
- Listener Account Pool'ni kengaytirish (ko'proq akkount, avtomatik "isitish" jarayoni).

## 12.4 Uzoq Muddat (Bosqich 3 — Ikki Tomonlama Bozor)

- Egalar/agentlar uchun Promoted Listing funksiyasi (FR-052).
- Web Dashboard orqali egalar/agentlar o'z e'lonlarini boshqarishi.
- Analytics va Recommendation System (v2.0, 20-bo'lim asosida).

---

# 13. Xulosa

Ushbu v3.1 hujjati v2.0'dagi puxta ishlab chiqilgan texnik arxitekturani saqlab qolgan holda, loyihaga quyidagi muhim yangi o'lchamlarni qo'shadi:

1. **Bozor validatsiyasi** — muammoning realligi va moliyaviy signal orqali tasdiqlangani.
2. **Aniq, bosqichma-bosqich biznes model** — ishonchdan boshlab, monetizatsiyaga tabiiy o'tish.
3. **Amaliy o'sish strategiyasi** — referral, kanal taklif qilish, kontent marketing orqali organik o'sish.
4. **AI xarajatlarini optimallashtirish** — OpenRouter, ko'p bosqichli model strategiyasi va Pre-AI Filter orqali xarajatni minimal darajaga tushirish.
5. **Foydalanuvchi tajribasini yaxshilovchi yangi funksiyalar** — Tabiiy Tilda Qidiruv va Auditoriya Targeting.
6. **Duplicate aniqlash mantig'idagi jiddiy tuzatish** — telefon raqami endi yolg'iz holda duplicate signal sifatida ishlatilmaydi (maklerlar bir raqamni ko'p e'londa ishlatishi tufayli).
7. **Operatsion barqarorlik** — alohida monitoring kanali va ko'p akkountli Listener Pool orqali tizim ishonchliligini oshirish.
8. **UX ustuvorligi** — Telegram Mini App MVP'dan keyingi birinchi katta investitsiya sifatida belgilangan.

Loyiha endi nafaqat texnik jihatdan puxta, balki bozor, biznes va operatsion barqarorlik nuqtai nazaridan ham asoslangan holatga keldi. Keyingi qadam — MVP'ni tezroq ishga tushirish va real foydalanuvchi metrikalarini (activation rate, retention, referral conversion, notification engagement) kuzatib borish orqali ushbu gipotezalarni raqamlar bilan tasdiqlashdir.
