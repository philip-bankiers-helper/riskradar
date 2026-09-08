#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
pattern="(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|[0-9]{8,10}:[A-Za-z0-9_-]{30,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|(api[_-]?key|token|password|secret)[[:space:]]*[:=][[:space:]]*[\"'][A-Za-z0-9_./+=:-]{24,}[\"'])"

matches=$(git grep -IlE "$pattern" -- ':!scripts/check_no_secrets.sh' || true)
if [ -n "$matches" ]; then
  echo "possible secret material found in tracked files:" >&2
  echo "$matches" >&2
  exit 1
fi

echo "no key-looking secret values found in tracked files"
