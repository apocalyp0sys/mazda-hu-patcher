# Boot speed diagnostics

How to find out why the HU takes a long time to come back after a reboot
requested by the patcher, and how to tell shutdown time from boot time.

This document is for the **Mazda CMU KBST-41-47-M5 / DS04L** and the bundled
patcher.

## 1. What the patcher actually does

After a patch install or rollback the patcher **does not issue a reboot
command**. It prints:

```text
To reboot, hold Music, Favorite (Star) and Volume On/Off buttons for
5 seconds, then select 'End Diag Mode'.
```

When you use those buttons, the unit performs a normal systemd shutdown:

```text
/sbin/reboot -> /bin/systemctl
```

So it is not an immediate reset:

1. the UI (`weston`) stops and the screen goes black almost immediately;
2. systemd stops the remaining services, with per-unit stop timeouts;
3. the unit reboots and starts the kernel, systemd and the SVP stack;
4. the UI comes back.

The patcher waits for the boot id to change and for root SSH to return
(currently up to 600 s). A 5-minute boot can therefore look like a patcher
failure even when the HU is still booting normally.

The bootstrap path prints the same instruction and waits the same way.

## 2. Known slow points on this firmware

These are visible in the firmware image and explain a long black screen:

- `DefaultTimeoutStopSec=90s` — default stop timeout for units without their
  own value.
- `user@.service` has `TimeoutStopSec=120s`. The root user manager starts
  `lightmediascanner.service` on every login; `lightmediascannerd` cannot open
  `/usr/lib64/lightmediascanner/plugins`, so the user manager can wait for its
  startup timeout.
- `svp-launcher.service` is `Type=forking` and has no `ExecStop` or
  `TimeoutStopSec`. `/svp/etc/svp.init.sh stop` only does
  `killall svp.util.procguard`, and systemd does not call it because
  `ExecStop` is not set. If systemd cannot stop the guessed main process, it
  waits for the default 90 s and then kills the cgroup.
- The SVP services are started by `/svp/etc/svp.startup.sh` outside systemd,
  so shutdown cleanup is not complete.

A plausible 5-minute sequence is: ~120 s for `user@0`, ~90 s for
`svp-launcher`, other stop timeouts, then the boot itself.

**Fast Login note:** the patch masks `lightmediascanner.service` for root with

```text
/home/root/.config/systemd/user/lightmediascanner.service -> /dev/null
```

The mask applies when root's **next** user manager starts. The first reboot
after installing Fast Login still uses the old user manager with
`lightmediascanner` running, so that shutdown can still take up to ~120 s.
The second reboot should be noticeably faster. If it is not, the mask is not
being applied (wrong root home, different unit path, or the patch is not
installed).

Other possible causes:

- `fsck`/ext4 journal recovery on `/home` or the data partition after an
  unclean shutdown or power-cycle;
- the SVP data partition mount/check in `svp.init.fs.sh`;
- ignition/ACC state: on the bench the CMU may enter standby instead of
  showing the UI.

## 3. Quick triage: where is the time?

Run a continuous ping from the PC while the HU reboots:

```powershell
ping -t <HU>
```

Use the result like this:

| Observation | Likely stage |
|---|---|
| ping continues, SSH works, screen black | UI/display only; the OS is up |
| ping continues, SSH does not answer | systemd up, sshd/login or network not ready |
| ping stops for minutes | shutdown, kernel, fsck or power |
| ping returns, UI still black | SVP/Weston/UI startup |

Record the wall-clock times of:

1. button reboot / patcher prompt;
2. last successful ping before the gap;
3. first successful ping after the gap;
4. first successful root SSH;
5. UI back on screen.

Compare with a manual button reboot. If that reboot is also ~5 minutes, the
patcher is not the cause.

## 4. Evidence to collect on the HU

Before rebooting:

```sh
date -u
readlink -f /sbin/reboot
systemctl show -p DefaultTimeoutStopSec --value
systemctl show -p TimeoutStopUSec user@0.service
systemctl show -p Type,TimeoutStopUSec,ExecStop svp-launcher.service
```

After the unit is back:

