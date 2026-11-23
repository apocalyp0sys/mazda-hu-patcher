#!/bin/sh
# ---------------------------------------------------------------------------
# bootstrap.sh -- container test for the bundled root bootstrap (bootstrap/v2).
#
# Covers: shell syntax, root-home resolution, boot.sh on a dedicated /home
# mount, deferral when /home is not mounted, the userdata-source fallback,
# the sysctl shim and preflight on the unit layout.
#
# Run from the patcher root in the mazda-tools container:
#   docker run --rm --privileged -v "<patcher>:/work" -w /work mazda-tools \
#       sh test/bootstrap.sh
#
# --privileged (or CAP_SYS_ADMIN) is needed for the tmpfs mounts at /home and
# /mnt/userdata; without it the mount-specific checks are reported as SKIP
# and boot.sh is exercised with UNLOCK_SKIP_MOUNT_CHECK=1 instead.
#
# The test refuses to run outside a container: it creates /svp, changes
# root's home in the container's /etc/passwd and mounts tmpfs filesystems.
# Set BOOTSTRAP_TEST_IN_CONTAINER=1 only for a runtime without
# /.dockerenv or /run/.containerenv.
# ---------------------------------------------------------------------------
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd) || exit 1
V2=$ROOT/bootstrap/v2

if [ ! -e /.dockerenv ] && [ ! -e /run/.containerenv ] \
    && [ "${BOOTSTRAP_TEST_IN_CONTAINER:-0}" != "1" ]; then
    echo "ERROR: run this test in a container (see the header comment)" >&2
    exit 2
fi
if [ "$(id -u 2>/dev/null)" != "0" ]; then
    echo "ERROR: this test must run as root" >&2
    exit 2
fi
if [ -e /svp ]; then
    echo "ERROR: /svp already exists; use a fresh container" >&2
    exit 2
fi

TMPD=$(mktemp -d "${TMPDIR:-/tmp}/hu_patcher_bootstrap.XXXXXX") || exit 1
HOME_MOUNTED=0
DATA_MOUNTED=0

cleanup() {
    [ "$HOME_MOUNTED" = "1" ] && umount /home 2>/dev/null
    [ "$DATA_MOUNTED" = "1" ] && umount /mnt/userdata 2>/dev/null
    rm -rf "$TMPD"
    return 0
}
trap cleanup EXIT INT TERM HUP

fail=0
ok()   { printf 'ok   - %s\n' "$*"; }
bad()  { printf 'FAIL - %s\n' "$*"; fail=1; }
skip() { printf 'skip - %s\n' "$*"; }

mount_home() {
    mkdir -p /home || return 1
    mount -t tmpfs tmpfs /home 2>/dev/null || return 1
    HOME_MOUNTED=1
    return 0
}

mount_userdata() {
    mkdir -p /mnt/userdata || return 1
    mount -t tmpfs tmpfs /mnt/userdata 2>/dev/null || return 1
    DATA_MOUNTED=1
    return 0
}

# ---------------------------------------------------------------------------
# 1. syntax
# ---------------------------------------------------------------------------
echo "== syntax =="
for name in install preflight boot sysctl-shim status uninstall; do
    if sh -n "$V2/$name.sh"; then ok "syntax $name.sh"; else bad "syntax $name.sh"; fi
done

# ---------------------------------------------------------------------------
# 2. root-home resolution (mirrors mount_point() in boot.sh)
# ---------------------------------------------------------------------------
echo
echo "== root home resolution =="

mount_point_file() {
    awk -v p="$2" '
        {
            m = $2
            gsub(/\\040/, " ", m)
            if (m == "/" || p == m || index(p, m "/") == 1) {
                if (length(m) > length(best)) best = m
            }
        }
        END { if (best == "") best = "/"; print best }
    ' "$1" 2>/dev/null
}

check_mp() {
    got=$(mount_point_file "$1" "$2")
    if [ "$got" = "$3" ]; then ok "$2 -> $got"; else bad "$2 -> $got (want $3)"; fi
}

cat >"$TMPD/m-device" <<'EOF'
/dev/mmcblk0p4 / ext4 rw,relatime 0 0
/dev/mmcblk0p6 /home ext4 rw,relatime,data=ordered 0 0
proc /proc proc rw 0 0
EOF
cat >"$TMPD/m-bind" <<'EOF'
/dev/mmcblk0p4 / ext4 rw,relatime 0 0
/dev/mmcblk0p6 /mnt/userdata ext4 rw,relatime,data=ordered 0 0
/dev/mmcblk0p6 /home ext4 rw,relatime,data=ordered 0 0
EOF
cat >"$TMPD/m-early" <<'EOF'
/dev/mmcblk0p4 / ext4 rw,relatime 0 0
/dev/mmcblk0p6 /mnt/userdata ext4 rw,relatime,data=ordered 0 0
EOF

