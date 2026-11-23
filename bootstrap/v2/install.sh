#!/bin/sh
# ---------------------------------------------------------------------------
# install.sh -- install the "variant B" root bootstrap on a Mazda CMU.
#
# Run ON THE HEAD UNIT as user 'media' (or root):
#   sh /tmp/v2/install.sh --key /tmp/bootstrap_key.pub
#
# It does NOT modify any existing system file.  It only adds:
#   /svp/unlock/boot.sh
#   /svp/unlock/authorized_key.pub
#   /svp/bin/sysctl                  (one-shot shim)
#
# Variant B relies on /svp/etc/svp.env.extra.sh calling "sysctl" by name.
# The check below refuses to install if that trigger is not present.
# ---------------------------------------------------------------------------

set -u
umask 022

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
SVP_DIR=${UNLOCK_SVP:-/svp}
KEY_SRC=

usage() {
    cat <<'EOF'
Usage: install.sh [options]

  --key FILE       public key to install (required)
  --svp DIR        SVP mount point (default: /svp)
  -h, --help       this help
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

warn() {
    printf 'WARN: %s\n' "$*" >&2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --key)
            shift
            [ $# -gt 0 ] || die "--key needs a value"
            KEY_SRC=$1
            ;;
        --key=*) KEY_SRC=${1#*=} ;;
        --svp)
            shift
            [ $# -gt 0 ] || die "--svp needs a value"
            SVP_DIR=$1
            ;;
        --svp=*) SVP_DIR=${1#*=} ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
    shift
done

UNLOCK_DIR=$SVP_DIR/unlock
SHIM=$SVP_DIR/bin/sysctl
BOOT=$UNLOCK_DIR/boot.sh
KEY_DST=$UNLOCK_DIR/authorized_key.pub

# --- helpers ---------------------------------------------------------------
# Normalize CRLF and an optional UTF-8 BOM.  Written as a function so the
# installed files are LF-only even if the repository was checked out on Windows.
normalize() {
    src=$1
    dst=$2
    if command -v od >/dev/null 2>&1 \
        && [ "$(head -c 3 "$src" 2>/dev/null | od -An -tx1 2>/dev/null | tr -d ' \n')" = "efbbbf" ]; then
        tail -c +4 "$src" 2>/dev/null | tr -d '\r' >"$dst"
    else
        tr -d '\r' <"$src" >"$dst"
    fi
}

TMP_SHIM=
TMP_KEY=
TMP_BOOT=
cleanup() {
    if [ -n "${TMP_SHIM:-}" ]; then
        rm -f "$TMP_SHIM" 2>/dev/null || true
    fi
    if [ -n "${TMP_KEY:-}" ]; then
        rm -f "$TMP_KEY" 2>/dev/null || true
    fi
    if [ -n "${TMP_BOOT:-}" ]; then
        rm -f "$TMP_BOOT" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM HUP

# --- checks ----------------------------------------------------------------
[ -d "$SVP_DIR" ] || die "$SVP_DIR not found (is /svp mounted?)"
[ -d "$SVP_DIR/bin" ] || die "$SVP_DIR/bin not found"
[ -d "$SVP_DIR/etc" ] || die "$SVP_DIR/etc not found"
[ -w "$SVP_DIR" ] || die "no write access to $SVP_DIR (need /svp rw and media write permission)"
[ -w "$SVP_DIR/bin" ] || die "no write access to $SVP_DIR/bin"

if [ "${UNLOCK_SKIP_MOUNT_CHECK:-0}" != "1" ]; then
    OPTS=$(awk -v m="$SVP_DIR" '$2 == m { print $4; exit }' /proc/mounts 2>/dev/null)
    if [ -n "$OPTS" ]; then
        case ",$OPTS," in
            *,ro,*) die "$SVP_DIR is mounted read-only" ;;
            *,noexec,*) die "$SVP_DIR is mounted noexec; variant B needs exec" ;;
        esac
    else
        warn "$SVP_DIR is not listed in /proc/mounts"
    fi
fi

[ -f "$SCRIPT_DIR/boot.sh" ] || die "missing $SCRIPT_DIR/boot.sh"
[ -f "$SCRIPT_DIR/sysctl-shim.sh" ] || die "missing $SCRIPT_DIR/sysctl-shim.sh"

if ! grep -Eq '^[[:space:]]*sysctl([[:space:]]|$)' "$SVP_DIR/etc/svp.env.extra.sh" 2>/dev/null; then
    die "trigger not found: no active 'sysctl' call in $SVP_DIR/etc/svp.env.extra.sh; variant B is not applicable, use variant A"
fi
if ! grep -q 'export PATH' "$SVP_DIR/etc/svp.env.runtime.sh" 2>/dev/null; then
    warn "$SVP_DIR/etc/svp.env.runtime.sh does not look like the expected PATH setup"
fi
if [ ! -x /sbin/sysctl ] && [ ! -x /usr/sbin/sysctl ]; then
    warn "real sysctl not found at /sbin/sysctl or /usr/sbin/sysctl"
fi

# key source
[ -n "$KEY_SRC" ] || die "no key given; use --key FILE"
[ -r "$KEY_SRC" ] || die "cannot read key: $KEY_SRC"

# existing shim
if [ -e "$SHIM" ] || [ -L "$SHIM" ]; then
    if [ -L "$SHIM" ]; then
        TARGET=$(readlink "$SHIM" 2>/dev/null || true)
        case "$TARGET" in
            /sbin/sysctl|/usr/sbin/sysctl|/bin/sysctl|/bin/busybox.nosuid|/bin/busybox) ;;
            *) die "foreign symlink exists: $SHIM -> $TARGET" ;;
        esac
    elif grep -q 'operator-unlock-shim-v2' "$SHIM" 2>/dev/null; then
        :
    else
        die "foreign file exists: $SHIM (refusing to overwrite)"
    fi
