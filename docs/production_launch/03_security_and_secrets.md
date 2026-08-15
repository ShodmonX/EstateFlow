# Security and Secrets Plan

## Objective

Secrets, admin access, session files, and logs uchun production-safe posture yaratish.

## Tasks

- `.env` productionda ishlatilmasin.
- Repository va deployment artifactlarda secret leak scan o'tkazish.
- Telethon `.session` fayllari git track qilinmasin.
- Session directory permission tekshiruvi o'tkazish.
- Admin API faqat token bilan ochilsin.
- Feature flag mutation audit log bilan qayd etilsin.
- Log redaction PII va secret values ni yashirsin.
- Ops bot va notification credentials faqat zarurat bo'lsa yoqilsin.

## Exit criteria

- Secret scan clean.
- Admin route auth verified.
- Session file path restricted.
- No sensitive values logs orqali chiqmaydi.

