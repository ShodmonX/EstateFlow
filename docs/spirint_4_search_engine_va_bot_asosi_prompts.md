# Sprint 4 - Search Engine va Bot Asosi: AI Agent Promptlari

## Qollash tartibi

Promptlarni `4.1` dan `4.4` gacha ketma-ket yuboring. Sprint 3 canonical parent query contracti buzilmasin: foydalanuvchi natijalarida child/source duplicates chiqmasligi shart.

## Umumiy kontekst

```text
Sen EstateFlow loyihasining lead product/backend muhandisisan. Sprint 4 maqsadi: foydalanuvchi Telegram bot orqali real canonical e'lonlar bo'yicha qidiruv qila olishi va saqlangan filtrlarini boshqara olishi.

Avval repository va docs/real_estate_sprint_plan.md, docs/real_estate_v3.md, docs/real_estate_changelog.md, docs/real estate.md ni o'qi. Qarorlar ustuvorligi: changelog > v3 > base document. Mavjud kod uslubini davom ettir va uncommitted o'zgarishlarni saqla.

Qidiruv narx uchun `price_normalized_monthly`ni ishlatadi; `daily` narxlar shu maydonda 30 kunlik qiymatga aylantirilgan. `one_time` sale qiymatlarini monthly rent queryga aralashtirma. `per_person` narxlar umumiy narxga aylantirilmaydi. Audience targeting flat/direct match: requested tag `audience_tags` ichida bo'lishi va `audience_excluded_tags` ichida bo'lmasligi kerak. Amenities hozircha filter emas.

Har task oxirida changed files, qarorlar, test/buyruq natijalari va bloklovchilarni yoz.
```

## Prompt 4.1 - Search domain, repository va API contract

```text
Yuqoridagi umumiy kontekstga amal qil. Canonical announcementlar uchun fast, validatsiyalangan search domain va REST API contractini implement qil.

Vazifa:
1. Mavjud announcements schema, parent-child relation, FastAPI routers va repository patternni tekshir. Business filteringni endpointdan ajratib, typed search criteria/query object yarat.
2. Quyidagi MVP filterlarini implement qil: monthly normalized price range, price basis preference (faqat total yoki per-personni ham ko'rsatish), district, rooms, renovation level va audience tag. Default query faqat active parent/canonical listinglarni ko'rsatsin.
3. `one_time` listinglarni rent/monthly price filteriga kiritma. Null/missing price va per-person narx uchun xavfsiz, tushunarli query semanticsini belgilab, API response metadata'da ko'rsat.
4. Audience query uchun direct flat matchingni implement qil: tag accepted bo'lsa resultda shu tag target listda bo'lishi va exclude listda bo'lmasligi kerak. Hierarchy, parent tag yoki implicit `young_family` qidiruv kengaytmasini qo'shma.
5. Pagination, stable sort va max page size validation qo'sh. Mavjud full text search bo'lsa, uni canonical query bilan birlashtir; bo'lmasa bu sprintda keraksiz external search engine kiritma.
6. PostgreSQL index va query planlarni tekshir. SQLAlchemy parametrizatsiyasi va explicit allowed sort fieldlar bilan injectiondan saqla.
7. API/service/repository uchun filters, price edge cases, audience exclusion, parent-only results, pagination va invalid input testlarini yoz.

Qabul mezonlari:
- Search bir xil kvartirani bitta canonical result sifatida qaytaradi.
- Narx filtering daily/monthly/one_time/per_person farqlarini buzmaydi.
- Audience matching faqat flat/direct qoidaga amal qiladi.
- API contract keyingi bot qatlamiga yetarli va testlangan.
```

## Prompt 4.2 - Aiogram bot core va asosiy menu

```text
Yuqoridagi umumiy kontekstga amal qil. Search API/service contracti ustida Telegram botning foydalanuvchi uchun asosiy qatlamini implement qil.

Vazifa:
1. Mavjud Aiogram arxitekturasini tekshir yoki minimal router/dispatcher/DI integratsiyasini qo'sh. Bot token faqat Settings orqali olinsin; testlarda fake bot/session ishlat.
2. First-time user registration/upsert flow yarat: Telegram user ID asosida minimal user record yaratiladi, lekin kerak bo'lmagan profil yoki PII yig'ilmaydi.
3. Main menu yarat: Qidiruv, Saqlangan filtrlar, Bildirishnomalar, Do'stlarni taklif qilish, Kanal taklif qilish, Sozlamalar, Yordam. Sprint 4da hali implement qilinmagan menu itemlari uchun foydalanuvchiga aniq "tez orada" yoki disabled holat ko'rsat; broken callback qoldirma.
4. Callback data compact, versionlangan va forged/expired payloadga chidamli bo'lsin. Har handler owner/user authorizationini tekshirsin.
5. Common UX flowsni qo'sh: `/start`, cancel/back, global error handler va empty state. Xatolik ichki detail yoki secretni foydalanuvchiga chiqarmasin; correlation ID logda qoladi.
6. Bot router, registration, menu navigation, callback validation va error/cancel flow uchun test yoz.

Qabul mezonlari:
- User botga kirib main menuni ishonchli ko'radi.
- Bot handlerlari business logicni bevosita olib yurmaydi.
- Hali tugallanmagan feature buttonlari xatosiz, aniq holatda.
- Token va Telegram networkga qaram bo'lmagan testlar ishlaydi.
```

