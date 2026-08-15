# Staging and Dry Run Plan

## Objective

Productionga yaqin muhitda real launchdan oldin behavior va load ni tekshirish.

## Tasks

- Stagingda real env values bilan ishga tushirish.
- Real user va real Telegram sendlarsiz test qilish.
- Queue backlog behavior tekshirish.
- Search latency kuzatish.
- Notification retry and failure paths tekshirish.
- AI fallback va validation failure metrics tekshirish.
- Ops notification routing tekshirish.

## Exit criteria

- `/health/live` va `/health/ready` stagingda green.
- Dry-run evidence saved.
- No external call leakage.

