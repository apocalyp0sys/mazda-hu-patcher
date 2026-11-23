# ---------------------------------------------------------------------------
# Patch hook for GPS/timezone.
#
# The hook is optional. hu_patcher.py calls only the functions that exist.
# ---------------------------------------------------------------------------


def describe(api, ssh, patch):
    """Extra lines shown in the status table."""
    localtime, timezone = api.remote_tz_state(ssh)
    return ["localtime: %s" % localtime, "timezone : %s" % timezone]


def pre_install(api, ssh, patch):
    """Ask for the timezone before the library is replaced."""
    return {"zone": api.prompt_zone(ssh)}


def post_install(api, ssh, patch, state):
    zone = (state or {}).get("zone")
    if not zone:
        return True
    ok, _out = api.set_timezone(ssh, zone)
    if not ok:
        if (state or {}).get("already"):
            print("ERROR: timezone setup failed on an already-patched library.")
        return False
    print("Timezone set: %s" % zone)
    return True


def post_rollback(api, ssh, patch, state):
    ok, _out = api.restore_timezone(ssh)
    if not ok:
        print("WARNING: timezone backup not restored "
              "(no backup or /etc not writable).")
    return True


def self_check(api):
    """Called by hu_patcher.py --self-check."""
    return api.self_test_zone_map()
