# Real Estate Super Aggregator — O'zgarishlar Jurnali (Changelog)

Ushbu fayl asosiy loyiha hujjatiga (`real_estate_v3.md`, joriy holat: **v3.1**) nisbatan keyinchalik qabul qilingan barcha yangi qarorlar va o'zgarishlarni qisqa versiya yozuvlari sifatida qayd etadi.

**Qoida:** Asosiy hujjat endi har safar qayta yozilmaydi. Bu yerga faqat farqlar (nima o'zgardi, nima qaror qilindi) yoziladi. Bir nechta o'zgarish to'plangandan so'ng, ular birlashtirilib, asosiy hujjatning navbatdagi katta versiyasiga (masalan v4.0) integratsiya qilinadi.

---

## v3.2 — E'lon Narxini Parsing Qilish: Davr va Asos Ajratish

**Sana:** 2026-07-30
**Ta'sir qiladigan qism:** v3.1 hujjatining Appendix B (Standard AI JSON Schema) va 11-bo'lim (Search Engine, v2.0 referens)

### Muammo

E'lonlarda narx turlicha ifodalanadi:
- **Davr bo'yicha:** oylik ("oyiga 500$"), kunlik ("sutkasiga 30$", qisqa muddatli ijara), yoki bir martalik (sotish narxi).
- **Asos bo'yicha:** umumiy (butun kvartira uchun) yoki kishiga (masalan bir nechta kishi bo'lib ijaraga oladigan xona uchun, "4 ta qizga" turidagi e'lonlarda uchraydi).

Buni farqlamasdan Search Engine'da narx bo'yicha filtrlash (masalan "$400 gacha") noto'g'ri natija berishi mumkin.

### Qaror — Yangi Schema Maydonlari

AI JSON Schema'ga (v2.0 Appendix B, B.3) ikkita yangi maydon qo'shiladi:

```json
{
  "price_period": "monthly",
  "price_basis": "total"
}
```

| Maydon | Qiymatlar | Standart (aniq ko'rsatilmasa) |
|---|---|---|
| `price_period` | `daily`, `monthly`, `one_time` | `monthly` |
| `price_basis` | `total`, `per_person` | `total` |

AI ushbu maydonlarni faqat matnda **aniq ko'rsatma** bo'lganda to'ldiradi (masalan "sutkasiga", "kishiga" kabi so'zlar), taxmin qilmaydi — bu v2.0'dagi B.2 General Rules tamoyiliga mos.

### Normalizatsiya Qoidasi

- **Kunlik → Oylik:** avtomatik hisoblanadi (`price × 30`), xavfsiz matematik amal. Natija `price_normalized_monthly` degan qo'shimcha maydonda saqlanadi va Search Engine narx filtrlarida shu maydon ishlatiladi.
- **Kishiga → Umumiy:** **avtomatik konversiya qilinmaydi** — chunki kishilar soni har doim aniq bo'lmaydi, noto'g'ri hisoblash xavfi bor. Buning o'rniga:
  - Foydalanuvchiga narx yonida aniq ko'rsatiladi (masalan "100$/kishiga").
  - Search Engine'ga yangi filtr qo'shiladi: foydalanuvchi "faqat umumiy narxli e'lonlar" yoki "kishiga narxlarni ham ko'rsat" tanlovini qila oladi.

### Database Ta'siri

`announcements` jadvaliga (v2.0, 6.7-bo'lim) qo'shimcha ustunlar:

| Column | Type | Description |
|---|---|---|
| price_period | ENUM | daily / monthly / one_time |
| price_basis | ENUM | total / per_person |
| price_normalized_monthly | DECIMAL | Faqat daily→monthly konvertatsiya uchun, nullable (one_time uchun bo'sh) |

### Ochiq Savol (keyingi muhokama uchun)

Sotish (`one_time`) e'lonlari uchun umuman alohida `listing_type` (rent/sale) maydoni hozircha asosiy hujjatda aniq ko'rsatilmagan — bu keyingi versiyada aniqlashtirilishi tavsiya etiladi.

---

## v3.3 — Auditoriya Targeting: Dinamik Enum Jadvali va Ierarxiya

**Sana:** 2026-07-30
**Ta'sir qiladigan qism:** v3.1 hujjatining 7A-bo'limi (Auditoriya Targeting) — ushbu qaror 7A-bo'limni **almashtiradi/kengaytiradi**

### Muammo

v3.1'dagi dastlabki dizayn (7A.3-bo'lim) auditoriya teglarini **qattiq belgilangan (hardcoded) ro'yxat** sifatida taklif qilgan edi. Amalda esa:
- E'lonlarda auditoriya ifodalari juda xilma-xil bo'ladi ("4 ta qizga", "chet el fuqarolariga", va hokazo — oldindan barchasini bashorat qilib bo'lmaydi).
- Teglar orasida **ierarxik munosabat** bor — masalan `young_family` (yosh oila) `family` (oila)ning bir turi hisoblanadi.
- Agar ro'yxat cheksiz kengaysa, har bir AI so'roviga to'liq ro'yxatni yuborish xarajatni nazoratsiz oshirib yuboradi.

### Qaror — Yangi Arxitektura

**1. Qattiq ro'yxat o'rniga dinamik `audience_tag_types` jadvali:**

| Column | Type | Description |
|---|---|---|
| id | UUID | Primary Key |
| tag_key | VARCHAR | Mashina uchun kalit (masalan `young_family`) |
| display_name_uz | VARCHAR | Foydalanuvchiga ko'rinadigan nom |
| parent_tag_id | UUID (FK, nullable) | Ierarxiya uchun — kengroq kategoriya |
| status | ENUM | `approved` / `pending` |
| usage_count | INTEGER | Necha marta ishlatilgani (optimizatsiya uchun) |
| created_at | TIMESTAMP | Yaratilgan vaqt |

**2. Ierarxiya misoli:**

```
family (root)
 ├── young_family
 └── family_with_children
```

**3. AI Prompt Strategiyasi (xarajat nazorati):**

Har bir so'rovga butun jadval emas, faqat **`usage_count` bo'yicha eng yuqori 10-15 ta `approved` teg** yuboriladi. Promptga qo'shiladi: *"Agar mos teg ro'yxatda bo'lmasa, yangi teg nomini o'zing taklif qil (snake_case, masalan `military_personnel`)."* Bu — jami teglar soni qancha o'sishidan qat'iy nazar, prompt hajmini (demak xarajatni) bir xil darajada ushlab turadi.

**4. Yangi teg qo'shilishida — Fuzzy Matching orqali duplikatni oldini olish:**

AI "yangi" teg qaytarganda, backend uni darhol qo'shmaydi:
- Avval mavjud teglar bilan matn o'xshashligi solishtiriladi (v3.1'dagi 6.2.c bo'limidagi SimHash/matn moslashtirish mexanizmi qayta ishlatiladi — alohida yangi kod yozilmaydi).
- O'xshashi topilsa → mavjud tegga bog'lanadi, `usage_count += 1`.
- O'xshashi topilmasa → `status = pending` bilan yangi qator qo'shiladi.

**5. Admin Tasdiqlash Jarayoni:**

`pending` teglar Admin Panel'da (v2.0, 4.2.10-bo'lim) ko'rinadi. Admin: tasdiqlaydi (`approved` bo'ladi, qidiruv filtrida ko'rinadi) / boshqa tegga birlashtiradi (merge) / rad etadi (o'chiriladi).

**6. Qidiruv Mantig'i — Ierarxiya Bilan Yangilanadi (v3.1, 7A.6-bo'limini almashtiradi):**

