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

## 2026-09-23 — W3 night 7 (Wednesday)

- Streak check: launchd service delivered the 16:30:00 ET summary for 2026-09-22 (message 5224, server-clock corroborated; checked=12, corroborated=12, mismatched=0). Scheduled streak: 6 of 7 consecutive (2026-09-17..09-22); today's 16:30 ET slot is day 7. Service was live on pid 10386, /health 200, through the check.
- W3 step 5: the mechanical flip verdict. w3_status() in src/delivery_log.py + a w3 block in scripts/delivery_streak.py — due-week Monday-slot math (a Monday-slot fire counts for its ISO week even past ET midnight; on Monday before 16:30 ET the prior week is still under judgment), scheduled-only weekly_scorecard records (manual sends never count), tier-aware verify_corroboration(), and a first-expected clamp at 2026-09-28 so pre-cadence weeks never read as misses. Emits the ready-to-paste evidence line for the ROADMAP flip. Tonight it reads: w3_met=false, "not yet due; first expected weekly post 2026-09-28".
- Independent review: Codex read-only pass over the verdict (runs/diagnostics/codex_w3_review_2026-09-23.md) found a real hazard — send_weekly_scorecard defaulted scheduled=True, so a future manual call that forgot the flag would forge scheduler provenance and satisfy w3_status(). Fixed (aeef90c): default now False (matching send_daily_summary), the Monday-slot scheduler call passes scheduled=True explicitly, and an anti-forgery test pins the default. Monday post behavior unchanged. W1 behavior confirmed untouched by the tier parameter.
- Tests: 304 passed, 1 deselected (was 292; +11 verdict tests, +1 provenance test). Floor 310 vs baseline 225. Secrets scan clean. Holdout 4 passed (fresh tonight). Commits a3fa4ca, aeef90c, 6b5f0cf.
- Deployed aeef90c to com.kairox.riskradar ahead of today's slot: pid 10386→34155, /health 200, scheduler re-armed for 16:30 ET (45895 s).
- W2 still WAITING on Philip: config/positions.yaml remains SAMPLE. W3 remaining: first automatic weekly post Monday 2026-09-28 16:30 ET; Tuesday night's run flips the increment quoting w3_status evidence if suites stay green.

## 2026-09-24 — W3 night 8 (Thursday)

- Streak check: launchd service delivered the 16:30:00 ET summary for 2026-09-23 (message 5270, server-clock corroborated; checked=13, corroborated=13, mismatched=0). Post-fix scheduled streak: 7 of 7 consecutive (2026-09-17..09-23). Service live on pid 34155 (aeef90c), /health 200. w3_status tonight: w3_met=false, "not yet due; first expected weekly post 2026-09-28" — expected until Monday's slot.
- W3 step 6: weekly scorecard readiness smoke (commit 8b47ca5). scripts/weekly_scorecard_smoke.py runs the exact production fetch+build path (produce_weekly_scorecard_text, default network fetch) and grades the render — None is a FAIL with skip hint, unparseable/stale (>10 days) window is a FAIL, otherwise OK with window/episodes/age diagnostics. Never imports the alert manager, so it is structurally incapable of sending; a source-guard test pins that. 8 offline unit tests (verdict grading incl. a real build_scorecard_text render, zero-episode validity, staleness boundary, no-send import guard).
- Live smoke run tonight (network, no send): OK — window 2024-09-24→2026-09-23, age 1 day, 3 episodes ≥5%, VIX 3/3 median lead 24 d, MA 3/3 median lead 43 d — identical medians to the 2026-09-22 render with the window rolled forward. Monday 2026-09-28 16:30 ET post should render; if the slot skips, grep stdout.log for "Weekly scorecard fetch" and rerun this smoke.
- Tests: 312 passed, 1 deselected (was 304). Floor 318 vs baseline 225. Holdout 4 passed (fresh tonight). Secrets scan clean. No service redeploy — change is offline-only (script + tests).
- Housekeeping: yesterday's 02:10 state-tooling commit (43ece66: STATE.md, STATE.answers.md, scripts/write_state.sh) had landed unpushed; tonight's push carries it.
- W2 still WAITING on Philip: config/positions.yaml remains SAMPLE. W3 remaining: first automatic weekly post Monday 2026-09-28 16:30 ET; Tuesday night's run flips the increment quoting w3_status evidence if suites stay green.

