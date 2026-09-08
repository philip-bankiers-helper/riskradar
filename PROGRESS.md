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