```text
Foydalanuvchi tag X bo'yicha qidirsa:

    KO'RSATILADI, agar e'londa:
        X ning o'zi audience_tags ichida bor
        YOKI X ning istalgan ota-tegi (parent) audience_tags ichida bor
    VA
        X (yoki uning ota-tegi) audience_excluded_tags ichida YO'Q

    Masalan: "young_family" bo'yicha qidiruv →
        "young_family" YOKI "family" deb belgilangan e'lonlar chiqadi.

    Lekin: "family" bo'yicha qidiruv →
        faqat aniq "family" deb belgilangan e'lonlar chiqadi
        ("young_family" alohida ko'rsatilmaydi, chunki
        u tor auditoriya, kengroq so'rovga majburiy mos kelmaydi)
```

### Database Ta'siri

Yangi jadval: `audience_tag_types` (yuqorida). `announcements.audience_tags` va `audience_excluded_tags` endi erkin matn emas, balki `audience_tag_types.tag_key`ga ishora qiluvchi qiymatlar saqlaydi.

### Dastlabki "Urug'" (Seed) Teglar

MVP boshlanishida quyidagi teglar `approved` holatda oldindan kiritiladi (v3.1, 7A.3-bo'limidagi ro'yxat asosida): `family`, `young_family`, `family_with_children`, `students`, `single_male`, `single_female`, `group_of_girls`, `group_of_boys`, `foreigners`. Qolganlari AI orqali dinamik ravishda, real e'lonlar asosida qo'shiladi.

---