## 2026-09-25 — Friday verification (W3 night 9)

- Friday gates, all green: blocking 312 passed 1 deselected; holdout 4 passed; battle 49/49; floor 318 vs baseline 225; secrets scan clean. No features per contract.
- Streak check: 16:30 ET summary for 2026-09-24 delivered (message 5291, server-clock corroborated; checked=14, corroborated=14, mismatched=0). Scheduled streak: 8 consecutive (2026-09-17..09-24); w1_met=true, live. Service on pid 34155 (aeef90c), /health 200, scheduler re-armed for today's 16:30 ET slot.
- W2 UNBLOCKED: Philip's real holdings landed in 142b3e6 ("Saved by Philip from Autopilot HQ") — 6 symbols, weights sum 1.00. positions.yaml is no longer SAMPLE. Monday's run starts W2: wire holdings into the score, top-3 attributions, one action per summary, yes/no feedback path.
- W3: w3_status not yet due — first expected automatic weekly post Monday 2026-09-28 16:30 ET; Tuesday night's run flips the increment on w3_status evidence if suites stay green.
- Housekeeping: removed redundant config/positions.yaml.bak-20260924T174315Z (byte-identical to git history at 142b3e6~1). Tonight's push carries 142b3e6 (holdings, landed unpushed Thursday).


## 2026-09-28 — W2 night 1 (Monday)

