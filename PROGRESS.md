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

## 2026-09-14 — W1 night 6 (Monday)

- Verified three days of scheduled deliveries since Friday's run (no runs Sat/Sun): 2026-09-11 (message 4911, heat 0.540), 2026-09-12 (message 4916, heat 0.745), 2026-09-13 (message 4918, heat 0.745), each 16:30:00 ET, each corroborated by Telegram's server clock (checked=4, corroborated=4, mismatched=0 across the log). Scheduled streak: 6 consecutive days (2026-09-08..09-13); today's 16:30 ET slot is day 7 of 7.
- One step: `w1_status()` in `src/delivery_log.py` renders the W1 done_when as a mechanical verdict — met iff streak>=7 AND live AND zero local-vs-Telegram day mismatches — and emits a ready-to-paste evidence line (day range, per-day message ids, corroboration counts). `scripts/delivery_streak.py` now carries a `w1` block; tonight it reads `w1_met=false, days_remaining=1`. Tomorrow's flip to `"passes": true` quotes its evidence line instead of hand-copied numbers.
- Suite green before and after: 254 passed, 1 deselected (+6). Floor 260 vs baseline 225. Secrets scan clean.
- Deployed 8dff6e0 ahead of today's slot: pid 80114→90663, /health 200, scheduler re-armed for 16:30 ET (day 7 of 7).
- W1 waiting on: today's 16:30 ET delivery (day 7), then the ROADMAP flip tomorrow night. No input needed from Philip.

## 2026-09-15 — W1 CLOSED (night 7, Tuesday)

- W1 met and flipped: 7 consecutive scheduled 16:30 ET summaries 2026-09-08..09-14 (messages 4823, 4837, 4884, 4911, 4916, 4918, 4965); server-clock corroboration checked=5 corroborated=5 mismatched=0; service live (pid 90663, /health 200). ROADMAP.json W1 "passes": true quotes the w1_status mechanical evidence line — commit 2bea785.
- W2 WAITING on Philip: config/positions.yaml still SAMPLE; no real holdings supplied.
- W3 prep (one small step): src/engine/scorecard.py extract_drawdown_episodes() — peak->trough->recovery episodes >=5% over the trailing 24 months, ragged paths counted once at worst depth, unrecovered losses kept with recovery_date=None. 8 offline unit tests in tests/test_scorecard.py. This is the episode list the warning-day scorecard (VIX>25, 50-day MA lead time) will count against.
- Tests: 262 passed, 1 deselected (was 254). Floor 268 vs baseline 225. Secrets scan clean. Commit b387a0f.
- Done: W1 closed after 7 nights. Waiting on: Philip's real holdings for W2; W3 scorecard assembly continues meanwhile.

## 2026-09-16 — W3 night 2 (Wednesday)

- Streak check: launchd service delivered the 16:30:00 ET summary for 2026-09-15 (message 4990, corroborated by Telegram's server clock; checked=6, corroborated=6, mismatched=0). Scheduled streak: 8 consecutive days (2026-09-08..09-15). Service live on pid 90663, /health 200.
- W2 still WAITING on Philip: config/positions.yaml remains SAMPLE.
- W3 step 2: src/engine/scorecard.py measure_warning_leads() — for every extracted episode, records when VIX>25 and close<50-day-MA first fired inside [peak, trough], how many days each stayed active, and each signal's lead days to the trough. Attribution is per-episode (pre-peak fires don't leak in), MA warmup and missing VIX are honest misses, coarse VIX stamps forward-fill onto the close index, trough-day fires score lead 0, unrecovered episodes measured the same as recovered ones. 8 offline unit tests.
- Tests: 270 passed, 1 deselected (was 262). Floor 276 vs baseline 225. Secrets scan clean. Commit 0c37e9c.
- Done: warning-lead measurement layer. Next: assemble the weekly scorecard report (episodes x warning leads -> hit/miss/lead-time table, posted weekly). Waiting on: Philip's real holdings for W2.

## 2026-09-17 — incident night: FD leak killed the 09-16 summary (Thursday)

- Incident: the 2026-09-16 16:30 ET scheduled summary FAILED to send — alert manager hit `[Errno 24] Too many open files` (stdout.log 15:29:59 CT; two attempts 18 ms apart, then re-armed). No message, no delivery-log record for the day. Post-W1 miss = fresh incident, not a ROADMAP rollback.
- Root cause: the 5-min heat loop refetches 18 symbols (9 SAMPLE positions + 9 factor ETFs) via `yf.download(threads=True)`; yfinance 1.7.0's threaded downloader leaks FDs per call — thread-local session caches plus never-reaped sockets. Live evidence on pid 90663 after 61 h uptime (deployed 09-14, longest stretch ever): 65 sockets stuck in CLOSE_WAIT + 41 tkr-tz sqlite handles; the daily-summary send burst tipped the FD table over. Nightly W1-era restarts had masked the leak. Reproduced with the exact 18-symbol set: threads=True grows FDs monotonically (26->48->64->52 over 4 calls), threads=False plateaus at a stable 56-60.
- Fix (commit ed296a5): `market_data.py` get_returns/get_prices now pass `threads=False`; 3 offline contract tests pin the kwargs. backtest.py/benchmark.py left untouched (offline paths, not in the 5-min loop).
- Gates: 273 passed, 1 deselected (was 270). Holdout 4 passed. Floor 279 vs baseline 225. Secrets scan clean.
- Deployed ed296a5 to com.kairox.riskradar ahead of today's slot: pid 90663->39592, /health 200, scheduler re-armed "at 16:30 ET" (46097 s), fresh process at 48 FDs.
- Scheduled streak: BROKEN at 8 (2026-09-08..09-15). Today's 16:30 ET delivery — first on fixed code — restarts the count at day 1; tomorrow's run verifies it landed.
- W2 still WAITING on Philip: config/positions.yaml remains SAMPLE. W3 scorecard assembly not advanced tonight — the incident fix took the step slot.

