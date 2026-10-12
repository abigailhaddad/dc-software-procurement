#!/usr/bin/env bash
# Weekly refresh: pull the portal, classify what is new, and report drift.
#
# This deliberately does NOT republish the briefing. It refreshes the data and
# says which published figures have moved; deciding to restate them is a human
# call. See docs/SCHEDULING.md.
#
# Run by hand, or on a schedule -- see scripts/com.dcbudget.weekly.plist.
# Exits non-zero only when a step genuinely failed. Drift alone is not failure.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

# launchd hands a job a near-empty PATH, so uv and gh have to be findable.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

STAMP="$(date +%Y%m%dT%H%M%S)"
LOG_DIR="$REPO/data/drift/logs"
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
step "pull portal snapshot" make pull      || failed=1
# Straight after the pull, before anything downstream can fail: the archive is
# the one copy of contracts the portal has stopped listing.
step "publish raw archive" make publish    || failed=1
step "classify new contracts" make classify || failed=1

# The derived tables are computed from the snapshot, so they have to be rebuilt
# before drift is measured. Otherwise the report mixes "the portal changed" with
# "we never re-ran the downstream steps", and the two are not distinguishable in
# the output. Left out on purpose: calc (re-pulling replaces the GSA provenance
# snapshot every federal figure was computed from), h1b (other agencies' APIs),
# and ocr (needs a local OCR toolchain). Those stay manual.
step "rebuild rate cards"   make rates         || failed=1
step "rebuild solicitations" make solicitations || failed=1
step "rebuild DCAS"          make dcas          || failed=1
# Everything else verify_claims checks. Without this the run refreshes the
# snapshot and leaves these tables frozen, so their checks pass against stale
# data and the drift report goes quiet exactly where the newest findings are.
step "rebuild derived tables" make derived     || failed=1

# Drift reporting runs even if classification failed: a stale-label drift
# report is still worth having, and it records what it could not compute.
step "drift report" make drift || failed=1

# The dashboard reports the current snapshot, so it follows the refresh. The
# briefing is deliberately NOT rebuilt: it stays on the published baseline
# until a human restates it, and the drift panel explains the gap.
step "rebuild dashboard" make dashboard || failed=1

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
log "finished clean; report in data/drift/"
