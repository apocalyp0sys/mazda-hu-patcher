#!/bin/sh
# ---------------------------------------------------------------------------
# uninstall.sh -- remove the variant B bootstrap.
#
# Usage: uninstall.sh [--svp DIR] [--remove-key]
#
# By default the root key in authorized_keys is LEFT IN PLACE (media cannot
# edit root's file).  Use --remove-key when running as root if you want it
# removed too.
# ---------------------------------------------------------------------------

set -u
umask 022

SVP_DIR=${UNLOCK_SVP:-/svp}
REMOVE_KEY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --svp)
            shift
            [ $# -gt 0 ] || { echo "ERROR: --svp needs a value" >&2; exit 2; }
            SVP_DIR=$1
            ;;
        --svp=*) SVP_DIR=${1#*=} ;;
        --remove-key) REMOVE_KEY=1 ;;
        -h|--help)
            echo "Usage: uninstall.sh [--svp DIR] [--remove-key]"
            exit 0
            ;;
        *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

UNLOCK_DIR=$SVP_DIR/unlock
SHIM=$SVP_DIR/bin/sysctl

PASSWD_HOME=$(awk -F: '$1 == "root" { print $6; exit }' /etc/passwd 2>/dev/null)
[ -n "$PASSWD_HOME" ] || PASSWD_HOME=/home/root
if [ -n "${UNLOCK_HOME_DIR:-}" ]; then
    HOME_DIR=$UNLOCK_HOME_DIR
elif [ -n "${UNLOCK_USERDATA:-}" ]; then
    HOME_DIR=$UNLOCK_USERDATA/home/root
else
    HOME_DIR=$PASSWD_HOME
fi

if [ -L "$SHIM" ]; then
    TARGET=$(readlink "$SHIM" 2>/dev/null || true)
    case "$TARGET" in
        /sbin/sysctl|/usr/sbin/sysctl|/bin/sysctl|/bin/busybox.nosuid|/bin/busybox)
            if rm -f "$SHIM"; then
                echo "[+] removed $SHIM (symlink)"
            else
                echo "WARN: cannot remove $SHIM" >&2
            fi
            ;;
        *)
            echo "WARN: refusing to remove foreign symlink $SHIM -> $TARGET" >&2
            ;;
    esac
elif [ -f "$SHIM" ]; then
    if grep -q 'operator-unlock-shim-v2' "$SHIM" 2>/dev/null; then
        if rm -f "$SHIM"; then
            echo "[+] removed $SHIM"
        else
            echo "WARN: cannot remove $SHIM" >&2
        fi
    else
        echo "WARN: refusing to remove foreign file $SHIM" >&2
    fi
fi

if [ -d "$UNLOCK_DIR" ]; then
    if rm -rf "$UNLOCK_DIR"; then
        echo "[+] removed $UNLOCK_DIR"
    else
        echo "WARN: cannot remove $UNLOCK_DIR (run as root?)" >&2
    fi
fi

if [ "$REMOVE_KEY" = "1" ]; then
    if [ "$(id -u 2>/dev/null)" != "0" ]; then
        echo "WARN: --remove-key requires root; leaving authorized_keys untouched" >&2
    else
        DEST=$HOME_DIR/.ssh/authorized_keys
        if [ -f "$DEST" ] && [ ! -L "$DEST" ]; then
            TMP=$DEST.tmp.$$
            if awk 'index($0, "mazda-unlock") == 0' "$DEST" >"$TMP"; then
                chmod 600 "$TMP" 2>/dev/null || true
                chown 0:0 "$TMP" 2>/dev/null || true
                if mv -f "$TMP" "$DEST"; then
                    echo "[+] removed operator key from $DEST"
                else
                    rm -f "$TMP"
                    echo "WARN: cannot replace $DEST" >&2
                fi
            else
                rm -f "$TMP"
                echo "WARN: cannot rewrite $DEST" >&2
            fi
        fi
    fi
fi

echo
echo "Note: the root public key is still in:"
echo "  $HOME_DIR/.ssh/authorized_keys"
echo "Remove it as root with:"
echo "  awk 'index(\$0,\"mazda-unlock\")==0' $HOME_DIR/.ssh/authorized_keys > /tmp/ak && chmod 600 /tmp/ak && mv /tmp/ak $HOME_DIR/.ssh/authorized_keys"
