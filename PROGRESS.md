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

## 2026-09-09 — W1 night 3

- Verified yesterday's scheduled delivery independently: launchd service delivered the 16:30:00 ET summary for 2026-09-08 (message 4823, heat 0.561) to thread 4799; delivery log + scheduler log agree. Scheduled streak: 1 day; today 16:30 ET is day 2 of 7.
- One step: daily-summary header now dates itself in America/New_York ("YYYY-MM-DD HH:MM ET") instead of UTC, matching delivery-log day keys so each delivered message is self-evidently a dated ET summary (W1 done_when wording).
- Suite green before and after: 239 passed, 1 deselected (+1 new test). Floor 245 vs baseline 225. Secrets scan clean.
- Deployed 6e6b596 to the service ahead of today's slot: new pid 42360, /health 200, scheduler re-armed for 16:30 ET. Pushed c92f835..6e6b596 to origin/main.
- W1 waiting on: today's 16:30 ET delivery (day 2), then five more consecutive days. No input needed from Philip.

## 2026-09-10 — W1 night 4

- Verified yesterday's scheduled delivery: launchd service delivered the 16:30:00 ET summary for 2026-09-09 (message 4837, heat 0.559) to thread 4799; delivery log and scheduler log agree. Scheduled streak: 2 days; today 16:30 ET is day 3 of 7 (service on pid 42360 through the check, then redeployed below).
- One step: every delivery record now stores Telegram's server-side send timestamp (`telegram_date` from the Bot API response). `verify_corroboration()` re-derives each record's ET day from that server clock and flags any disagreement with the locally recorded day — the W1 evidence no longer trusts the Mac Studio's clock alone. `scripts/delivery_streak.py` reports corroboration counts; today's 16:30 ET record will be the first corroborated one.
- Suite green before and after: 248 passed, 1 deselected (+9). Battle 49/49. Floor 254 vs baseline 225. Secrets scan clean.
- Deployed bafd3de to the service ahead of today's slot: /health 200, scheduler re-armed for 16:30 ET (day 3 of 7).
- W1 waiting on: today's 16:30 ET delivery (day 3), then four more consecutive days. No input needed from Philip.

## 2026-09-11 — W1 night 5 (Friday verification)

- Friday night: verification only, no features, no code changes.
- Verified yesterday's scheduled delivery: launchd service delivered the 16:30:00 ET summary for 2026-09-10 (message 4884, heat 0.587) to thread 4799. First server-clock-corroborated record: Telegram's send timestamp re-derives to the same ET day (checked=1, corroborated=1, mismatched=0). Scheduled streak: 3 consecutive days; today 16:30 ET is day 4 of 7. Service healthy on pid 80114, /health 200, scheduler armed.
- Gates: blocking 248 passed, 1 deselected; holdout 4 passed; battle 49/49; floor 254 vs baseline 225; secrets scan clean.
- W1 waiting on: today's 16:30 ET delivery (day 4), then three more consecutive days. No input needed from Philip.