- Streak check: launchd service delivered the 16:30 ET summaries through 2026-09-27 (last scheduled message 5446, server-clock corroborated; checked=17, corroborated=17, mismatched=0). Scheduled streak: 11 consecutive (2026-09-17..09-27); w1_met=true, live. Today's 16:30 ET slot is also W3's first weekly scorecard post (due-week Monday = 2026-09-28; flip judgment is Tuesday night's run).
- Critical find: the running service (pid 34155, started Wed 02:44 — BEFORE holdings landed Thu 12:43) was still scoring the 9-symbol SAMPLE book; positions load once at startup and nothing watched the file, so every summary since Thursday ran on the wrong portfolio.
- W2 step 1: positions hot-reload (a950c52). New src/positions.py — load_positions / validate_positions / positions_signature / reload_positions_if_changed. Refusals are atomic: bad weight sum (tolerance 0.05), duplicate symbols, zero/negative weights, malformed YAML, or deletion all keep the current book with a logged reason; the signature is recorded even on refusal so a malformed file is not re-parsed every cycle, and a later fix reloads normally. compute_heat_loop consults it every cycle; an applied reload also recreates ClusteringEngine (the only symbol-keyed sticky state). 19 offline tests, including parser-consistency with Settings.from_yaml and a source guard that main.py stays wired.
- Deployed a950c52 ahead of today's slot: pid 34155→60342, /health 200, /positions shows the real book (NVDA .10, TSLA .20, META .20, SPCX .20, GOOG .10, PLTR .20). First cycle on the real book: heat 0.19 (cool), dominant factor market, top pair NVDA↔TSLA 0.35, top attribution PLTR 26.1% heat share (rec=hold). Scheduler re-armed for 16:30 ET (46209 s). No thresholds or alert semantics touched.
- Tests: 331 passed, 1 deselected (was 312). Floor 337 vs baseline 225. Secrets scan clean.
- W2 next: top-3 attributions + one action inside the daily summary text (per-position attribution endpoint already live), then the yes/no feedback path.

## 2026-09-29 — W3 flipped; W2 night 2 (Tuesday): the yes/no feedback path

- Streak check: launchd service delivered the 16:30 ET summary for 2026-09-28 (message 5457, server-clock corroborated; checked=18, corroborated=18, mismatched=0). Scheduled streak: 12 consecutive (2026-09-17..09-28); w1_met=true, live. Today's 16:30 ET slot is day 13.
- **W3 MET and FLIPPED** (2aaccaa): the first automatic weekly scorecard landed Monday 2026-09-28 16:30 ET as message 5458 (w3_status: weekly_posts=1, corroborated=1, mismatched=0). ROADMAP W3 passes=true with the evidence line pasted from the streak script. All three increments that can pass on their own have now passed once; only W2 remains open.
- Architecture find before building: the Telegram token in the launchd plist is kairox_manager_bot — the SAME bot the Hermes gateway uses (verified via getMe; never printed). A RiskRadar getUpdates consumer would 409-fight the gateway and steal the Manager's updates, so inline callback buttons are impossible on this token without a dedicated bot (a Philip-only @BotFather action, deliberately not requested). The "tap" is therefore a one-word YES/NO reply in topic 4799.
- W2 step 2: the feedback path (ef49a1b). src/feedback.py — JSONL feedback log mirroring delivery-log semantics (invalid votes rejected, corrupt lines skipped, latest tap wins). POST/GET /feedback on the local API (422 on bad vote). scripts/record_feedback.py — API-first with a direct-append fallback so a tap survives a service-down window; stdlib only; never sends to Telegram. send_daily_summary now always asks "Was this useful? Reply YES/NO in this topic." and shows "Last feedback: 👍/👎 (date)" when a log exists — read at send time inside try/except so display can never break the W1 delivery. Relay rule added to the riskradar skill so the topic-4799 Hermes session records Philip's replies with the script. 15 offline tests (incl. a source guard pinning no getUpdates call/URL in src/feedback.py).
- Deployed ef49a1b to com.kairox.riskradar ahead of today's slot: pid 60342→7101, scheduler re-armed for 16:30 ET (46083 s), /positions shows the real book, GET /feedback returns {"feedback": null} (new code live; no test vote written to the production log — POST is covered by tests, the first real record must be Philip's).
- Gates: 346 passed, 1 deselected (was 331). Holdout 4 passed (fresh tonight). Floor script not run tonight (floor check is a Friday gate; suite count grew). Secrets scan clean.
- W2 remaining: Philip's first real yes/no recorded and surfaced in a live summary — then flip. Nothing is waiting on setup; he just replies in the topic after today's 16:30 ET summary.

## 2026-09-30 — W2 night 3 (Wednesday): one action per summary, guaranteed

- Streak check: launchd service delivered the 16:30 ET summary for 2026-09-29 (message 5462, server-clock corroborated; checked=19, corroborated=19, mismatched=0). Scheduled streak: 13 consecutive (2026-09-17..09-29); w1_met=true, live. Today's 16:30 ET slot is day 14. w3_met=true (weekly post 5458 standing).
- Gap find: calm days return "No actions needed. Heat X in Y regime." with an empty actions list, and the summary render dropped the whole Recommendations block — so cool-day summaries carried top-3 attributions but ZERO action lines, violating W2's "one action per summary" done_when. Last night's cycle log confirmed the realistic path (heat 0.236, no actions).
- W2 step 3 (41b5d71): empty action list now renders "HOLD: hold all positions (no changes recommended)" under the Recommendations header; a missing recommendation package renders "unavailable this cycle" + the same explicit hold. Display only — thresholds and alert semantics untouched. 2 offline tests pin both paths.
- Deployed 41b5d71 to com.kairox.riskradar ahead of today's slot: pid 7101→81510, /health 200, /positions shows the real 6-symbol book, GET /feedback {"feedback":null}, scheduler re-armed for 16:30 ET (46399 s).
- Tests: 348 passed, 1 deselected (was 346). Secrets scan clean. Pushed 0a87e42..41b5d71.
- W2 remaining: Philip's first real YES/NO reply in topic 4799 (recorded via scripts/record_feedback.py) surfaced in a live summary — then flip. Nothing waiting on setup; the ask rides every 16:30 ET summary.

