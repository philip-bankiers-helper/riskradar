RiskRadar is a portfolio risk-heat service that posts a 16:30 ET daily summary (plus a Monday weekly scorecard) to the RiskRadar Telegram topic, scored off the sample book in config/positions.yaml.

The day-58 hole is closed: a bounded boot-time catch-up (commit 2400f2a) now fires a slot missed to host downtime once, idempotently, and it is deployed live (pid 81706); day 61 and scorecard #8 verified as single posts, the live streak is rebuilding at 3 days since the outage reset, and every gate is green (439 tests, holdout 4, secrets).

Reply YES or NO in the RiskRadar topic after any daily summary to flip W2, the last open increment — that reply is the only missing piece; the real holdings in config/positions.yaml remain a future upgrade over the SAMPLE book.