```sh
journalctl --list-boots
journalctl -b -1 -o short-precise | tail -150
journalctl -b 0 -o short-precise | head -150
journalctl -b -1 -u user@0.service -u svp-launcher.service \
    -u weston.service --no-pager | tail -150
systemd-analyze
systemd-analyze blame | head -30
systemd-analyze critical-chain
dmesg | grep -iE 'fsck|ext4|mmc|error|timeout'
```

If the journal shows only one boot, it is volatile and the previous boot is
gone. Persistent journal is optional and changes the HU:

```sh
mkdir -p /var/log/journal
systemctl restart systemd-journald
```

(`/var` is bind-mounted from userdata on this firmware, so the journal survives
a reboot once it is persistent.)

### Reading the shutdown boundary

Look at the last timestamps of the previous boot:

```sh
journalctl -b -1 | grep -E \
    'Stopping|Stopped|Timed out|Killing|Reached target|Shutting down' | tail -120
```

Typical messages:

- `Timed out stopping user@0.service` → the root user manager /
  `lightmediascanner` is blocking shutdown.
- `Timed out stopping svp-launcher.service` → the forking SVP launcher did not
  stop; systemd killed it after the default timeout.
- `Killing ...` followed by a long gap → systemd is escalating to SIGKILL.
- `EXT4-fs ... recovery` / `fsck` → unclean shutdown or storage problem.

### While the screen is black

If SSH still answers during the black screen:

```sh
systemctl list-jobs
systemd-analyze critical-chain
journalctl -f
```

`systemctl list-jobs` shows which stop/start jobs are still running. This
separates "UI is late" from "system is still shutting down".

## 5. Interpreting the result

| Finding | Meaning | What to do |
|---|---|---|
| `systemd-analyze blame` shows a large `svp-launcher` or user-manager time | SVP/user startup is the bottleneck | baseline vs Fast Login; check the unit logs |
| `systemd-analyze` kernel time is tens of seconds | kernel/hardware init, not the patcher | check storage, bootloader, watchdog |
| `Timed out stopping user@0.service` | old user manager still runs `lightmediascanner` | install Fast Login, reboot twice |
| `Timed out stopping svp-launcher.service` | forking service cannot be stopped cleanly | firmware/systemd change (outside the patcher) |
| `EXT4-fs recovery` / fsck | unclean shutdown or failing eMMC/SD | avoid power-cycles; check the card |
| ping alive but screen black | OS is up, UI/display is late | check weston/SVP UI startup |
| manual button reboot is also slow | firmware baseline | not a patcher problem |
| first reboot slow, second fast | Fast Login mask took effect | expected |

## 6. Safe test procedure

1. **Baseline:** reboot with the button instruction, measure with ping and the
   journal. Do not use the patcher.
2. **Patcher-requested reboot:** apply a change with the patcher, then use the
   same button instruction and measure the same way.
3. **Fast Login:** if it is not installed, install it, then reboot **twice**.
   Measure the first and the second reboot separately.
4. Keep the ignition/ACC on for the whole test.
5. Do not power-cycle the unit while the patcher is waiting for root: an
   unclean stop can trigger fsck on the next boot and make the next
   measurement worse.

## 7. Patcher-side notes

- The patcher never issues a reboot command. It prints the button instruction
  and waits for the boot id to change and root to return (up to 600 s).
  Wait for the UI and verify manually before assuming a failure.
- The wait is a detection window, not a limit on the HU's boot time.
- A reboot with the button instruction is the same clean systemd shutdown
  measured here; it is also the baseline for comparison.

## 8. What to include in a bug report

- output of `journalctl --list-boots`;
- last ~150 lines of the previous boot (`journalctl -b -1`);
- `systemd-analyze` and `systemd-analyze blame | head -30`;
- `readlink -f /sbin/reboot` and the three `systemctl show` commands above;
- whether ping and SSH were alive during the black screen;
- whether Fast Login is installed and whether the slow reboot was the first or
  the second one after installing it;
- ignition/ACC state and whether the same delay happens with the manual
  button reboot.

## See also

- [`README.md`](README.md) — patcher usage, bootstrap and patches.
- [`patches/D-fast-login/README.md`](patches/D-fast-login/README.md) — the
  root-only `lightmediascanner` mask.
