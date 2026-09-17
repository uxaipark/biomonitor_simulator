#!/usr/bin/env bash
# Remove multiprocessing shared-memory segments (/dev/shm/psm_*) that no process maps any more.
#
# The engine unlinks its segments in Engine.shutdown(); when a stop is abnormal (crash, SIGKILL
# from a deploy mid-rebuild) the main process exits without doing so, and with KillMode=mixed
# systemd SIGKILLs the resource_tracker child at once -- before it can clean up.  Each such stop
# leaves one SharedState set (6 segments, ~1 MB) in tmpfs for good.  Run from ExecStartPre.
set -u
for f in /dev/shm/psm_*; do
  [ -e "$f" ] || continue
  n=$(basename "$f")
  # skip anything a live process still maps (ours or anyone else's)
  if grep -lqs "$n" /proc/[0-9]*/maps 2>/dev/null; then continue; fi
  rm -f "$f" && echo "shm-sweep: removed orphan $n"
done
exit 0