check_mp "$TMPD/m-device" /home/root /home
check_mp "$TMPD/m-device" /mnt/userdata/home/root /
check_mp "$TMPD/m-device" /root /
check_mp "$TMPD/m-bind" /home/root /home
check_mp "$TMPD/m-bind" /mnt/userdata/home/root /mnt/userdata
check_mp "$TMPD/m-early" /home/root /
check_mp "$TMPD/m-early" /mnt/userdata/home/root /mnt/userdata

# ---------------------------------------------------------------------------
# 3. payload staged like install.sh does
# ---------------------------------------------------------------------------
echo
echo "== payload install =="
mkdir -p /svp/unlock /svp/bin /svp/etc || bad "cannot create /svp"
printf '%s\n' \
    'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEYBODYtesttesttesttesttesttestte test@host' \
    >"$TMPD/bootstrap_key.pub" || bad "cannot write test key"
cp "$TMPD/bootstrap_key.pub" /svp/unlock/authorized_key.pub || bad "cannot stage key"
cp "$V2/boot.sh" /svp/unlock/boot.sh || bad "cannot stage boot.sh"
chmod 0755 /svp/unlock/boot.sh 2>/dev/null || true
printf '%s\n' 'sysctl -w kernel.test=1' >/svp/etc/svp.env.extra.sh
printf '%s\n' 'export PATH=/svp/bin:$PATH' >/svp/etc/svp.env.runtime.sh
printf '%s\n' '. /svp/etc/svp.env.extra.sh' >/svp/etc/svp.env.setup.sh

# ---------------------------------------------------------------------------
# 4. mount-dependent scenarios
# ---------------------------------------------------------------------------
if mount_home; then
    # Emulate the unit: root's home is /home/root (see its /etc/passwd).
    sed -i 's|^root:\([^:]*\):\([^:]*\):\([^:]*\):\([^:]*\):[^:]*:|root:\1:\2:\3:\4:/home/root:|' /etc/passwd
    pw_home=$(awk -F: '$1 == "root" { print $6; exit }' /etc/passwd)
    if [ "$pw_home" = "/home/root" ]; then
        ok "root home emulated as /home/root"
    else
        bad "cannot emulate root home /home/root (got $pw_home)"
    fi
    mkdir -p /home/root

    echo
    echo "== boot.sh on a dedicated /home mount =="
    rm -f /svp/unlock/installed /svp/unlock/last.log
    if sh /svp/unlock/boot.sh; then ok "boot.sh rc=0"; else bad "boot.sh failed"; fi
    AK=/home/root/.ssh/authorized_keys
    grep -q 'mazda-unlock' "$AK" 2>/dev/null \
        && ok "key installed in /home/root/.ssh/authorized_keys" \
        || bad "key not installed"
    mode=$(stat -c %a "$AK" 2>/dev/null || echo "?")
    [ "$mode" = "600" ] && ok "authorized_keys mode 600" || bad "mode $mode"
    [ -f /svp/unlock/installed ] && ok "installed marker" || bad "no marker"
    grep -q 'root home /home/root (mount /home)' /svp/unlock/last.log 2>/dev/null \
        && ok "log shows the resolved home" || bad "log does not show the resolved home"

    sh /svp/unlock/boot.sh
    lines=$(grep -c 'mazda-unlock' "$AK" 2>/dev/null || true)
    [ "$lines" = "1" ] && ok "idempotent re-run (one key line)" \
        || bad "expected one key line, got $lines"

    echo
    echo "== preflight on the unit layout =="
    if sh "$V2/preflight.sh" >"$TMPD/preflight.out" 2>&1; then
        ok "preflight rc=0"
    else
        bad "preflight failed"
        sed 's/^/    /' "$TMPD/preflight.out"
    fi
    grep -q 'root home /home/root (mount /home)' "$TMPD/preflight.out" 2>/dev/null \
        && ok "preflight resolved the root home" \
        || bad "preflight did not resolve the root home"

    echo
    echo "== deferred when /home is not mounted =="
    umount /home; HOME_MOUNTED=0
    rm -f /svp/unlock/installed
    if UNLOCK_WAIT_SECONDS=0 sh /svp/unlock/boot.sh; then rc=0; else rc=$?; fi
    [ "$rc" = "75" ] && ok "boot.sh exited 75" || bad "expected exit 75, got $rc"
    [ -f /svp/unlock/installed ] && bad "marker written while deferred" \
        || ok "no marker while deferred"
    [ -e /home/root/.ssh ] && bad "key written into unmounted /home" \
        || ok "nothing written into the unmounted /home"

    echo
    echo "== fallback to the userdata source =="
    if mount_userdata; then
        mkdir -p /mnt/userdata/home/root
        rm -f /svp/unlock/installed /svp/unlock/last.log
        if sh /svp/unlock/boot.sh; then ok "boot.sh rc=0 via /mnt/userdata"; else bad "boot.sh failed"; fi
        AKU=/mnt/userdata/home/root/.ssh/authorized_keys
        grep -q 'mazda-unlock' "$AKU" 2>/dev/null \
            && ok "key installed under /mnt/userdata" || bad "key not installed"
        grep -q 'root home /mnt/userdata/home/root (mount /mnt/userdata)' \
            /svp/unlock/last.log 2>/dev/null \
            && ok "resolved the userdata source" || bad "userdata source not resolved"
        umount /mnt/userdata; DATA_MOUNTED=0
    else
        skip "userdata fallback (cannot mount /mnt/userdata)"
    fi
