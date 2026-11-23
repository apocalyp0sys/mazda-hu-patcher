# Root password for UART (recovery)

How to set **your own root password** on a Mazda head unit (KBST-41-47-M5 /
DS04L) so you can log in locally over UART `ttyS0` when SSH or the network is
unavailable.

> You need **root** already (usually obtained through this patcher's `media`
> bootstrap).
> The password is changed only in `/etc/shadow`; the root key is not touched.
> Step 4 separately changes only the `PermitRootLogin` policy (root over SSH
> by key).

---

## 1. Why

The UART console (`ttyS0`, `921600 8N1`) is already enabled:

- `serial-getty@ttyS0.service` is present in `getty.target.wants`;
- `/etc/securetty` contains `ttyS0`;
- `/etc/pam.d/login` uses `pam_unix` through `common-auth`;
- `/` is mounted `rw` and `/etc/shadow` is writable;
- `/etc/login.defs` sets `ENCRYPT_METHOD SHA512`.

So after setting a password you can connect to UART and log in as `root` even
if SSH or the network is down.

**Limitation:** this works only once the system has reached userspace/systemd
and started getty. If the HU is stuck in the bootloader (no kernel/rootfs),
the UART password will not help — you need a different recovery path
(fastboot/eMMC).

---

## 2. Preparation

Connect over root SSH and **do not close this session** until UART is verified:

```sh
ssh -i <root-key> root@<HU>
```

`<root-key>` is your private root key. The repository does not ship a private
key.

If `/` happens to be mounted read-only:

```sh
mount -o remount,rw / 2>/dev/null || true
```

