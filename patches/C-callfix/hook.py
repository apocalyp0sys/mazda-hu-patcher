# ---------------------------------------------------------------------------
# Patch hook for Callfix (loudness).
#
# The manifest installs three files:
#   /svp/bin/svp-callfix                                  (daemon)
#   /etc/systemd/system/svp-callfix.service               (unit)
#   /etc/systemd/system/multi-user.target.wants/...        (enable symlink)
#
# This hook only performs the systemd actions that a manifest cannot express:
# daemon-reload / start after install and stop / daemon-reload on rollback.
# The svp.startup.sh fallback from loudness/install-callfix.sh is intentionally
# not implemented: the patch uses the systemd mode only.
# ---------------------------------------------------------------------------


def _root(ssh, command, timeout=60):
    return ssh.run("root", command, timeout=timeout)


def pre_install(api, ssh, patch):
    r = _root(
        ssh,
        "if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1 "
        "&& [ -w /etc/systemd/system ]; then echo YES; else echo NO; fi")
    out = (r.stdout or "").strip()
    if "YES" not in out:
        print("ERROR: Callfix needs systemd and a writable /etc/systemd/system.")
        print("       This patch does not use the svp.startup.sh fallback.")
        print("       Output: %s" % (out or "(empty)"))
        return False
    return None


def post_install(api, ssh, patch, state):
    r = _root(ssh, "systemctl daemon-reload && "
                   "systemctl restart svp-callfix.service")
    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.returncode != 0:
        print("ERROR: svp-callfix.service did not start.")
        print("       Check: journalctl -u svp-callfix -n 50")
        _root(ssh, "systemctl stop svp-callfix.service 2>/dev/null || true")
        return False
    print("svp-callfix.service started.")
    return True


def pre_rollback(api, ssh, patch):
    r = _root(ssh, "systemctl stop svp-callfix.service 2>/dev/null || true")
    out = (r.stdout or "").strip()
    if out:
        print(out)
    return None


def post_rollback(api, ssh, patch, state):
    _root(ssh, "systemctl daemon-reload 2>/dev/null || true")
    return True
