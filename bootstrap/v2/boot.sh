#!/bin/sh
# ---------------------------------------------------------------------------
# operator-unlock-boot-v2
#
# Executed as root from /svp/bin/sysctl (variant B).  Installs the operator's
# public key into root's authorized_keys.  Idempotent and safe to re-run.
#
# It writes to root's home as defined in /etc/passwd.  Depending on the unit,
# that home is either a bind mount of userdata (/mnt/userdata/home/root) or a
# filesystem mounted directly at /home (e.g., /dev/mmcblk0p6).  Both layouts
# are resolved at runtime; the key is only written to a mounted filesystem.
#
# Test overrides (leave unset on the device):
#   UNLOCK_SVP, UNLOCK_HOME_DIR, UNLOCK_USERDATA, UNLOCK_SKIP_MOUNT_CHECK,
#   UNLOCK_WAIT_SECONDS, UNLOCK_LOCK_DIR
#
# Exit codes: 0 = key installed, 75 = root home not mounted yet (safe retry).
# ---------------------------------------------------------------------------

SVP_DIR=${UNLOCK_SVP:-/svp}
UNLOCK_DIR=$SVP_DIR/unlock
KEYFILE=$UNLOCK_DIR/authorized_key.pub
STATE=$UNLOCK_DIR/installed

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

LOG=$UNLOCK_DIR/last.log
if ( : >>"$LOG" ) 2>/dev/null; then
    chmod 0644 "$LOG" 2>/dev/null || true
else
    LOG=/tmp/operator-unlock.log
    : >>"$LOG" 2>/dev/null || true
    chmod 0644 "$LOG" 2>/dev/null || true
fi
log() {
    printf '%s boot: %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" "$*" \
        >>"$LOG" 2>/dev/null || true
    chmod 0644 "$LOG" 2>/dev/null || true
}

# --- preconditions ----------------------------------------------------------
if [ "$(id -u 2>/dev/null)" != "0" ]; then
    log "not root (uid=$(id -u 2>/dev/null)); refusing"
    exit 1
fi

# --- resolve root's home ----------------------------------------------------
# Longest mount point from /proc/mounts that contains $1 ("/" if none).
mount_point() {
    awk -v p="$1" '
        {
            m = $2
            gsub(/\\040/, " ", m)
            if (m == "/" || p == m || index(p, m "/") == 1) {
                if (length(m) > length(best)) best = m
            }
        }
        END { if (best == "") best = "/"; print best }
    ' /proc/mounts 2>/dev/null
}

PASSWD_HOME=$(awk -F: '$1 == "root" { print $6; exit }' /etc/passwd 2>/dev/null)
[ -n "$PASSWD_HOME" ] || PASSWD_HOME=/home/root

if [ -n "${UNLOCK_HOME_DIR:-}" ]; then
    HOME_DIR=$UNLOCK_HOME_DIR
elif [ -n "${UNLOCK_USERDATA:-}" ]; then
    HOME_DIR=$UNLOCK_USERDATA/home/root
else
    HOME_DIR=$PASSWD_HOME
fi