else
    skip "dedicated /home mount (needs --privileged)"

    echo
    echo "== boot.sh with mount checks skipped (no --privileged) =="
    mkdir -p "$TMPD/home/root"
    rm -f /svp/unlock/installed /svp/unlock/last.log
    if UNLOCK_SKIP_MOUNT_CHECK=1 UNLOCK_HOME_DIR="$TMPD/home/root" \
        sh /svp/unlock/boot.sh; then
        ok "boot.sh rc=0"
    else
        bad "boot.sh failed"
    fi
    grep -q 'mazda-unlock' "$TMPD/home/root/.ssh/authorized_keys" 2>/dev/null \
        && ok "key installed" || bad "key not installed"
    [ -f /svp/unlock/installed ] && ok "installed marker" || bad "no marker"

    echo
    echo "== preflight with mount checks skipped =="
    if UNLOCK_SKIP_MOUNT_CHECK=1 sh "$V2/preflight.sh" >"$TMPD/preflight.out" 2>&1; then
        ok "preflight rc=0"
    else
        bad "preflight failed"
        sed 's/^/    /' "$TMPD/preflight.out"
    fi
    skip "deferral/userdata checks (needs --privileged)"
fi

# ---------------------------------------------------------------------------
# 5. sysctl shim
# ---------------------------------------------------------------------------
echo
echo "== sysctl shim =="
printf '%s\n' '#!/bin/sh' 'echo "real-sysctl $*"' >"$TMPD/real-sysctl"
chmod 0755 "$TMPD/real-sysctl"
cp "$V2/sysctl-shim.sh" /svp/bin/sysctl || bad "cannot stage the shim"
chmod 0755 /svp/bin/sysctl 2>/dev/null || true
mkdir -p "$TMPD/shim-home/root"
rm -f /svp/unlock/installed /run/operator-unlock.tried /tmp/operator-unlock.tried

out=$(UNLOCK_SKIP_MOUNT_CHECK=1 UNLOCK_HOME_DIR="$TMPD/shim-home/root" \
    UNLOCK_REAL_SYSCTL="$TMPD/real-sysctl" sh /svp/bin/sysctl -a 2>&1)
rc=$?
printf '%s\n' "$out" | grep -q 'real-sysctl -a' \
    && ok "pass-through to the real sysctl" || bad "no pass-through: $out"
[ "$rc" = "0" ] && ok "shim rc=0" || bad "shim rc=$rc"
[ -L /svp/bin/sysctl ] && ok "shim replaced itself with a symlink" \
    || bad "shim was not replaced"
[ "$(readlink /svp/bin/sysctl 2>/dev/null)" = "$TMPD/real-sysctl" ] \
    && ok "symlink target is the real sysctl" || bad "wrong symlink target"
grep -q 'mazda-unlock' "$TMPD/shim-home/root/.ssh/authorized_keys" 2>/dev/null \
    && ok "key installed via the shim" || bad "key not installed via the shim"
[ -f /svp/unlock/installed ] && ok "installed marker via the shim" \
    || bad "no marker via the shim"

echo
if [ "$fail" = 0 ]; then
    echo "BOOTSTRAP_TEST: OK"
else
    echo "BOOTSTRAP_TEST: FAILED" >&2
fi
exit "$fail"
