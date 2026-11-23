#!/bin/sh
# ---------------------------------------------------------------------------
# status.sh -- inspect the variant B bootstrap (no root required).
#
# Usage: status.sh [--svp DIR]
# ---------------------------------------------------------------------------

set -u

SVP_DIR=${UNLOCK_SVP:-/svp}
while [ $# -gt 0 ]; do
    case "$1" in
        --svp)
            shift
            [ $# -gt 0 ] || { echo "ERROR: --svp needs a value" >&2; exit 2; }
            SVP_DIR=$1
            ;;
        --svp=*) SVP_DIR=${1#*=} ;;
        -h|--help) echo "Usage: status.sh [--svp DIR]"; exit 0 ;;
        *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

UNLOCK_DIR=$SVP_DIR/unlock
SHIM=$SVP_DIR/bin/sysctl
BOOT=$UNLOCK_DIR/boot.sh
KEY=$UNLOCK_DIR/authorized_key.pub
LOG=$UNLOCK_DIR/last.log

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
if [ "$(mount_point "$HOME_DIR")" = "/" ]; then
    case "$HOME_DIR" in
        /home/*)
            ALT=/mnt/userdata$HOME_DIR
            if [ -d "$ALT" ] && [ "$(mount_point "$ALT")" != "/" ]; then
                HOME_DIR=$ALT
            fi
            ;;
    esac
fi

echo "== operator unlock v2 (variant B) =="
echo "svp:          $SVP_DIR"
echo "root home:    $HOME_DIR (mount $(mount_point "$HOME_DIR"))"
echo "uid:          $(id -u 2>/dev/null) ($(id 2>/dev/null))"

echo
echo "-- files --"
if [ -L "$SHIM" ]; then
    echo "shim:         symlink -> $(readlink "$SHIM" 2>/dev/null)"
elif [ -f "$SHIM" ]; then
    if grep -q 'operator-unlock-shim-v2' "$SHIM" 2>/dev/null; then
        echo "shim:         script (pending first boot)"
    else
        echo "shim:         FOREIGN file (not ours)"
    fi
else
    echo "shim:         absent"
fi
[ -f "$SHIM" ] && ls -l "$SHIM" 2>/dev/null

if [ -f "$BOOT" ]; then
    echo "boot.sh:      present"
    ls -l "$BOOT" 2>/dev/null
    if sh -n "$BOOT" 2>/dev/null; then
        echo "boot.sh:      syntax OK"
    else
        echo "boot.sh:      SYNTAX ERROR"
    fi
else
    echo "boot.sh:      absent"
fi

if [ -f "$KEY" ]; then
    echo "key file:     present"
    if command -v ssh-keygen >/dev/null 2>&1; then
        ssh-keygen -lf "$KEY" 2>/dev/null || true
    else
        awk '{ print $1, substr($2, 1, 16) "..." }' "$KEY" 2>/dev/null || true
    fi
else
    echo "key file:     absent"
fi

if [ -f "$UNLOCK_DIR/installed" ]; then
    echo "installed:    marker present"
else
    echo "installed:    no marker"
fi

echo
echo "-- root key (may be unreadable as media) --"
if [ -d "$HOME_DIR/.ssh" ]; then
    ls -ld "$HOME_DIR/.ssh" 2>/dev/null || true
    if [ -r "$HOME_DIR/.ssh/authorized_keys" ]; then
        ls -l "$HOME_DIR/.ssh/authorized_keys" 2>/dev/null || true
    else
        echo "authorized_keys is root-only; inspect it as root"
    fi
else
    echo "not found: $HOME_DIR/.ssh"
fi

echo
echo "-- log (last 20 lines) --"
if [ -r "$LOG" ]; then
    tail -n 20 "$LOG" 2>/dev/null
elif [ -r /tmp/operator-unlock.log ]; then
    tail -n 20 /tmp/operator-unlock.log 2>/dev/null
else
    echo "no log yet"
fi

exit 0
