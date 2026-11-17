#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

require_command python3
# Status observes the platform even when .env, generated files or Docker are
# absent. Bootstrap, secret creation and model loading do not belong here.
exec python3 scripts/model-status.py --format "${FORMAT:-text}"
