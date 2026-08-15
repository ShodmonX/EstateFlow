# Sprint 1 - Listener va Ingestion: AI Agent Promptlari

## Qollash tartibi

Promptlarni `1.1` dan `1.5` gacha navbat bilan yuboring. Har bir keyingi prompt oldingisining testlari yashil bo'lgandan keyin bajariladi. Bu sprintda kamida ikki Telethon account bilan ishlash arxitekturasi quriladi, lekin testlar real Telegram credentiallariga qaram bo'lmasligi shart.

## Umumiy kontekst

```text
Sen EstateFlow loyihasining lead async Python muhandisisan. Loyiha Telegram manbalaridan ko'chmas mulk e'lonlarini yig'adi, ularni Redis queue orqali AI processing pipeline'ga uzatadi. Sprint 1 maqsadi: kamida ikki listener account orqali xom xabarlarni qabul qilish, album/media'ni to'g'ri guruhlash va aniq dublikatlarni AI'ga yubormaslik.

Avval repository, `docs/real_estate_sprint_plan.md`, `docs/real_estate_v3.md`, `docs/real_estate_changelog.md` va `docs/real estate.md`ni o'qi. Qarorlar ustuvorligi: changelog > v3 > base document. Mavjud uncommitted o'zgarishlarni saqla.

Muhim qoidalar: listener accountning sessioni va credentiallari hech qachon database, log yoki gitga yozilmaydi. Telefon raqami duplicate qarorining yagona sababi bo'lmaydi va Pre-AI filter'da hech qachon AI skip trigger bo'lmaydi. Noaniq dublikat AI pipeline'ga o'tadi. Barcha external Telegram testlari fake client yoki fixture bilan bajariladi.

Har task oxirida o'zgargan fayllar, testlar, natijalar va qolgan risklarni hisobot qil.
```

## Prompt 1.1 - Listener account pool va source assignment

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 1 uchun listener account pool va kanal taqsimlashning minimal, productionga kengaytiriladigan asosini qur.

Vazifa:
1. Avval Sprint 0 config, database/migration va service qatlamini audit qil. Mavjud model bo'lsa, unda ishlagin; yo'q bo'lsa faqat ingestion uchun zarur minimal migration va repositorylarni qo'sh.
2. Telegram listener account metadata'sini credentialdan ajrat. Account uchun immutable identifier, enabled/health status, last successful event, FloodWait hisoblagichi va assigned channel count saqlanishi mumkin; phone number, session secret yoki bot token saqlanmasin.
3. Telegram channel/source registry va account-channel assignment modelini yarat. Bir kanal bir vaqtda bitta active listenerga tayinlansin; assignment tarixini yoki audit ma'lumotini yo'qotma.
4. Deterministik oddiy assignment service yoz: kam yuklangan sog'lom accountga kanal berish, disabled/unhealthy accountdan kanallarni sog'lom accountga vaqtincha qayta taqsimlash. MVP uchun kamida 2 account konfiguratsiyasini qo'llab-quvvatla.
5. Account health state machine'ini aniq belgilab implement qil: online, reconnecting, flood_wait, disabled/banned kabi holatlar hamda transitionlar. FloodWait yoki uzoq sukutda Ops event chiqarilsin, lekin avtomatik retry Telegram cheklovini buzmasin.
6. Assignment deterministikligi, failover, disabled account va credentiallarning persistencega tushmasligi uchun test yoz.

Qabul mezonlari:
- Ikki healthy account bo'lganda kanallar muvozanatli taqsimlanadi.
- Bitta account unhealthy bo'lsa, uning kanallari xavfsiz qayta tayinlanadi.
- Session path yoki secretlar database/model serialization/log payloadida yo'q.
- Real Telegramsiz to'liq testlanadi.
``` 

## Prompt 1.2 - Telethon listener adapter va message lifecycle

```text
Yuqoridagi umumiy kontekstga amal qil. Account pool contracti ustida Telethon listener adapterini implement qil.

Vazifa:
1. Telethon'ni business servicelardan ajratadigan adapter/interface yarat. Har enabled account uchun mustaqil listener lifecycle bo'lsin: connect, subscribe to assigned channels, reconnect, graceful shutdown va health reporting.
2. Xabar kelganda source channel ID, source message ID, account ID, timestamp, forward metadata, edit/deletion metadata, xom text va media reference'larni standart raw eventga o'tkaz. Raw event schema versioni va idempotency keyi bo'lsin.
3. Aynan bir source channel + message ID ikkinchi marta kelganda queuega ikki marta yuborilmasin. Edit va delete eventlari yangi e'lon deb talqin qilinmasin; ularning alohida update/delete semanticsini saqla.
4. Connection failure, FloodWait va authorization errorlarni account health state bilan bog'la. Ops notifierga safe, PII'siz event yubor; account sessionini log qilma.
5. Listenerni unit-testable qil: Telethon clientni dependency injection orqali fake client bilan almashtirish mumkin bo'lsin. New message, duplicate delivery, edit, delete, reconnect va FloodWait testlarini qo'sh.

Qabul mezonlari:
- Listener eventni faqat adapter orqali tashqi dunyodan qabul qiladi.
- Idempotency key reliable va testlangan.
- Message edit/deletion alohida event bo'lib qoladi.
- Telegram xatosi boshqa account listenerlarini yiqitmaydi.
```

## Prompt 1.3 - Media buffer, debounce va Redis raw queue

```text
Yuqoridagi umumiy kontekstga amal qil. Telegram listenerdan kelgan xabarlarni album/media buffer va Redis queue orqali ishonchli ingestion oqimiga aylantir.