## 2026-10-01 — W2 night 4 (Thursday): the mechanical flip instrument

- Streak check: 16:30 ET summary for 2026-09-30 delivered (message 5466, server-clock corroborated; checked=20, corroborated=20, mismatched=0). Scheduled streak: 14 consecutive (2026-09-17..09-30); w1_met=true, live. w3_met=true (weekly post 5458 standing). Tonight's 16:30 ET slot is day 15 and the FIRST expected to carry W2 evidence fields.
- W2 step 4 (bbfa556): scheduled daily-summary records now self-evidence the W2 done_when — positions (the scored book), top-3 (symbol, heat_share) attributions, and the exact action line displayed (summary_action_line is the single source of truth for render + record; rendered output byte-identical, pinned by the existing one-action tests plus a source guard). w2_status() in src/delivery_log.py: met requires every scheduled summary since FIRST_W2_EVIDENCE (2026-10-01) to carry the payload, the latest record's book to equal the repo config book (delivery_streak.py passes load_positions() output as expected_positions), and Philip's tap recorded AND surfaced by a later scheduled summary (same-day sends must postdate the tap — the summary reads the feedback log at send time). Manual sends never count; pre-clamp records never block; ready-to-paste evidence line like w1/w3.
- Live verdict tonight: w2_met=false — "no scheduled daily summaries since 2026-10-01 yet" + "waiting for Philip's first YES/NO tap". Both legs resolve on their own: today's 16:30 ET slot carries the payload; the tap ask already rides every summary.
- Deployed bbfa556 to com.kairox.riskradar ahead of today's slot: pid 81510→29794, /health 200, scheduler re-armed (46214 s), /positions shows the real 6-symbol book, GET /feedback {"feedback":null}.
- Gates: 359 passed, 1 deselected (was 348; +11 w2 verdict tests). Holdout 4 passed (fresh tonight). Battle 49/49. Floor 365 vs baseline 225. Secrets scan clean.
- W2 remaining: Philip's first real YES/NO reply in topic 4799 (relayed via scripts/record_feedback.py) surfaced in the next 16:30 ET summary — then flip on w2_status evidence. Nothing waiting on setup.

## 2026-10-02 — Friday verification: all gates green; W2 blocked only on the tap

- Friday gates (no features tonight): pytest 359 passed, 1 deselected; holdout 4 passed (fresh this session); battle 49/49; floor 365 vs baseline 225; secrets scan clean.
- Streak check: 16:30 ET summary for 2026-10-01 delivered (message 5469, server-clock corroborated; checked=21, corroborated=21, mismatched=0). Scheduled streak: 15 consecutive (2026-09-17..2026-10-01); w1_met=true, live. w3_met=true (weekly post 5458 standing; next weekly slot Monday 2026-10-05). Service live on pid 29794, /positions shows the real 6-symbol book.
- W2 verdict: payload leg GREEN — every scheduled summary since FIRST_W2_EVIDENCE (2026-10-01) carries positions + top-3 attributions + the displayed action line (msg 5469); latest record's book equals the repo config book. w2_met=false with the single reason "waiting for Philip's first YES/NO tap" (feedback log empty).
- Escalated the tap to HQ Inbox (card 33e08cea38d32af9c2d1b2ba0b83c1d63a7fabc3, ws-riskradar, created 2026-10-02, priority 50): one-word YES or NO reply in topic 4799 after any 16:30 ET summary. Do NOT re-ask — every other leg is done and verified.
- Done: Friday verification complete; no code changes; tree clean. Waiting on: Philip's tap, nothing else.

## 2026-10-05 — W2 night 5 (Monday): the tap can no longer be lost

