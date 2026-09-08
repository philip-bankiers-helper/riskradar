#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
baseline=$(tr -d '[:space:]' < tests/.baseline_count)
actual=$(
  find tests -name '*.py' -exec grep -hE '^[[:space:]]*(async[[:space:]]+)?def test_' {} + \
    | wc -l \
    | tr -d '[:space:]'
)

if [ "$actual" -lt "$baseline" ]; then
  echo "test count shrank: baseline=$baseline actual=$actual" >&2
  exit 1
fi

echo "test count ok: baseline=$baseline actual=$actual"
