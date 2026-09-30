# STATE.answers

**What is it?** RiskRadar is a personal portfolio risk monitor: it scores portfolio heat daily from real holdings (NVDA, TSLA, META, SPCX, GOOG, PLTR), posts a 16:30 ET summary with top-3 attributions and one explicit action to Telegram topic 4799, and posts a weekly lead-time scorecard on Mondays proving how many days of warning VIX/MA signals gave before every ≥5% drawdown.

**What is currently happening?** W1 and W3 are met and standing (13-day delivery streak; first automatic weekly scorecard landed as message 5458). W2 is open: holdings drive the score, every summary now carries top-3 attributions plus a guaranteed action line (explicit HOLD on calm days, shipped tonight in 41b5d71 and deployed ahead of the 16:30 ET slot) — the only missing piece is Philip's first real YES/NO reply in the topic.

**What can you do next?** Philip: reply YES or NO in topic 4799 after any 16:30 ET summary (the topic-4799 Hermes session records it via scripts/record_feedback.py); once that tap surfaces in a live summary, W2 flips. Otherwise: keep verifying nightly delivery and hold for the flip.
