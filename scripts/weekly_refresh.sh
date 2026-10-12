#!/usr/bin/env bash
# Weekly refresh: pull the portal, rebuild the site's tables, check them, publish.
#
# The site states no figures of its own: everything on it is a query over the
# published tables, so refreshing the tables is the whole job. See
# docs/SCHEDULING.md.
#
# Run by hand, or on a schedule -- see scripts/com.dcbudget.weekly.plist.
# Exits non-zero when a step failed.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

# launchd hands a job a near-empty PATH, so uv and gh have to be findable.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

STAMP="$(date +%Y%m%dT%H%M%S)"
LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run-$STAMP.log"

log() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }

step() {
  local name="$1"; shift
  log "-- $name"
  # Capture the status directly. Reading $? after an `if` reads the status of
  # the `if` itself, which is 0 when the test command failed and no else branch
  # ran -- so a failing step reported success and the run exited clean.
  "$@" >>"$LOG" 2>&1
  local code=$?
  if [ "$code" -eq 0 ]; then
    log "   ok"
    return 0
  fi
  log "   FAILED (exit $code)"
  return "$code"
}

log "weekly refresh starting in $REPO"
failed=0

# The pull merges into the previous files, which prune removed last week.
# If this fails the pull would start from nothing and publish would replace the
# archive with one week's rows, so the run stops here instead.
if ! step "restore raw archive" make hydrate; then
  log "stopping: could not restore data/raw from the archive; see $LOG"
  exit 1
fi
step "pull portal snapshot" make pull || failed=1

# The site reads the Parquet tables, so they are rebuilt from the pull and
# checked against it before anything is published. A failed check stops the
# publish: the live site would otherwise serve the broken tables. data/raw is
# left on disk, so nothing is lost and next week's run publishes it.
if [ "$failed" -eq 0 ] && step "export and check the tables" make verify; then
  step "publish archive and tables" make publish || failed=1
else
  failed=1
  log "not publishing: the pull or the checks failed"
fi

# Free the disk only on a clean run, and prune itself deletes nothing unless
# every file matches what was published.
if [ "$failed" -eq 0 ]; then
  step "delete local raw copy" make prune || failed=1
fi
# Keep the last 12 runs. Weekly, so about a quarter of history.
ls -1t "$LOG_DIR"/run-*.log 2>/dev/null | tail -n +13 | xargs -r rm --

if [ "$failed" -ne 0 ]; then
  log "finished WITH FAILURES; see $LOG"
  exit 1
fi
log "finished clean"
