RiskRadar is a portfolio risk-heat service that posts a 16:30 ET daily summary (plus a Monday weekly scorecard) to the RiskRadar Telegram topic, scored off the sample book in config/positions.yaml.

The Mac was down across Nov 13 and one daily summary (day 58) was missed; the service self-recovered via launchd and days 59-60 delivered, so the live streak restarts at 2 while every gate is green (427 tests, holdout 4, battle 49/49, floor, secrets) and tonight's due-week Monday slot carries scorecard #8.

Reply YES or NO in the RiskRadar topic after any daily summary to flip W2, the last open increment; separately, decide whether to add a bounded boot-time catch-up for slots missed during host downtime (it would have saved day 58).