fi

# --- install payload -------------------------------------------------------
mkdir -p "$UNLOCK_DIR" || die "cannot create $UNLOCK_DIR"
[ -w "$UNLOCK_DIR" ] || die "no write access to $UNLOCK_DIR"

TMP_BOOT=$UNLOCK_DIR/.boot.sh.$$
normalize "$SCRIPT_DIR/boot.sh" "$TMP_BOOT" || die "cannot write $TMP_BOOT"
chmod 0755 "$TMP_BOOT" 2>/dev/null || true
sh -n "$TMP_BOOT" 2>/dev/null || die "syntax error in installed boot.sh"
mv -f "$TMP_BOOT" "$BOOT" || die "cannot install $BOOT"
TMP_BOOT=

TMP_KEY=$UNLOCK_DIR/.authorized_key.$$
normalize "$KEY_SRC" "$TMP_KEY" || die "cannot write $TMP_KEY"
if ! awk '/^[[:space:]]*(ssh-|ecdsa-|sk-ssh-|sk-ecdsa-)/ { found = 1 } END { exit !found }' "$TMP_KEY"; then
    rm -f "$TMP_KEY"
    TMP_KEY=
    die "invalid public key in $KEY_SRC"
fi
chmod 0644 "$TMP_KEY" 2>/dev/null || true
mv -f "$TMP_KEY" "$KEY_DST" || {
    rm -f "$TMP_KEY"
    TMP_KEY=
    die "cannot install $KEY_DST"
}
TMP_KEY=

# --- install the sysctl shim (last, atomically) ----------------------------
TMP_SHIM=$SVP_DIR/bin/.sysctl.unlock.$$
normalize "$SCRIPT_DIR/sysctl-shim.sh" "$TMP_SHIM" || die "cannot write $TMP_SHIM"
chmod 0755 "$TMP_SHIM" 2>/dev/null || true
sh -n "$TMP_SHIM" 2>/dev/null || die "syntax error in $TMP_SHIM"
FIRST=$(head -n 1 "$TMP_SHIM" 2>/dev/null || true)
[ "$FIRST" = '#!/bin/sh' ] || die "bad shebang in $TMP_SHIM"
mv -f "$TMP_SHIM" "$SHIM" || die "cannot install $SHIM"
TMP_SHIM=
[ -x "$SHIM" ] || die "$SHIM is not executable"

# --- report ----------------------------------------------------------------
echo "[+] installed: $BOOT"
echo "[+] installed: $KEY_DST"
echo "[+] installed: $SHIM"
if command -v ssh-keygen >/dev/null 2>&1; then
    FP=$(ssh-keygen -lf "$KEY_DST" 2>/dev/null || true)
    [ -n "$FP" ] && echo "[i] key: $FP"
fi
echo
echo "NEXT: reboot the head unit, then:"
echo "  ssh -i <private-key> root@<HU>"
echo "VERIFY:"
echo "  cat $UNLOCK_DIR/last.log"
echo "  ls -l $SHIM        # after success: symlink to the real sysctl"
