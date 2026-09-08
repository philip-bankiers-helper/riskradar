#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
.venv/bin/pytest -o addopts='' -q tests/holdout