## v3.4 — Auditoriya Targeting: Ierarxiya Olib Tashlandi (v3.3'ni Soddalashtirish)

**Sana:** 2026-07-30
**Ta'sir qiladigan qism:** v3.3 yozuvini to'g'irlaydi

### Tuzatish

v3.3'da kiritilgan **ierarxiya tushunchasi (parent-child, `parent_tag_id`) butunlay olib tashlanadi.** Bu ortiqcha murakkablik edi. Asl fikr oddiyroq: "yosh oila" va "oila" — ikkalasi **bitta tur** hisoblanadi, alohida ota-bola tegi emas.

### Yangi (Soddalashtirilgan) Qaror

- `audience_tag_types` jadvalidan **`parent_tag_id` ustuni olib tashlanadi.** Endi bu — oddiy tekis (flat) teglar ro'yxati, ierarxiyasiz.
- "Yosh oilaga" degan matn kelsa, AI buni alohida `young_family` tegi sifatida emas, balki mavjud **`family`** tegiga mos deb belgilaydi. Bu — alohida yangi mantiq emas, v3.3'dagi mavjud **fuzzy-matching/sinonim aniqlash mexanizmi** (6.2.c bo'limidagi matn moslashtirish) orqali tabiiy ravishda hal bo'ladi — "yosh oila" "oila"ning sinonimi sifatida qaraladi, ikkalasi ham bitta `family` tegiga tushadi.
- `family_with_children` (bolali oila) — bu alohida, mustaqil teg bo'lib qoladi (chunki bu haqiqatan boshqa auditoriya — ba'zi egalar bolasiz oilaga rozi, bolali oilaga rozi emas), lekin endi u ham **flat** ro'yxatda, `family`ning "bolasi" sifatida emas.

### Qidiruv Mantig'i — Soddalashtirildi

v3.3'dagi ierarxiya-asosli qidiruv mantig'i (ota-tegni ham tekshirish) **bekor qilinadi.** Endi oddiy to'g'ridan-to'g'ri moslik:

```text
Foydalanuvchi tag X bo'yicha qidirsa:

    KO'RSATILADI, agar:
        X audience_tags ichida bor
        VA X audience_excluded_tags ichida YO'Q

    (Ota-tegni qidirish yoki qo'shimcha tekshirish yo'q —
    chunki "yosh oila" allaqachon parsing bosqichidayoq
    "family" ga birlashtirilgan bo'ladi)
```

### Yangilangan Seed Teglar Ro'yxati

`young_family` alohida teg sifatida olib tashlanadi. Yangi ro'yxat: `family`, `family_with_children`, `students`, `single_male`, `single_female`, `group_of_girls`, `group_of_boys`, `foreigners`.

---

## v3.5 — Rasm Boshqaruvi: S3 Storage, Rasm Tanlash Tuzatildi, Remont signali moslashuvi

**Sana:** 2026-07-30
**Ta'sir qiladigan qism:** v2.0 hujjatining 4.14.7 (Storage Scaling) va v3.1'ning 5.2-bo'limi (Rasm Optimizatsiyasi)

### 1. S3-Compatible Object Storage — MVP'dan Boshlab Qo'shiladi

v2.0'da "kelajak" (Future) sifatida belgilangan Storage Scaling (4.14.7) endi **MVP'ning bir qismi** sifatida qaror qilinadi, chunki keyinchalik Web UI qo'shilganda rasmlarni ko'chirish (migratsiya) qo'shimcha murakkablik va vaqt yaratadi.

