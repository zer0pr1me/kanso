#!/bin/sh
# Point git at the versioned hooks in .githooks/.
set -e
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
echo "Git hooks installed from .githooks/"
