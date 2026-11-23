#!/bin/sh
# ---------------------------------------------------------------------------
# preflight.sh -- check whether variant B is applicable on this unit.
#
# Usage: preflight.sh [--svp DIR]
# Exit: 0 = B looks applicable, 1 = critical check failed.
# ---------------------------------------------------------------------------

set -u

SVP_DIR=${UNLOCK_SVP:-/svp}

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

while [ $# -gt 0 ]; do
    case "$1" in
        --svp)
            shift
            [ $# -gt 0 ] || { echo "ERROR: --svp needs a value" >&2; exit 2; }
            SVP_DIR=$1
            ;;
        --svp=*) SVP_DIR=${1#*=} ;;
        -h|--help) echo "Usage: preflight.sh [--svp DIR]"; exit 0 ;;
        *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

fail=0
ok()   { printf 'OK    %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; }
bad()  { printf 'FAIL  %s\n' "$*"; fail=1; }

echo "== preflight: operator unlock v2 / variant B =="

# --- SVP tree --------------------------------------------------------------
if [ -d "$SVP_DIR" ]; then ok "$SVP_DIR exists"; else bad "$SVP_DIR missing"; fi
if [ -d "$SVP_DIR/bin" ]; then ok "$SVP_DIR/bin exists"; else bad "$SVP_DIR/bin missing"; fi
if [ -d "$SVP_DIR/etc" ]; then ok "$SVP_DIR/etc exists"; else bad "$SVP_DIR/etc missing"; fi
if [ -w "$SVP_DIR" ]; then ok "$SVP_DIR is writable"; else bad "$SVP_DIR is not writable (uid $(id -u 2>/dev/null || echo ?))"; fi
if [ -d "$SVP_DIR/bin" ] && [ -w "$SVP_DIR/bin" ]; then ok "$SVP_DIR/bin is writable"; else bad "$SVP_DIR/bin is not writable"; fi

# --- mount options ---------------------------------------------------------
if [ "${UNLOCK_SKIP_MOUNT_CHECK:-0}" != "1" ]; then
    OPTS=$(awk -v m="$SVP_DIR" '$2 == m { print $4; exit }' /proc/mounts 2>/dev/null)
    if [ -z "$OPTS" ]; then
        warn "$SVP_DIR is not in /proc/mounts"
    else
        ok "$SVP_DIR mount options: $OPTS"
        case ",$OPTS," in *,ro,*) bad "$SVP_DIR is read-only" ;; esac
        case ",$OPTS," in *,noexec,*) bad "$SVP_DIR is noexec (variant B needs exec)" ;; esac
    fi
fi

# --- trigger ---------------------------------------------------------------
if [ -f "$SVP_DIR/etc/svp.env.extra.sh" ]; then
    if grep -Eq '^[[:space:]]*sysctl([[:space:]]|$)' "$SVP_DIR/etc/svp.env.extra.sh" 2>/dev/null; then
        ok "trigger present: svp.env.extra.sh calls sysctl"
    else
        bad "no active 'sysctl' call in svp.env.extra.sh; variant B is not applicable"
    fi
else
    bad "$SVP_DIR/etc/svp.env.extra.sh missing"
fi

if [ -f "$SVP_DIR/etc/svp.env.runtime.sh" ] \
    && grep -q 'export PATH' "$SVP_DIR/etc/svp.env.runtime.sh" 2>/dev/null; then
    ok "svp.env.runtime.sh sets PATH"
else
    warn "svp.env.runtime.sh not found or does not set PATH"
fi

if [ -f "$SVP_DIR/etc/svp.env.setup.sh" ] \
    && grep -q 'svp.env.extra.sh' "$SVP_DIR/etc/svp.env.setup.sh" 2>/dev/null; then
    ok "svp.env.setup.sh sources svp.env.extra.sh"
else
    warn "cannot confirm that svp.env.setup.sh sources svp.env.extra.sh"
fi

# --- sysctl ----------------------------------------------------------------
REAL=
for c in /sbin/sysctl /usr/sbin/sysctl /bin/sysctl; do
    if [ -x "$c" ]; then REAL=$c; break; fi
done
if [ -n "$REAL" ]; then
    ok "real sysctl: $REAL"
else
    warn "real sysctl not found at the usual paths"
fi

SHIM=$SVP_DIR/bin/sysctl
if [ -L "$SHIM" ]; then
    ok "shim present as symlink -> $(readlink "$SHIM" 2>/dev/null)"
elif [ -f "$SHIM" ]; then
    if grep -q 'operator-unlock-shim-v2' "$SHIM" 2>/dev/null; then
        ok "shim present as script (pending first boot)"
    else
        bad "foreign file exists at $SHIM"
    fi
else
    ok "shim absent (ready to install)"
fi

# --- root home -------------------------------------------------------------
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
    MP=$(mount_point "$HOME_DIR")
    if [ "$MP" = "/" ]; then
        # /home may be a bind mount of userdata; the source is always visible.
        case "$HOME_DIR" in
            /home/*)
                ALT=/mnt/userdata$HOME_DIR
                if [ -d "$ALT" ] && [ "$(mount_point "$ALT")" != "/" ]; then
                    HOME_DIR=$ALT
                    MP=$(mount_point "$HOME_DIR")
                fi
                ;;
        esac
    fi
    case "$HOME_DIR" in
        /home/*)
            if [ "$MP" = "/" ]; then
                bad "root home $HOME_DIR is not on a mounted filesystem"
            else
                ok "root home $HOME_DIR (mount $MP)"
            fi
            ;;
        *) ok "root home $HOME_DIR" ;;
    esac
fi
if [ -d "$HOME_DIR" ]; then
    ok "$HOME_DIR exists"
else
    warn "$HOME_DIR not found (boot.sh will create it)"
fi

# --- sshd ------------------------------------------------------------------
SSHD_CONF=/etc/ssh/sshd_config
if [ -r "$SSHD_CONF" ]; then
    PRL=$(awk '$1 == "PermitRootLogin" { print $2; exit }' "$SSHD_CONF")
    AKF=$(awk '$1 == "AuthorizedKeysFile" { print $2; exit }' "$SSHD_CONF")
    [ -n "$PRL" ] && ok "sshd PermitRootLogin=$PRL" || warn "sshd PermitRootLogin not set explicitly"
    [ -n "$AKF" ] && ok "sshd AuthorizedKeysFile=$AKF" || warn "sshd AuthorizedKeysFile not set explicitly"
    case "$PRL" in
        yes|without-password|prohibit-password) ;;
        no) bad "sshd forbids root login; key will not work" ;;
    esac
else
    warn "$SSHD_CONF not readable"
fi

echo
if [ "$fail" = "0" ]; then
    echo "RESULT: variant B looks applicable"
    exit 0
fi
echo "RESULT: critical problems found; do not install variant B as-is" >&2
exit 1