Vazifa:
1. Album/media group uchun buffer yoz. Bir albomdagi bir nechta Telegram event bitta canonical raw ingestion eventga birlashsin; guruh ID yo'q bo'lsa oddiy xabar mustaqil event bo'lib qolsin.
2. Debounce siyosatini konfiguratsiyadan boshqar: albomning keyingi media xabarlari kelishi uchun cheklangan vaqt kut, keyin bitta eventni publish qil. Race condition va bir guruhning ikki marta flush bo'lishidan himoyalan.
3. Media yuklashni listener hot pathdan ajrat. Queue payloadga kerakli, serializatsiyalanadigan media reference/metadata o'tsin; katta binary ma'lumot Redisga yozilmasin. Temporary media lifecycle va cleanup strategiyasini aniq qil.
4. Typed raw event schema, queue publisher interface, retry/idempotency hamda dead-letterga tayyor error contractini yarat. Queue nomlari va payload versionlari keyingi AI worker uchun hujjatlashtirilsin.
5. Bir account yoki ikki account aynan bitta public kanal xabarini ko'rib qolsa, raw queue'da faqat bitta logical event qolishini ta'minla.
6. Fake Redis yoki test container yordamida single post, album, debounce race, restartdan keyingi idempotency va publish failure testlarini yoz.

Qabul mezonlari:
- Album bitta AI-ready raw event sifatida chiqadi.
- Redisda katta media binary saqlanmaydi.
- Queue eventi versionlangan, idempotent va keyingi worker uchun yetarli metadata'ga ega.
- Failure holatida xabar jim yo'qolmaydi.
```

## Prompt 1.4 - Pre-AI deduplication filter

```text
Yuqoridagi umumiy kontekstga amal qil. AI workerga yuborishdan oldingi konservativ, arzon Pre-AI Deduplication Filter'ni implement qil.

Vazifa:
1. Toza interface yarat: input - raw ingestion event, output - `proceed_to_ai`, `high_confidence_duplicate` yoki `needs_reviewable_signal` kabi aniq qaror va sabablar. Qarorlar audit/log uchun signal tafsilotlari bilan saqlansin.
2. Quyidagi signallarni implement qil: forward metadata'dagi original channel/post mosligi; normalizatsiyalangan text SHA-256 exact hash; SimHash yoki MinHash/Jaccard orqali near-duplicate matn; telefon regex extraction; rasm pHash. Text normalizatsiyasi emoji, ortiqcha whitespace va hashtaglar sabab false negative bo'lmasligini ko'zda tutsin.
3. Faqat juda yuqori ishonchli holatda AI skip qil: aynan forward origin mosligi yoki aynan normalized text hash mosligi. Faqat pHash o'xshashligi, near-text match yoki telefon raqami hech qachon skip trigger bo'lmasin.
4. Telefonni faqat yordamchi signal sifatida saqla. Bitta agent bir raqamni turli kvartiralarda ishlatishi mumkin; `same phone only` natijasi yangi/noaniq e'lon bo'lishi shart.
5. pHash va near-text thresholdlarini config orqali boshqar, benchmark/tuning uchun qaror metrikalarini Ops event sifatida chiqar. AI skip bo'lsa, mavjud parent yoki raw source bilan bog'lash uchun yetarli audit reference saqla; data yo'qotma.
6. Test fixturelarda quyilarni isbotla: forward duplicate, exact text duplicate, pHash-only, near-text-only, same-phone-different-apartments va true multi-signal duplicate.

Qabul mezonlari:
- False positive xavfi uchun filter konservativ: noaniq event AI pipeline'ga o'tadi.
- Telefon raqami yolg'iz holda hech qachon duplicate/skip qarori bermaydi.
- Har skip qarori tekshiriladigan sabab va parent/source reference'ga ega.
- Signal thresholdlari kodga qotirib qo'yilmagan.
```

## Prompt 1.5 - Ingestion end-to-end sinovi va Sprint 1 handoff

```text
Yuqoridagi umumiy kontekstga amal qil. Sprint 1 komponentlarini bitta integration flowda tekshir va keyingi AI sprintiga handoff qil.

Vazifa:
1. Ikki fake Telethon account, bir nechta assigned source channel, single message va media album bilan to'liq test flow yarat: listener -> buffer/debounce -> raw queue -> Pre-AI filter.
2. Quyidagi failurelarni test qil: bir account FloodWaitga tushishi, kanal failoveri, bir xabar ikki accountdan kelishi, Redis vaqtincha ishlamasligi, exact duplicate va ambiguous duplicate.
3. Ops eventlarining health/failover/duplicate uchun chiqishini, ammo PII va secretlar yo'qligini tekshir.
4. AI worker uchun raw queue payload contractini `docs/`da qayd qil: schema version, majburiy maydonlar, media reference, idempotency key, retry va filter qarori. Contract koddagi schema bilan bir xil bo'lsin.
5. Faqat testda aniqlangan ingestion muammolarini tuzat; AI parsing, R2 upload yoki post-AI deduplicationni bu sprintga tortma.

Qabul mezonlari:
- 2-account ingestion oqimi real Telegramsiz takrorlanadigan integration test bilan qoplangan.
- Aniq duplicate AI queue'ga o'tmaydi, noaniq duplicate esa o'tadi.
- Sprint 2 AI workerga kerak bo'ladigan contract hujjatlashtirilgan va testlangan.
```
