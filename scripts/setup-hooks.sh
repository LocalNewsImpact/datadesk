#!/usr/bin/env bash
# Install the pre-push hook.
#
# Run once per checkout:  ./scripts/setup-hooks.sh
#
# The hook runs `make check`, which is `lint` + `test`, which is exactly
# what .github/workflows/ci.yml runs -- ruff, black, isort, mypy, then
# `makemigrations --check --dry-run` and pytest, in that order. One
# definition, so the hook cannot drift from CI: if the hook passes, the
# push passes.
#
# This exists because a lint failure reached GitHub. Black alone was run
# locally, ruff was not, and a bare expression statement went out as a
# red pull request.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_DIR="$(git rev-parse --git-path hooks)"
case "$HOOK_DIR" in /*) ;; *) HOOK_DIR="$REPO_ROOT/$HOOK_DIR" ;; esac
mkdir -p "$HOOK_DIR"
HOOK="$HOOK_DIR/pre-push"

cat > "$HOOK" <<'HOOK_BODY'
#!/usr/bin/env bash
# Everything CI runs, before the push rather than after it.
# Regenerate with ./scripts/setup-hooks.sh
set -uo pipefail

# `git rev-parse` rather than a path relative to the hook: a worktree's
# hooks live in the parent's .git directory, so deriving the root from
# $0 runs the checks against the wrong tree.
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT" || exit 1

mkdir -p logs/pre-push
LOG="logs/pre-push/pre-push-$(date +%Y%m%d_%H%M%S).log"

echo "🔍 Running everything CI runs (make check)..."
echo "   ruff, black, isort, mypy, makemigrations --check, pytest"
echo

if make check 2>&1 | tee "$LOG"; then
  echo
  echo "✅ All checks passed. Pushing..."
  exit 0
fi

echo
echo "❌ Checks failed — push aborted."
echo "   This is what CI would have told you, minutes sooner."
echo "   Fix, or run 'make fmt' for the formatting ones."
echo "📝 Full log: $LOG"
exit 1
HOOK_BODY

chmod +x "$HOOK"
echo "✅ Installed pre-push hook at $HOOK"
echo "   It runs 'make check' — the same commands as .github/workflows/ci.yml."