- **Tanlangan yechim:** Cloudflare R2 (S3-compatible API, lekin **egress trafigi uchun to'lov yo'q** — rasm ko'rsatish trafigi ko'p bo'lgan loyiha uchun muhim ustunlik).
- `announcement_media.storage_url` (v2.0, 6.10-bo'lim) endi R2 manzilini saqlaydi.
- Bu qaror kelajakdagi Web UI (v3.1, 12.2-bo'lim, Mini App'dan keyingi bosqich) uchun rasm ko'rsatishni to'g'ridan-to'g'ri qo'llab-quvvatlaydi, qo'shimcha migratsiya kerak bo'lmaydi.

### 2. Rasm Tanlash Siyosati — Tuzatildi (v3.1, 5.2-bo'limini to'g'irlaydi)

**Muammo:** v3.1'da "eng informativ 3-4 rasmni tanlab yuborish" tavsiya qilingan edi, lekin bu tavsiya ortida **haqiqiy tanlash algoritmi yo'q edi** — faqat umumiy g'oya edi.

**Yangi qoida:** Agar algoritmik asos bo'lmasa, rasm soni sun'iy ravishda kamaytirilmaydi. Standart siyosat:

- **Barcha rasmlar yuboriladi** (siqilgan/kichraytirilgan holda, lekin sonini cheklamasdan).
- **Yagona qonuniy filtrlash — near-duplicate olib tashlash:** bitta e'lon ichida deyarli bir xil rasmlar (masalan xuddi shu xonaning bir necha marta, deyarli bir xil burchakdan olingan suratlari) pHash orqali aniqlanadi va faqat bittasi qoldiriladi. Bu — Pre-AI Filter uchun qurilgan mavjud pHash infratuzilmasidan (v3.1, 6.2.e-bo'lim) qayta foydalanadi, yangi alohida kod yozilmaydi.
- **"Sifat bo'yicha" (masalan blur/rezolyutsiya asosida "eng chiroyli"ni) tanlash hozircha amalga oshirilmaydi**, chunki bunday algoritm hali loyihalashtirilmagan. Kelajakda zarurat sezilsa, klassik CV evristikasi (Laplacian variance orqali blur aniqlash va h.k. — AI emas, arzon) qo'shilishi mumkin, alohida keyingi qaror sifatida.

### 3. Remont Darajasini Baholash — Vision va text signallari uyg'unligi

**Muammo:** v3.1'ning 5.2-bo'limida umumiy xarajat-optimizatsiya siyosati sifatida "Vision faqat matnda yetishmayotgan ma'lumot bo'lsa ishga tushiriladi, har doim emas" deyilgan edi. Bu siyosat `renovation_level` maydoni uchun **noto'g'ri**, chunki:

- Matndagi "yaxshi remont", "evro remont" kabi ifodalar reklama tili — ishonchsiz.
- Platformaning asosiy qadriyati — shaffoflik va ishonch (v3.1, 2-bo'lim) — bu joyda xarajatni tejashdan ko'ra muhimroq.
- AI xarajati baribir arzon (v3.1, 5.4-bo'lim), tejashning amaliy foydasi kam.

**Yangilangan qoida (5.2-bo'limga istisno sifatida qo'shiladi):** Agar e'londa kamida bitta rasm mavjud bo'lsa, `renovation_level` uchun Vision qo'shimcha signal sifatida ishlatilishi mumkin, ammo majburiy emas. Matn aniq va validated bo'lsa, renovation shu matndan saqlanishi mumkin; manual review faqat confidence yoki boshqa business check'lar sabab bo'ladi.

### Database/Schema Ta'siri

Qo'shimcha ustun talab qilinmaydi — `renovation_level` maydoni allaqachon mavjud (v2.0, Appendix B). Faqat AI Worker'ning ichki mantig'ida valid text va Vision signallari orasida tanlov biznes confidence asosida qilinadi.

---

## v3.6 — Qulaylik Maydonlari (Amenities): Struktura Qilinmaydi

**Sana:** 2026-07-30
**Ta'sir qiladigan qism:** Oldingi muhokamada ko'tarilgan savol — kir mashina, muzlatgich, mikrovalnovka kabi qulayliklar uchun Auditoriya Targeting'ga o'xshash dinamik teg tizimi kerakmi

### Qaror

**Kerak emas — hozircha qoldiriladi.** Bu turdagi qulayliklar universal emas (ko'p e'lonlarda umuman tilga olinmaydi), shuning uchun ularni alohida struktura maydon yoki dinamik teg tizimi (7A/audience_tag_types uslubida) qilish hozirgi bosqichda ortiqcha murakkablik hisoblanadi.

**Amaliy yechim:** Bu ma'lumot alohida ustunlarga ajratilmaydi — mavjud `description` (erkin matn) maydonida saqlanib qolaveradi (v2.0, Appendix B, `description` maydoni). AI bu ma'lumotni yo'qotmaydi, faqat struktura qilmaydi.

### Farq — Nega Auditoriya Targeting'dan Boshqacha?

Auditoriya Targeting (7A-bo'lim) struktura qilingan, chunki u **qidiruv filtri** sifatida ishlatiladi (foydalanuvchi "faqat talabalarga" deb filtrlaydi). Qulaylik maydonlari uchun hozircha bunday **aniq, tasdiqlangan qidiruv talabi yo'q** — shuning uchun ularni struktura qilish investitsiyasi hozircha oqlanmaydi. Agar kelajakda foydalanuvchilar "kir mashinasi bor uylarni ko'rsat" kabi filtrlashni talab qilishi aniqlansa, bu masala 7A'dagi bir xil dinamik teg arxitekturasi asosida qayta ko'rib chiqilishi mumkin.

---


