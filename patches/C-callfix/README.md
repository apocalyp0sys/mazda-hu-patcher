# Callfix (loudness)

Installs `svp-callfix`, a small aarch64 daemon that re-asserts **loudness OFF**
after a phone call. On this firmware the MCU/DSP applies the loudness profile
again when the call ends; `svp-callfix` listens to
`v1.svp.source.Source::SourceStatusChanged` and sends the MCU sequence

```text
05 7D 61 02 00 01   loudness ON
   ...150 ms...
05 7D 61 02 00 00   loudness OFF
   ...800 ms...
05 7D 61 02 00 00   repeat
```

No firmware files are patched. The fix is one daemon plus its systemd unit.

## Files

| Target | Mode | Purpose |
|---|---|---|
| `/svp/bin/svp-callfix` | `0755` | aarch64 daemon (prebuilt) |
| `/etc/systemd/system/svp-callfix.service` | `0644` | systemd unit with the SVP environment |
| `/etc/systemd/system/multi-user.target.wants/svp-callfix.service` | symlink | `WantedBy=multi-user.target` |

The unit runs after `svp-launcher.service`:

```ini
[Unit]
Description=svp-callfix (loudness re-assert after BT call)
ConditionPathExists=/svp/bin/svp-callfix
After=svp-launcher.service
Wants=svp-launcher.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart=/svp/bin/svp-callfix
Environment=LD_LIBRARY_PATH=/svp/lib/someip:/svp/lib:/usr/lib64
Environment=COMMONAPI_DEFAULT_BINDING=someip
Environment=COMMONAPI_CONFIG=/svp/etc/someip/commonapi.ini
Environment=VSOMEIP_CONFIGURATION=/svp/etc/vsomeip.json
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
```

## Install

The patch engine creates the three files. Then the hook runs:

```sh
systemctl daemon-reload
systemctl restart svp-callfix.service
```

If the service does not start, the patcher stops it and rolls the files back.

## Rollback

The hook runs `systemctl stop svp-callfix.service`, then the patch engine
removes the binary, the unit and the enable symlink. Finally the hook runs
`systemctl daemon-reload`.

## Check on the HU

```sh
systemctl status svp-callfix.service
journalctl -u svp-callfix -f
```

After a BT call, the log should show:

```text
[i] no phone source active -> armed (...)
[i] media source resumed: SID_... -> fix in ... ms
[i] send loudness ON  : 05 7D 61 02 00 01
[i] send loudness OFF : 05 7D 61 02 00 00
[i] send loudness OFF : 05 7D 61 02 00 00
[i] === done (ok=1) ===
```

Manual trigger at any time:

```sh
kill -USR1 $(pidof svp-callfix)
```

## Upgrading from the manual install

If `loudness/install-callfix.sh` was already used, the files may not match this
manifest byte-for-byte (for example, the unit may contain extra daemon
arguments). In that case the patch reports `Conflict (unknown file version)`
and changes nothing. Remove the manual install first:

```sh
sh /tmp/uninstall-callfix.sh
```

Then run the patcher again.

## OTA

An SVP/OTA update replaces `/svp`, so `/svp/bin/svp-callfix` is lost. The
systemd unit lives in `/etc` and may survive, but the unit has
`ConditionPathExists=/svp/bin/svp-callfix`, so it simply does nothing until the
patcher is run again. Re-run the patch after an OTA.