- Streak check: launchd service delivered the 16:30 ET summary for 2026-10-04 (message 5942, server-clock corroborated; checked=24, corroborated=24, mismatched=0). Scheduled streak: 18 consecutive (2026-09-17..2026-10-04); w1_met=true, live. Today's 16:30 ET slot is day 19 and weekly scorecard #2 (due-week Monday). Service on pid 29794, /health 200, scheduler self-re-arms daily (last arm 2026-10-04 15:30 CT for 16:30 ET).
- W2 verdict: payload leg GREEN — all 4 scheduled summaries since FIRST_W2_EVIDENCE carry positions + top-3 attributions + action (5469, 5474, 5658, 5942); book matches repo. w2_met=false, single reason "waiting for Philip's first YES/NO tap". HQ ask standing (card 33e08cea, ws-riskradar) — not re-asked.
- Tap forensics tonight: feedback log empty AND no telegram-platform transcript session exists since 2026-09-26; gateway log last saw thread-4799 activity 2026-09-24. Philip has not replied through the gateway path — the wait is real, not a lost message.
- W2 step 5 (345cf3b): scripts/check_topic_tap.py — nightly backstop that scans local Hermes transcripts for a bare "[philip Bankier] yes|no" (telegram platform, on/after FIRST_W2_EVIDENCE), attributes the session to the RiskRadar topic by joining gateway-log 4799 activity (±1 day of transcript mtime), and with --apply records only the newest topic-verified candidate via src.feedback (source=transcript-relay:<session>). Dry-run by default; never sends to Telegram; local files only; wordy replies surface as near-misses and are never recorded. Live run tonight: quiet no-op, exit 0. 17 offline tests (regex strictness, sender/platform/since filters, log-join verify/refuse, newest-wins apply, dedupe vs newer feedback, source guards on api.telegram.org/getUpdates(/sendMessage( call forms).
- Weekly readiness (Friday's directive): scripts/weekly_scorecard_smoke.py OK through the production path — window 2024-10-03→2026-10-02, 3 episodes ≥5%, VIX 3/3 median lead 24 d, MA 3/3 median lead 43 d. Today's 16:30 ET slot should post scorecard #2; Tuesday night's run verifies weekly_posts=2.
- Tests: 376 passed, 1 deselected (was 359). Secrets scan clean. No service redeploy — offline tooling only. W2 remaining: Philip's tap, then flip on w2_status evidence.

## 2026-10-06 — W2 night 6 (Tuesday): emergency-storm post-mortem + cadence fix

- Streak check: 16:30 ET summary for 2026-10-05 delivered (message 6225, server-clock corroborated; checked=25, corroborated=25, mismatched=0) and weekly scorecard #2 landed in the same Monday slot (message 6226; w3_met=true, weekly_posts=2). Scheduled streak: 19 consecutive (2026-09-17..2026-10-05); w1_met=true, live.
- INCIDENT (HQ operator card bdee2940, ws-riskradar): card flagged ~50 identical EMERGENCY RISK ALERT messages in thread 4799 (Mon 09:02-13:13 ET window); stdout forensics show ~850 total (2026-10-03: 284, 10-04: 284, 10-05: 282 — one per ~5-min compute cycle). Two stacked causes: (a) heat jumped 0.63 (warm) -> clipped 1.00 (emergency) between 23:58 and 00:03 CT Fri->Sat with markets closed and zero new data — a calendar-midnight pipeline artifact held the raw composite above the clip for ~72 h; (b) the EMERGENCY tier had cooldown 0 by design, so every cycle re-broadcast while the level persisted. Post-restart evidence: same market data scored 0.81 (critical) on the old process (pid 29794, in-memory percentile histories carrying the weekend) and 0.20 (cool) on the fresh one — the artifact also pollutes the scorer's rolling in-memory history and deflates only as it rolls off.
- Fix (6ec0de9, deployed pid 83276 ahead of today's slot; /health 200, scheduler re-armed 45872 s, real 6-symbol book confirmed): EMERGENCY_REBROADCAST_MINUTES=360 — first broadcast immediate, persisting-level repeats capped at 4/day; de-escalation/re-entry still alert through LEVEL_CHANGE (30-min anti-flap unchanged; rapid flapping stays suppressed by design). Cadence only — trigger thresholds and semantics untouched, per the operator ask. Alert sends (emergency/level_change/regime_shift) now append delivery-log records under alert_* tiers: invisible to daily_streak/w1/w2/w3 (exact-tier filters, pinned by test) but visible to the nightly runner — the storm had been invisible to three consecutive nightly verifications.
- Next investigation (not tonight; scorer untouched): root-cause the midnight score jump — which component's window math breaks at the calendar-day boundary on closed markets, and how the weekend's degenerate values enter the in-memory histories. Evidence trail: stdout Heat Score lines 2026-10-02 23:58 -> 10-03 00:03; daily summaries 5658/5942/6225 all heat=1.0 with identical internals; 10-06 02:44 (0.81) vs 02:45 (0.20) restart flip.
- W2 verdict: payload leg GREEN — all 5 scheduled summaries since FIRST_W2_EVIDENCE carry positions + top-3 attributions + action (5469, 5474, 5658, 5942, 6225); book matches repo. w2_met=false, single reason "waiting for Philip's first YES/NO tap". Tap backstop quiet again tonight (0 candidates, 0 near-misses). Note: the 2026-10-02 tap ask no longer appears in HQ list-open — not re-asked per protocol.
- Gates: 382 passed, 1 deselected (was 376; +6 storm-cadence/alert-record/isolation tests). Holdout 4 passed (fresh tonight). Secrets scan clean. HQ card bdee2940 resolved with the diagnosis + fix receipt.
- Done: storm mechanism fixed, deployed before today's slot, and observable. Waiting on: Philip's tap (W2 flip); next session picks up the midnight-jump root cause.

## 2026-10-07 — W2 night 7 (Wednesday): midnight-jump root cause found and fixed

- Streak check: 16:30 ET summary for 2026-10-06 delivered (message 6330, server-clock corroborated; checked=26, corroborated=26, mismatched=0). Scheduled streak: 20 consecutive (2026-09-17..2026-10-06); w1_met=true, live. w3_met=true (weekly posts 5458, 6226 standing; next due-week Monday 2026-10-12).
- ROOT CAUSE (0fa18ac, closing the investigation opened in 6ec0de9): the midnight jump was never about the calendar boundary — the log shows continuous per-cycle creep on closed markets (2026-10-02 00:04 0.21 → 05:04 0.27 with zero data change). compute() appended the unchanged component values to the five in-memory percentile histories on EVERY ~5-min cycle, and percentile_rank counts history <= value, so each duplicate append of v self-inflates v's own rank at ~(1-p)/(n+1) per cycle; ~284 duplicate appends/day saturate every percentile toward 1.0 and clip the composite to EMERGENCY across a closed weekend. Explains all three post-mortem facts: the ~72 h phantom EMERGENCY, identical internals across days (5658/5942/6225 all heat=1.0), and the restart flip 0.81→0.20 (fresh histories).
- Fix: append-once-per-data-date — the five histories advance only when the returns frame's last index value changes (the date key locks after all five appends so a mid-compute exception retries next cycle). Repeated cycles on unchanged data rank against a frozen history: score frozen, no drift. percentile_rank, weights, thresholds, level semantics, and alert cadence untouched. Also restores the documented 252/504 trading-day design intent — pre-fix, histories grew ~288/day even on trading days, so the 504 window held under 2 days of cycles.
- Storm replay with the real calibration seed + a frozen Friday frame: OLD logic 0.218 → 1.000, crossing EMERGENCY (0.8781) at cycle 421 (~1d 11h after freeze); NEW logic frozen at 0.218 (cool) across all 864 cycles. 8 new offline tests (closed-weekend 3d×288 replay flat, exact score freeze, one-append-per-day walk, same-date backfill no-append, seeded-history stability, rank-saturation mechanics pin).
- Deployed 0fa18ac ahead of today's slot: pid 83276→36926, /health 200, first cycle 0.18 (cool) on the real 6-symbol book (top pair TSLA↔SPCX 0.35), scheduler re-armed 46248 s.
- Gates: 390 passed, 1 deselected (was 382; +8). Battle 49/49. Secrets scan clean. Pushed 67ddacc..0fa18ac.
- W2 verdict: payload leg GREEN — all 6 scheduled summaries since FIRST_W2_EVIDENCE carry positions + top-3 attributions + action (5469, 5474, 5658, 5942, 6225, 6330); book matches repo. w2_met=false, single reason "waiting for Philip's first YES/NO tap". Tap backstop quiet again (0 candidates, 0 near-misses). Follow-up noted (not urgent, none of these drove alerts): CorrelationEngine's internal avg-corr history and the calibration/crowding histories in main.py still append per cycle.
- Done: storm root cause fixed, tested, deployed. Waiting on: Philip's tap (W2 flip) — nothing else.
## 2026-10-08 — W2 night 8 (Thursday): a bare thumbs up/down now counts as a tap

- Streak check: 16:30 ET summary for 2026-10-07 delivered (message 6333, server-clock corroborated; checked=27, corroborated=27, mismatched=0). Scheduled streak: 21 consecutive (2026-09-17..2026-10-07); w1_met=true, live. w3_met=true (weekly posts 5458, 6226 standing; next due-week Monday 2026-10-12). Alert-tier records in the delivery log: 2 total, both single alert_level_change events (10-06, 10-07) — expected post-storm cadence, no storm signature.
- Follow-up from 2026-10-07 closed by measurement, not code: checked the secondary per-cycle appends (regime avg_corr/heat histories in main.py, CorrelationEngine, CrowdingEngine) for drift over ~24 h of frozen overnight data — Heat frozen at 0.19 all night (0fa18ac holding in production), Regime flat (unknown, conf 0.34 every cycle), Crowding flat at 0.611 while data was unchanged, stepping only when the data date rolled. Those paths consume rolling last-n stats, not percentile ranking, so the storm-class self-inflation mechanism is structurally absent; per the standing rule they stay untouched unless drift ever appears.
- W2 step (7687825): the tap surface was word-only — a bare thumbs emoji, arguably the most likely lazy tap on Telegram, was rejected everywhere (normalize_vote, backstop regex, CLI argparse) and would have stayed a permanent near-miss. Now: normalize_vote accepts a bare thumbs up/down (variation selector / skin tone tolerated) and canonicalizes to yes/no so the log format and all downstream readers never change; the transcript backstop matches bare [sender] emoji taps with word-level strictness (wordy emoji replies stay near-misses; --apply records the canonical word); record_feedback CLI accepts the emoji forms and canonicalizes before the POST; the daily-summary ask line now reads "Reply YES / NO (or 👍 / 👎) in this topic." 14 new offline tests (regex strictness incl. doubled-emoji and wrong-emoji rejection, canonicalization at every entry point, API 200/422, summary render).
- Deployed 7687825 to com.kairox.riskradar ahead of today slot: pid 36926→94869, /health 200, scheduler re-armed (46041 s), /positions shows the real 6-symbol book, first cycle 0.19 (cool), top contributor PLTR (25.7% heat share). Backstop re-run on the new code: quiet (0 candidates, 0 near-misses).
- Gates: 404 passed, 1 deselected (was 390; +14). Holdout 4 passed (fresh tonight). Secrets scan clean. Floor/battle are Friday gates, not run tonight.
- W2 verdict: payload leg GREEN — all 7 scheduled summaries since FIRST_W2_EVIDENCE carry positions + top-3 attributions + action (5469, 5474, 5658, 5942, 6225, 6330, 6333); book matches repo. w2_met=false, single reason "waiting for Philip's first YES/NO tap" — which a bare 👍/👎 in topic 4799 now also satisfies. Waiting on: Philip's tap, nothing else.