if [ "${UNLOCK_SKIP_MOUNT_CHECK:-0}" != "1" ]; then
    waited=0
    while :; do
        MP=$(mount_point "$HOME_DIR")
        if [ "$MP" != "/" ]; then
            break
        fi
        # /home not mounted yet: write to the underlying userdata source, if
        # this unit has one.  Otherwise wait briefly and defer to a later call.
        case "$HOME_DIR" in
            /home/*)
                ALT=/mnt/userdata$HOME_DIR
                if [ -d "$ALT" ] && [ "$(mount_point "$ALT")" != "/" ]; then
                    HOME_DIR=$ALT
                    break
                fi
                ;;
            *) break ;;
        esac
        if [ "$waited" -ge "${UNLOCK_WAIT_SECONDS:-1}" ]; then
            log "root home $HOME_DIR is not on a mounted filesystem; retry later"
            exit 75
        fi
        waited=$((waited + 1))
        sleep 1 2>/dev/null || true
    done
fi

SSH_DIR=$HOME_DIR/.ssh
DEST=$SSH_DIR/authorized_keys
log "root home $HOME_DIR (mount $(mount_point "$HOME_DIR"))"

if [ -L "$HOME_DIR" ]; then
    log "refusing: $HOME_DIR is a symlink"
    exit 1
fi
if [ -L "$SSH_DIR" ]; then
    log "refusing: $SSH_DIR is a symlink"
    exit 1
fi

# --- serialize concurrent calls --------------------------------------------
LOCKDIR=${UNLOCK_LOCK_DIR:-}
if [ -z "$LOCKDIR" ]; then
    if [ -d /run ] && [ -w /run ]; then
        LOCKDIR=/run/operator-unlock.lock
    else
        LOCKDIR=/tmp/operator-unlock.lock
    fi
fi
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    log "another instance is running (lock $LOCKDIR)"
    exit 2
fi

TMP=
cleanup() {
    if [ -n "${TMP:-}" ]; then
        rm -f "$TMP" 2>/dev/null || true
    fi
    rmdir "$LOCKDIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM HUP

# --- read and normalize the public key -------------------------------------
[ -r "$KEYFILE" ] || { log "missing key file $KEYFILE"; exit 1; }

TMPKEY=/run/operator-unlock-key.$$
[ -w /run ] || TMPKEY=/tmp/operator-unlock-key.$$
if [ "$(head -c 3 "$KEYFILE" 2>/dev/null | od -An -tx1 2>/dev/null | tr -d ' \n')" = "efbbbf" ]; then
    tail -c +4 "$KEYFILE" 2>/dev/null | tr -d '\r' >"$TMPKEY"
else
    tr -d '\r' <"$KEYFILE" >"$TMPKEY"
fi
if [ ! -s "$TMPKEY" ]; then
    rm -f "$TMPKEY"
    log "empty or unreadable key file"
    exit 1
fi
KEY=$(awk '
    /^[[:space:]]*(ssh-|ecdsa-|sk-ssh-|sk-ecdsa-)/ {
        sub(/^[[:space:]]+/, "")
        print
        exit
    }
' "$TMPKEY")
rm -f "$TMPKEY"

case "$KEY" in
    ssh-*|ecdsa-*|sk-ssh-*|sk-ecdsa-*) ;;
    *)
        log "invalid public key (must start with ssh-/ecdsa-/sk-ssh-/sk-ecdsa-)"
        exit 1
        ;;
esac

if [ "${#KEY}" -gt 4096 ]; then
    log "public key is too long"
    exit 1
fi

KEY_TYPE=$(printf '%s\n' "$KEY" | awk '{ print $1 }')
KEY_BODY=$(printf '%s\n' "$KEY" | awk '{ print $2 }')
[ -n "$KEY_TYPE" ] && [ -n "$KEY_BODY" ] || {
    log "invalid public key (missing type/body)"
    exit 1
}
# Always use a stable comment so re-runs/rotations can find and replace the
# operator key even if the uploaded key had a different comment.
INSTALL_KEY="$KEY_TYPE $KEY_BODY mazda-unlock"

# --- install / merge authorized_keys ---------------------------------------
if ! mkdir -p "$SSH_DIR" 2>/dev/null; then
    log "cannot create $SSH_DIR"
    exit 1
fi
if [ ! -w "$HOME_DIR" ] || [ ! -w "$SSH_DIR" ]; then
    log "no write access to $SSH_DIR"
    exit 1
fi

chmod 755 "$HOME_DIR" 2>/dev/null || true
chown 0:0 "$HOME_DIR" 2>/dev/null || true
chmod 700 "$SSH_DIR" 2>/dev/null || true
chown 0:0 "$SSH_DIR" 2>/dev/null || true

if [ -L "$DEST" ]; then
    log "refusing: $DEST is a symlink"
    exit 1
fi

if [ -f "$DEST" ]; then
    if [ ! -f "$UNLOCK_DIR/authorized_keys.bak" ]; then
        cp -f "$DEST" "$UNLOCK_DIR/authorized_keys.bak" 2>/dev/null || true
    fi
fi

TMP=$SSH_DIR/.authorized_keys.$$
if [ -f "$DEST" ]; then
    awk -v k="$KEY_BODY" '
        index($0, "mazda-unlock") == 0 && index($0, k) == 0 { print }
    ' "$DEST" >"$TMP" 2>/dev/null || : >"$TMP"
else
    : >"$TMP"
fi
printf '%s\n' "$INSTALL_KEY" >>"$TMP" || { log "cannot write $TMP"; exit 1; }
chmod 600 "$TMP" 2>/dev/null || true
chown 0:0 "$TMP" 2>/dev/null || true
if ! mv -f "$TMP" "$DEST" 2>/dev/null; then
    log "cannot replace $DEST"
    exit 1
fi

# --- verify ----------------------------------------------------------------
if ! grep -qF -e "$INSTALL_KEY" "$DEST" 2>/dev/null; then
    log "verification failed: key not found in $DEST"
    exit 1
fi
MODE=$(stat -c %a "$DEST" 2>/dev/null || echo "")
if [ "$MODE" != "600" ]; then
    chmod 600 "$DEST" 2>/dev/null || true
    MODE=$(stat -c %a "$DEST" 2>/dev/null || echo "unknown")
fi
chown 0:0 "$DEST" 2>/dev/null || true

KEY_TYPE=$(printf '%s\n' "$KEY" | awk '{ print $1 }')
log "OK key_type=$KEY_TYPE key_len=${#KEY} dest=$DEST mode=$MODE"
: >"$STATE" 2>/dev/null || true
sync 2>/dev/null || true
exit 0