## 2026-09-18 — Friday verification (post-fix streak day 1 verified)

- Friday: verification only, no features, no code changes.
- Verified yesterday's scheduled delivery: the 2026-09-17 16:30 ET summary — first on fixed code — landed as message 5014 in thread 4799. Server-clock corroboration across the log: checked=7, corroborated=7, mismatched=0. Scheduled streak: 1 of 7 consecutive (restarted 2026-09-17 after the FD incident broke it at 8). Service live: pid 39592, /health 200, uptime ~24 h.
- FD-fix validation (ed296a5, first full day in production): 42 open FDs at ~24 h vs 48 at deploy — flat, no monotonic growth; 13 bounded CLOSE_WAIT sockets (failure signature was 65 CLOSE_WAIT + 41 sqlite handles climbing). The 09-17 daily send burst passed with no Errno 24. At this rate the Sat/Sun 16:30 ET sends (no nightly run until Monday) are safe.
- Gates: blocking 273 passed, 1 deselected; holdout 4 passed; battle 49/49; floor 279 vs baseline 225; secrets scan clean.
- W2 still WAITING on Philip: config/positions.yaml remains SAMPLE. W3 next step (Monday): assemble the weekly scorecard report — episodes × warning leads → hit/miss/lead-time table.

## 2026-09-21 — W3 night 5 (Monday)

- Weekend verified: launchd service delivered both scheduled 16:30 ET summaries — 2026-09-19 (message 5131) and 2026-09-20 (message 5151) — with no nightly run in between. Server-clock corroboration across the log: checked=10, corroborated=10, mismatched=0. Scheduled streak: 4 of 7 consecutive (2026-09-17..09-20); today 16:30 ET is day 5.
- FD-fix validation at ~96 h uptime (pid 39592, deployed 09-17, survived two weekend send bursts + full heat-loop cadence): 46 open FDs vs 48 at deploy — flat; 4 CLOSE_WAIT sockets and 16 py-yfinance cache handles, both bounded. The 09-17 pre-fix signature (65 CLOSE_WAIT + 41 tkr-tz handles climbing) is absent. threads=False fix (ed296a5) confirmed stable in production.
- W3 step 3: src/engine/scorecard.py build_weekly_scorecard() + render_scorecard_text() — the weekly report body. Per-episode hit/miss/lead-time lines (peak→trough, depth, recovery status, VIX/MA marks) plus per-signal aggregates: hit rate, median lead over fired episodes, and an either-signal row whose lead is the earliest fire. Unrecovered losses, MA-warmup misses, and missing-VIX misses all count in totals; an empty window is a valid empty report. 7 offline unit tests.
- Tests: 280 passed, 1 deselected (was 273). Floor 286 vs baseline 225. Secrets scan clean. Commits d43dc73 (code).
- W2 still WAITING on Philip: config/positions.yaml remains SAMPLE. W3 next step: wire the scorecard into the weekly cadence (regenerate + post to thread 4799 on a weekly schedule), then a real-data smoke of the rendered output. No service redeploy tonight — change is offline-only.


## 2026-09-22 — W3 night 6 (Tuesday)

- Streak check: launchd service delivered the 16:30:00 ET summary for 2026-09-21 (message 5181, server-clock corroborated; checked=11, corroborated=11, mismatched=0). Scheduled streak: 5 of 7 consecutive (2026-09-17..09-21); today's 16:30 ET slot is day 6.
- FD-fix validation CLOSED at ~120 h uptime (pid 39592, longest stretch): 33 open FDs vs 48 at deploy (46 at 96 h) — flat/declining, 4 CLOSE_WAIT bounded, no Errno 24 across five daily send bursts. threads=False fix (ed296a5) is stable; no further nightly FD readings needed barring a new incident.
- W3 step 4: weekly scorecard cadence wiring (commit 4688bdc). is_weekly_scorecard_slot() gates the Monday 16:30 ET slot (late fires still count for their week); produce_weekly_scorecard_text() fetches SPY+^VIX through the FD-safe MarketDataClient path off the event loop and returns None instead of raising; AlertManager.send_weekly_scorecard() posts under its own delivery-log tier (no cooldown) — proven invisible to the W1 daily-streak math by test; scheduler sends the scorecard strictly AFTER the daily summary so a failure can never delay or suppress the daily delivery. 12 offline unit tests.
- Real-data render smoke (no send): 24-month window 2024-09-23→2026-09-21, 3 SPY episodes ≥5% (2025-02-19→04-08 -18.8%, 2025-10-29→11-20 -5.1%, 2026-01-27→03-30 -8.9%); VIX 3/3 hits median lead 24 d, MA 3/3 hits median lead 43 d. First automatic weekly post: Monday 2026-09-28 16:30 ET.
- Gates: 292 passed, 1 deselected (was 280). Floor 298 vs baseline 225. Secrets scan clean.
- Deployed 4688bdc to com.kairox.riskradar ahead of today's slot: pid 39592→10386, /health 200, scheduler re-armed for 16:30 ET (day 6 of 7), fresh process at 21 FDs.
- W2 still WAITING on Philip: config/positions.yaml remains SAMPLE. W3 remaining: verify the first automatic weekly post lands Monday 2026-09-28, then flip the increment.