## Prompt 4.3 - FSM search wizard va result rendering

```text
Yuqoridagi umumiy kontekstga amal qil. Telegram botga bosqichma-bosqich qidiruv wizardini va canonical result paginationini qo'sh.

Vazifa:
1. Aiogram FSM yoki mavjud state mechanismda deterministic wizard yarat: district, rooms, monthly budget, price basis preference, renovation va audience tag. Har qadam skip/back/cancel imkoniga ega bo'lsin va state userga tegishli bo'lsin.
2. Input validationni har qadamda qil: price format/range, known district/tag, button callback va erkin text xatolari. Uzb/rus/aralash son formatlari uchun kichik normalizatsiya bo'lishi mumkin, ammo AI natural-language parsing bu sprintga kirmaydi.
3. Final criteria'ni Search servicega uzat, hech qachon botdan to'g'ridan-to'g'ri SQL qilma. Result cardda canonical listingning kerakli ma'lumoti: narx va period/basis, district, rooms, renovation, qisqa description, source count va media/link mavjudligi xavfsiz ko'rsatilsin.
4. Inline paginationni implement qil: stable page/cursor, one userga tegishli callback, stale result yoki listing o'chirilgan holatda graceful refresh. Bir listing child duplicate ko'rinishida chiqmasin.
5. Zero-results state foydalanuvchiga filterlarni o'zgartirish yoki reset qilish yo'lini bersin. Long result/text Telegram limitidan oshmasin.
6. Testlarda wizard happy path, back/cancel, invalid price, audience exclusion, no result, duplicate-free rendering va pagination authorizationni qamra.

Qabul mezonlari:
- Foydalanuvchi oddiy filterlar bilan qidiruvni boshidan oxirigacha yakunlaydi.
- Qidiruv criteria API domain bilan bir xil semanticsda ishlaydi.
- Result navigation xavfsiz va duplicate-free.
- FSM state bir userdan boshqasiga o'tmaydi.
```

## Prompt 4.4 - Saved filters va Sprint 4 quality gate

```text
Yuqoridagi umumiy kontekstga amal qil. Saved Filters CRUD'ni bot va backendda yakunla, so'ng Sprint 4 end-to-end sifat tekshiruvini o'tkaz.

Vazifa:
1. User filter model, repository va service contractini audit qil. Filter criteria SearchCriteria bilan bir xil typed schema orqali saqlansin; keyinchalik notification matching bu contractdan foydalana olsin.
2. Create, list, view, update, enable/disable va delete imkonlarini implement qil. Har operation user ownershipini qat'iy tekshirsin, nom/limit va invalid criteria validationiga ega bo'lsin.
3. Botda saved filter management UX yarat: qidiruv natijasidan saqlash, saved filterlar ro'yxati, tahrirlash uchun mavjud wizard yoki aniq replacement flow, delete uchun tasdiqlash. Callbacklarni userga bog'la.
4. Search criteria migratsiyasi/versioni kerak bo'lsa backward-compatible policy yoz. Free-form JSONni validatsiyasiz persist qilma.
5. Integration test yoz: user wizard orqali qidiradi -> filter saqlaydi -> ro'yxatda ko'radi -> tahrirlaydi -> qayta qidiradi -> o'chiradi. Boshqa user filterga kira olmasligini ham isbotla.
6. Sprint 4 qabul mezonlarini audit qil va faqat aniqlangan search/bot gaplarni tuzat. Notification, NLP va referralni Sprint 5ga qoldir. Bot UX va saved-filter contractini `docs/`da qisqacha qayd qil.

Qabul mezonlari:
- Saved filterlar qidiruv bilan bir xil schema va semanticsdan foydalanadi.
- CRUD ownership, validation va test bilan himoyalangan.
- Sprint 5 notification worker filterlarni ishonchli o'qiy oladi.
```
