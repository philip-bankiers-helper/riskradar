# RiskRadar Progress

Entries are append-only.

## 2026-09-07 — Week 0

- Re-homed the project to `/Users/kairox/riskradar` on the Mac Studio with Python 3.12 and a locked environment.
- Repaired the pandas 3 attribution failure and the daily-summary scheduler, moved credentials to environment-only configuration, added Telegram topic routing, and made loopback binding the default.
- Recalibrated 1,869 real yfinance trading days with nonzero factor concentration in every row. The derived warm/hot/critical/emergency thresholds are 0.4636/0.6732/0.8029/0.8781.
- Verified the blocking suite (`219 passed, 1 deselected`), battle suite (`49/49`), holdouts (`4 passed`), and committed test floor (`225`).
- Installed and started `com.kairox.riskradar` on `127.0.0.1:8001`; port 8000 remains owned by an unrelated existing MailAI service.
- Created Telegram topic 4799 and independently read back the verified daily-summary message 4800.
- W1 remains open until the launchd service has produced seven consecutive dated 16:30 ET summaries with the suite still green.

## 2026-09-08 — W1 night 2

- Verified the launchd service armed its first scheduled 16:30 ET slot for TODAY 2026-09-08 (installed 22:32 CT Sep 7, after Sep 7's slot had passed). Streak of service-delivered scheduled summaries: 0; day 1 of 7 fires 16:30 ET today.
- Fixed a W1-killing bug found by inspection: a manual daily-summary send (yesterday 23:37 ET) set the 1440-min DAILY_SUMMARY cooldown ~16h ahead and would have silently suppressed today's scheduled summary. Scheduled sends now bypass the cooldown; manual sends remain rate-limited.
- Added persistent delivery evidence: every successful daily summary appends to data/delivery_log.jsonl (ET date, message_id, heat, scheduled flag; gitignored runtime state). scripts/delivery_streak.py reports the W1 consecutive-day streak. Backfilled the verified 2026-09-07 manual summary (message 4800) as scheduled=false.
- Scheduler logging made honest: message_id on success, explicit error lines on send failure or skip.
- Tests: 238 passed, 1 deselected (was 219). Floor check 244 vs baseline 225. Secrets scan clean.
- Service restarted onto the new code ahead of the 16:30 ET slot (new PID, scheduler re-armed, port 8001 answering).
- W1 waiting on: first scheduled delivery today 16:30 ET, then six more consecutive days. No input needed from Philip.
