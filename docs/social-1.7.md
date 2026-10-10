# Server 1.7

Prepared protocol 5/private chat on fc2b948317b5b04fe51e87c34b0d2ab2a18830cf. Production is unchanged protocol 3/main 1140db8b2d5e1f10b88fd93db1fdc1b27c89790e. Backup social.db and leaderboard.db before deploying; use persistent storage and DODGE_SOCIAL_DB / DODGE_DB paths. Free Render ephemeral storage/sleep cannot retain chat or guarantee streak warning deadlines. No paid infrastructure created.

Build: pip install -r requirements.txt. Start: python online_server_render.py. FCM HTTP v1 needs GOOGLE_APPLICATION_CREDENTIALS pointing to a server-only service account secret file and an enabled Firebase project. Do not publish this key. Android requires public FIREBASE_ANDROID_CONFIG and phone permission.

Chat uses authenticated /v2/action, POST /v2/upload/photo|voice, GET /v2/media/<id>, POST /v2/push. Streak calendar: Asia/Qyzylorda. Messages/requests/blocks/privacy/settings stored in existing Social SQLite file with additive schema. No old tables dropped. Participant access and quotas enforced. Nonce retry is idempotent. Warnings have one persisted attempt; delivery loss after timeout possible.

Style ids are validated, sender style snapshot is persisted, but cosmetic coin ownership remains client-side; server wallet verification is not implemented. Shared quest counts client-reported result cards, not authoritative battle completions. Reports are persisted; no moderation dashboard.

27 local server regression tests passed. Real local HTTP two-account chat/media/access tests passed; fake FCM provider checks payload only. No live 1.7 Render deployment or Android closed-app push test.

Full Russian audit, phone checklist and all changed client files: https://github.com/DoDgeClan/DodgeTheEnemies-Kivy/blob/feature/social-pixel-1.7/docs/audit-1.7.md

Server source branch: feature/social-1.7. Rollback: backup/pre-1.7-20261003.
