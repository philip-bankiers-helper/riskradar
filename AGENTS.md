# RiskRadar Agent Contract

## Install and run
- Use Python 3.12: `python3.12 -m venv .venv`.
- Install exact dependencies: `.venv/bin/pip install -r requirements.lock`.
- Run locally: `.venv/bin/python -m src.main`.
- The service must bind to `127.0.0.1` unless Philip explicitly asks otherwise.

## Required checks
- Blocking suite: `.venv/bin/pytest -q`.
- Battle suite: `.venv/bin/python scripts/battle_test.py`.
- Holdout evaluator: `scripts/run_holdout.sh` (owner/reviewer only).
- Test floor: `scripts/tests_must_not_shrink.sh`.
- Secret scan: `scripts/check_no_secrets.sh`.

## Forbidden actions
- Never commit secrets or credential values; use environment variables only.
- Loop agents must not read or edit anything under `tests/holdout/`.
- No network access in the blocking test suite; mark network tests `network`.
- Never send test alerts to real recipients.
- Do not change alert thresholds or semantics without an explicit ask.
- Do not add dependencies outside MIT, BSD, or Apache licenses.
- Do not delete files under `tests/`, `fixtures/`, or `scripts/`.

## Product loop
- Read `ROADMAP.json`, then take only the next failing increment.
- Append dated evidence to `PROGRESS.md`; never rewrite prior entries.
- Keep `config/positions.yaml` marked SAMPLE until Philip supplies holdings.
