#!/bin/sh
# ---------------------------------------------------------------------------
# operator-unlock-shim-v2
#
# /svp/bin/sysctl -- one-shot bootstrap wrapper for the Mazda CMU (KBST-41-47-M5).
#
# Why this works:
#   /svp/etc/svp.env.runtime.sh puts /svp/bin first in PATH and
#   /svp/etc/svp.env.extra.sh calls "sysctl" by name.  A file named
#   /svp/bin/sysctl is therefore executed as root before the update check
#   and before svp.startup.sh.
#
# Safety:
#   * if anything is wrong, the real sysctl is still executed;
#   * boot.sh is bounded by "timeout" (default 3 s);
#   * after a successful boot.sh run the script atomically replaces itself
#     with a symlink to the real sysctl.  It does NOT delete itself: busybox
#     ash may keep the resolved path in its command hash, and removing it
#     would make the later sysctl calls in the same shell fail.
#
# Test overrides (leave unset on the device):
#   UNLOCK_SVP, UNLOCK_SHIM_SELF, UNLOCK_REAL_SYSCTL, UNLOCK_TRIED,
#   UNLOCK_BOOT_TIMEOUT
# ---------------------------------------------------------------------------

SVP_DIR=${UNLOCK_SVP:-/svp}
UNLOCK_DIR=$SVP_DIR/unlock
SELF=${UNLOCK_SHIM_SELF:-$SVP_DIR/bin/sysctl}

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

# --- real sysctl ------------------------------------------------------------
REAL=${UNLOCK_REAL_SYSCTL:-}
BUSYBOX=
if [ -z "$REAL" ]; then
    for c in /sbin/sysctl /usr/sbin/sysctl /bin/sysctl; do
        if [ -x "$c" ]; then
            REAL=$c
            break
        fi
    done
fi
if [ -n "$REAL" ] && [ ! -x "$REAL" ]; then
    REAL=
fi
if [ -z "$REAL" ] && [ -x /bin/busybox.nosuid ]; then
    BUSYBOX=/bin/busybox.nosuid
    REAL=$BUSYBOX
fi

# --- logging (best effort) --------------------------------------------------
LOG=$UNLOCK_DIR/last.log
if ( : >>"$LOG" ) 2>/dev/null; then
    chmod 0644 "$LOG" 2>/dev/null || true
else
    LOG=/tmp/operator-unlock.log
    : >>"$LOG" 2>/dev/null || true
    chmod 0644 "$LOG" 2>/dev/null || true
fi
log() {
    printf '%s shim: %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" "$*" \
        >>"$LOG" 2>/dev/null || true
    chmod 0644 "$LOG" 2>/dev/null || true
}

# --- one attempt per boot ---------------------------------------------------
TRIED=${UNLOCK_TRIED:-}
if [ -z "$TRIED" ]; then
    if [ -d /run ] && [ -w /run ]; then
        TRIED=/run/operator-unlock.tried
    else
        TRIED=/tmp/operator-unlock.tried
    fi
fi

TIMEOUT_CMD=
for c in /usr/bin/timeout /bin/timeout; do
    if [ -x "$c" ]; then
        TIMEOUT_CMD=$c
        break
    fi
done
BOOT_TIMEOUT=${UNLOCK_BOOT_TIMEOUT:-3}

# --- run payload ------------------------------------------------------------
if [ "$(id -u 2>/dev/null)" = "0" ] \
    && [ -r "$UNLOCK_DIR/boot.sh" ] \
    && [ ! -e "$TRIED" ]; then
    : >"$TRIED" 2>/dev/null || true
    log "invoked as root; running boot.sh"
    if [ -n "$TIMEOUT_CMD" ]; then
        "$TIMEOUT_CMD" "$BOOT_TIMEOUT" /bin/sh "$UNLOCK_DIR/boot.sh" \
            </dev/null >/dev/null 2>&1
    else
        /bin/sh "$UNLOCK_DIR/boot.sh" </dev/null >/dev/null 2>&1
    fi
    rc=$?
    if [ "$rc" -eq 0 ] && [ -f "$UNLOCK_DIR/installed" ]; then
        log "boot.sh OK; replacing shim with symlink to $REAL"
        # Only replace the file if it is still our script.
        if [ -n "$REAL" ] && [ -f "$SELF" ] \
            && grep -q 'operator-unlock-shim-v2' "$SELF" 2>/dev/null; then
            if /bin/ln -sfn "$REAL" "$SELF.new" 2>/dev/null \
                && /bin/mv -f "$SELF.new" "$SELF" 2>/dev/null; then
                log "shim replaced"
            else
                log "shim replacement failed (read-only /svp?); will retry next boot"
            fi
        fi
    elif [ "$rc" -eq 75 ]; then
        # Root home was not mounted yet.  Allow another attempt in this boot
        # instead of waiting for a reboot (env scripts call sysctl repeatedly).
        rm -f "$TRIED" 2>/dev/null || true
        log "boot.sh deferred (root home not mounted yet); will retry on the next call"
    else
        log "boot.sh did not complete (rc=$rc, marker=$UNLOCK_DIR/installed); will retry next boot"
    fi
fi

# --- pass through to the real sysctl ---------------------------------------
if [ -n "$BUSYBOX" ]; then
    exec "$BUSYBOX" sysctl "$@"
fi
if [ -x "$REAL" ]; then
    exec "$REAL" "$@"
fi
exit 127