Make backups:

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a /etc/shadow "/etc/shadow.bak-$STAMP"
cp -a /etc/ssh/sshd_config "/etc/ssh/sshd_config.bak-$STAMP"
ls -l /etc/shadow /etc/shadow.bak-* /etc/ssh/sshd_config.bak-*
```

Check the initial state:

```sh
awk -F: '$1=="root" {print substr($2,1,20)}' /etc/shadow
passwd -S root
```

---

## 3. Set a new root password

Generate a 32-character hex password (128 bits) and set it with `chpasswd`:

```sh
PW=$(openssl rand -hex 16)
printf 'root:%s\n' "$PW" | chpasswd
printf 'Root password: %s\n' "$PW"
```

The password is shown **once** — save it in a password manager (or another
offline location) immediately. Do not do this in a terminal session that logs
input; clear the scrollback after saving. `chpasswd`:

- does not require the old password (the command runs as root);
- hashes the new password according to `ENCRYPT_METHOD` (`SHA512` → `$6$...`);
- writes the hash to `/etc/shadow`;
- does not change the shell/home/UID or SSH keys.

If `chpasswd` reports `read-only file system`:

```sh
mount -o remount,rw /
```

Verify:

```sh
awk -F: '$1=="root" {print substr($2,1,3)}' /etc/shadow   # expect $6$
passwd -S root                                            # password set
ls -l /etc/shadow                                         # 0400 root
```

### Where to store the password

Store the password **outside the HU** (password manager / paper safe).
Optionally, keep a root-only copy on the device:

```sh
printf '%s\n' "$PW" > /home/root/root-password.txt
chmod 600 /home/root/root-password.txt
unset PW
```

But if root access is lost, this file cannot be read. An offline copy is
therefore mandatory. If you do not need the file on the HU, just run
`unset PW` after saving the password offline.

> Do not use a short or well-known password. Do not commit it to a repository.
> Do not pass it on the command-line `argv` — use stdin only, as shown above.

---

## 4. Keep the root password for UART only

To prevent the root password from being used over SSH while UART/getty keeps
working, allow root login **by key only**:

```sh
sed -i 's/^PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
sshd -t
```

- `sshd -t` must finish without errors.
- `prohibit-password` allows **public key** authentication but forbids
  password and keyboard-interactive authentication for root over SSH.
- UART uses `/bin/login` + PAM, so the root password on `ttyS0` keeps working.
- New SSH connections read the config; if a persistent `sshd.service` is
  running, you can restart it:
  ```sh
  systemctl try-restart sshd.service 2>/dev/null || true
  ```

Verify:

```sh
sshd -T 2>/dev/null | grep -i '^permitrootlogin'
```

Expected: `permitrootlogin prohibit-password` (older builds may print the
equivalent `without-password`).

**Verify the root key in a separate terminal** without closing the current
session:

```sh
ssh -i <root-key> root@<HU> 'id'
```

It must print `uid=0(root)`. If the key does not work, restore the previous
`sshd_config` and investigate before rebooting.

If the config is broken, restore the most recent backup:

```sh
cp -a "$(ls -t /etc/ssh/sshd_config.bak-* | head -n1)" /etc/ssh/sshd_config
sshd -t
```

> If you really need root password authentication over SSH, keep
> `PermitRootLogin yes`. It is less secure: the password becomes available
> for network brute-force attacks.

---

## 5. Verify UART

Connection settings:

- speed **921600**;
- **8N1**;
- flow control **None**;
- short wires, 3.3 V.

After boot, a login prompt should appear. Log in:

```text
login: root
password: <new password>
```

Verify:

```sh
id
# uid=0(root) gid=0(root) ...
```

If UART login does not work:

```sh
# over root SSH:
systemctl status serial-getty@ttyS0 --no-pager
grep -n '^ttyS0$' /etc/securetty
awk -F: '$1=="root" {print substr($2,1,3)}' /etc/shadow
journalctl -u serial-getty@ttyS0 --no-pager | tail -30
journalctl -u systemd-logind --no-pager | tail -30
```

Common causes:

- wrong baud rate / TX and RX swapped;
- `ttyS0` is missing from `/etc/securetty`;
- getty is not running;
- the password was not written
  (`awk -F: '$1=="root" {print substr($2,1,3)}' /etc/shadow` → not `$6$`);
- UART input is corrupted (check 3.3 V and wire length).

---

## 6. Rollback

Restore the original shadow and sshd_config from the most recent backups:

```sh
cp -a "$(ls -t /etc/shadow.bak-* | head -n1)" /etc/shadow
cp -a "$(ls -t /etc/ssh/sshd_config.bak-* | head -n1)" /etc/ssh/sshd_config
sshd -t
```

Or simply set a different password (without putting a literal in shell
history):

```sh
PW=$(openssl rand -hex 16)
printf 'root:%s\n' "$PW" | chpasswd
printf 'Root password: %s\n' "$PW"
# save it offline, then:
unset PW
```

> After restoring `/etc/shadow`, the factory root hash comes back — which you
> most likely do not know. Make sure the root key and/or another recovery
> path remains usable.

---

## 7. Persistence

| Event | Root password |
|---|---|
| Reboot | preserved |
| Factory reset | rootfs is not formatted according to the partition map — it should survive; verify on your device |
| OTA / `update.bin` flash | **reset** (rootfs is overwritten) |
| Manual change of `/etc/shadow` | takes effect immediately |

After an OTA, the root key on userdata usually survives; log in with it and
set the password again.

---

## 8. Security: what this password does NOT close

- **`media` with an empty password** and `PermitEmptyPasswords yes` — anyone
  who can reach SSH logs in as `media`.
- **`/svp` with `0777`** — from `media` you can modify root-executed files and
  gain root.
- Therefore UART with the `media` login (empty password) → `/svp` → root is
  still possible even if the root password is strong.

If the goal is not only recovery but also security:

1. Set a **password** for `media` (for UART/getty) and/or an SSH key for
   `media` (for the network). UART `login` does not support keys; it needs a
   password.
2. Disable `PermitEmptyPasswords` in `sshd_config` — this closes the empty
   password **over SSH**. It does not affect UART/getty: there `pam_unix
   nullok` is used, so an empty `media` password can only be closed by
   changing the `media` password (or editing PAM).
3. If possible, prevent `media` from writing to `/svp` (after verifying that
   SVP/OTA still work).
4. After provisioning, disable SSH passwords (`PasswordAuthentication no`) or
   restrict SSH to the local network.
5. Root — by key only (`PermitRootLogin prohibit-password`); the password —
   for UART only.

---

## 9. Quick reference

```sh
# root SSH
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a /etc/shadow "/etc/shadow.bak-$STAMP"
cp -a /etc/ssh/sshd_config "/etc/ssh/sshd_config.bak-$STAMP"

PW=$(openssl rand -hex 16)
printf 'root:%s\n' "$PW" | chpasswd
printf 'Root password: %s\n' "$PW"
# save the password offline
unset PW

sed -i 's/^PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
sshd -t
sshd -T 2>/dev/null | grep -i '^permitrootlogin'

# UART: 921600 8N1, login root / <password>, id
```

Save the password offline. Keep the root key as the primary network access
method.
