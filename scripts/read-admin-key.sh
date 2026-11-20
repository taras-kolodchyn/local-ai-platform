#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"
[[ ! -t 1 ]] || die 'The admin key can only be read over a pipe.'
load_env
read_admin_key
