# ---------------------------------------------------------------------------
# Patch hook for Fast Login.
#
# The manifest targets /home/root because that is root's home on this
# firmware.  Abort if /etc/passwd says otherwise: writing the mask to the
# wrong directory would have no effect.
# ---------------------------------------------------------------------------

EXPECTED_HOME = "/home/root"


def _root_home(ssh):
    r = ssh.run("root",
                "awk -F: '$1 == \"root\" { print $6; exit }' /etc/passwd")
    if r.returncode != 0:
        return ""
    return (r.stdout or "").strip()


def describe(api, ssh, patch):
    home = _root_home(ssh)
    if home:
        return ["root home: %s" % home]
    return []


def pre_install(api, ssh, patch):
    home = _root_home(ssh)
    if home and home != EXPECTED_HOME:
        print("ERROR: Fast Login expects root home %s, but this unit uses %s." %
              (EXPECTED_HOME, home))
        print("       Refusing to write the systemd mask to the wrong home.")
        return False
    return None
