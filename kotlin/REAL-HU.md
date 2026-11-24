# Stage 1 — проверка на реальном ГУ

Это пошаговый runbook для проверки Kotlin/sshj-слоя на реальном головном
устройстве (ГУ). Сам spike ничего не устанавливает в `/svp`, не меняет патчи,
не запускает reboot и не требует Fast Login. Python bootstrap из раздела 3 —
это отдельный, уже существующий способ подготовить root-ключ.

Проверяем:

- media login по паролю (обычно пустой);
- root login по ключу;
- `id -u` и `/proc/sys/kernel/random/boot_id`;
- SCP upload файлов 49 КБ и 1.2 МБ;
- sha256 загруженных файлов;
- host-key fingerprint и pinning;
- fallback `cat >`, если на ГУ нет удалённого `scp`;
- Android keygen и Android root/SCP.

Основное описание проекта: [README.md](README.md).

---

## 1. Что нужно

### Со стороны ГУ

- ГУ включено и доступно по сети;
- SSH на порту 22 открыт;
- известен IP-адрес ГУ (в примерах ниже `<HU_IP>`, часто `192.168.50.1`);
- уже установлен root-ключ без passphrase, либо есть возможность один раз
  выполнить Python bootstrap (см. раздел 3);
- известен пароль `media`, если он не пустой.

### Со стороны компьютера

- JDK 21;
- Android SDK 35 — только для Android-части;
- репозиторий `patcher`;
- OpenSSH client — желательно, для ручной проверки (`ssh`, `ssh-keygen`);
- доступ к тому же Wi-Fi/сегменту сети, что и ГУ.

### Со стороны Android-устройства

- Android 8+;
- USB debugging для `adb`;
- телефон подключён к тому же Wi-Fi, что и ГУ;
- мобильный интернет лучше временно отключить, чтобы Android не уводил
  трафик к локальному IP через сотовую сеть.

---

## 2. Проверить сеть и SSH-порт

Сначала узнать IP ГУ. Его показывает сам ГУ, роутер, или его можно увидеть при
запуске Python-патчера.

Проверка порта.

Windows PowerShell:

```powershell
Test-NetConnection -ComputerName <HU_IP> -Port 22
```

Linux/macOS:

```sh
nc -vz <HU_IP> 22
```

Ожидается `TcpTestSucceeded : True` или `succeeded`.

Если порт закрыт:

- проверить, что компьютер/телефон действительно в сети ГУ;
- отключить гостевой Wi-Fi и client isolation;
- проверить, что SSH не выключен на ГУ;
- не путать IP ГУ с IP роутера.

`ping` может быть заблокирован — ориентироваться лучше на проверку TCP-порта.

---

## 3. Подготовить root-доступ

Stage 1 предполагает, что root-ключ уже установлен. Если root по ключу уже
работает — переходите к разделу 4.

### 3.1. Если root-ключ уже есть

Проверить можно так:

```sh
ssh -i ~/.ssh/hu_patcher_ed25519 \
    -o IdentitiesOnly=yes \
    -o ConnectTimeout=8 \
    root@<HU_IP> 'id -u'
```

Ожидается:

```text
0
```

Если команда идёт ~25 секунд — это нормально для ГУ без Fast Login. Это не
означает, что ключ неправильный.

Если ключ был создан Kotlin-командой `keygen` (PKCS#8), OpenSSH `ssh` может
его не прочитать. В этом случае проверяйте root самим spike, а для Python
bootstrap используйте OpenSSH-ключ из раздела 3.2.

### 3.2. Если root-ключа нет

Используйте существующий Python bootstrap. Stage 1 его не заменяет.

Сгенерировать OpenSSH-ключ:

```sh
ssh-keygen -t ed25519 -N "" -C hu-patcher -f ~/.ssh/hu_patcher_ed25519
```

Если файл уже существует, `ssh-keygen` спросит, перезаписывать ли его. Не
перезаписывайте рабочий ключ без необходимости.

Из корня репозитория `patcher` запустить Python-патчер:

```sh
python3 hu_patcher.py <HU_IP> --key ~/.ssh/hu_patcher_ed25519
```

Патчер:

- проверит media login;
- загрузит bootstrap;
- установит публичный ключ;
- попросит вручную перезагрузить ГУ.

Инструкция для перезагрузки:

```text
To reboot, hold Music, Favorite (Star) and Volume On/Off
buttons for 5 seconds, then select 'End Diag Mode'.
```

После перезагрузки дождаться, пока root SSH снова начнёт отвечать, и
проверить:

```sh
ssh -i ~/.ssh/hu_patcher_ed25519 \
    -o IdentitiesOnly=yes \
    -o ConnectTimeout=8 \
    root@<HU_IP> 'id -u'
```

Должно быть `0`.

### 3.3. Важно про формат ключа

- `ssh-keygen` и Python bootstrap создают **OpenSSH**-ключ.
- `./gradlew :spike:run --args="keygen ..."` создаёт приватный ключ в
  **PKCS#8** (это удобно для sshj и Android).
- `spike` умеет работать с обоими форматами.
- Текущий Python bootstrap не умеет читать PKCS#8 Ed25519. Не передавайте ему
  ключ, созданный Kotlin-командой `keygen`.

Для реального ГУ проще всего:

1. взять уже работающий root-ключ, или
2. создать OpenSSH-ключ через `ssh-keygen` и установить его Python bootstrap'ом.

---

## 4. Подготовить рабочее место

Все команды ниже выполняются из каталога `patcher/kotlin`.

```sh
cd patcher/kotlin
```

Проверить сборку:

```sh
./gradlew :core:allTests :spike:compileKotlin
```

Windows:

```powershell
.\gradlew.bat :core:allTests :spike:compileKotlin
```

### 4.1. Проверить keygen отдельно (необязательно)

Это не устанавливает ключ на ГУ, только проверяет генерацию:

```sh
./gradlew :spike:run --args="keygen --out /tmp/hu-key-test --name id_ed25519 --overwrite"
```

Windows:

```powershell
.\gradlew.bat :spike:run --args="keygen --out $env:TEMP\hu-key-test --name id_ed25519 --overwrite"
```

Ожидается строка вида:

```text
public line : ssh-ed25519 AAAA... hu-patcher
fingerprint : SHA256:...
```

Этот ключ не надо устанавливать на ГУ в рамках Stage 1.

### 4.2. Подготовить тестовые файлы

Файл 49 КБ уже есть в репозитории:

```text
../patches/C-callfix/svp-callfix
```

Файл 1.2 МБ можно сгенерировать. Размер — 1 216 784 байта.

Linux/macOS:

```sh
head -c 1216784 /dev/urandom > /tmp/hu-spike-1.2m.bin
ls -l /tmp/hu-spike-1.2m.bin
```

Windows PowerShell:

```powershell
$path = Join-Path $env:TEMP "hu-spike-1.2m.bin"
$bytes = New-Object byte[] 1216784
(New-Object Random 42).NextBytes($bytes)
[IO.File]::WriteAllBytes($path, $bytes)
Get-Item $path | Select-Object FullName, Length
```

Можно использовать и настоящий сгенерированный payload:

```text
../patches/B-touch-while-driving/svp.svc.engineering.patched
```

Но он появляется только после `sh patches/build.sh`, для которого нужен
`firmware/update.bin`. Для проверки транспорта достаточно файла на 1.2 МБ.

---

## 5. Desktop: запуск spike

### 5.1. Первый запуск без pinning

Linux/macOS:

```sh
./gradlew :spike:run --args="run \
  --host <HU_IP> \
  --root-key ~/.ssh/hu_patcher_ed25519 \
  --upload ../patches/C-callfix/svp-callfix \
  --upload /tmp/hu-spike-1.2m.bin \
  --upload-mode scp"
```

Windows PowerShell:

```powershell
.\gradlew.bat :spike:run --args="run --host <HU_IP> --root-key ~/.ssh/hu_patcher_ed25519 --upload ..\patches\C-callfix\svp-callfix --upload $env:TEMP\hu-spike-1.2m.bin --upload-mode scp"
```

Если пароль `media` не пустой, добавить:

```text
--media-password "пароль"
```

Если пароль пустой, параметр не нужен — это значение по умолчанию.

Если нужно проверить только root, добавить `--skip-media`. Но для полного
Stage 1 media тоже нужно проверить.

### 5.2. Что должно быть в выводе

Пример успешного запуска:

```text
[media] host key ... SHA256:...
[media] connect OK in ...
[media] id -> uid=...
[media] upload svp-callfix 47.9 KiB via scp sha256 OK in ...
[media] upload hu-spike-1.2m.bin 1.2 MiB via scp sha256 OK in ...
[root] host key ... SHA256:...
[root] connect OK in ...
[root] id -u -> 0
[root] boot_id -> ...
[root] upload svp-callfix 47.9 KiB via scp sha256 OK in ...
[root] upload hu-spike-1.2m.bin 1.2 MiB via scp sha256 OK in ...

SPIKE: OK
```

Признаки успеха:

- есть `SPIKE: OK`;
- `[root] id -u -> 0`;
- `boot_id` непустой;
- для обоих файлов написано `sha256 OK`;
- нет строк `ERROR:`.

### 5.3. Записать host fingerprint и включить pinning

При первом запуске в логе будет:

```text
host key <HU_IP>:22 ... SHA256:...
```

Скопировать fingerprint и выполнить второй запуск:

```sh
./gradlew :spike:run --args="run \
  --host <HU_IP> \
  --root-key ~/.ssh/hu_patcher_ed25519 \
  --upload ../patches/C-callfix/svp-callfix \
  --upload /tmp/hu-spike-1.2m.bin \
  --upload-mode scp \
  --fingerprint SHA256:..."
```

Ожидается `(pinned)` и `SPIKE: OK`.

Для проверки отказа можно один раз запустить с заведомо неверным
fingerprint: должна появиться строка `host key mismatch`, а итог должен быть
`SPIKE: FAILED`. После этого вернуть правильный fingerprint.

Если fingerprint не совпал — не отключать проверку «на всякий случай».
Сначала выяснить, почему host key изменился.

### 5.4. Проверить fallback `cat >`

Отдельный запуск:

```sh
./gradlew :spike:run --args="run \
  --host <HU_IP> \
  --root-key ~/.ssh/hu_patcher_ed25519 \
  --upload ../patches/C-callfix/svp-callfix \
  --upload-mode cat"
```

Ожидается:

```text
[media] upload ... via cat sha256 OK
[root] upload ... via cat sha256 OK
SPIKE: OK
```

`auto` — режим по умолчанию: сначала пробует SCP, при ошибке переходит на
`cat >`. В логе будет видно, какой путь использовался.

### 5.5. Если SSH-сессия долгая

Без Fast Login один успешный SSH-сеанс может занимать около 25 секунд.
Это не ошибка ключа.

При необходимости увеличить таймауты:

```text
--connect-timeout-ms 15000
--command-timeout-ms 180000
--upload-timeout-ms 600000
```

### 5.6. Сохранить лог

Linux/macOS:

```sh
./gradlew :spike:run --args="..." 2>&1 | tee hu-stage1-desktop.log
```

Windows PowerShell:

```powershell
.\gradlew.bat :spike:run --args="..." 2>&1 |
  Tee-Object -FilePath hu-stage1-desktop.log
```

Лог не содержит приватный ключ и пароль, но в истории команд может остаться
сама команда. Пароль в историю лучше не писать.

---

## 6. Android: проверка на телефоне

Android-тест проверяет:

1. генерацию Ed25519-ключа на Android;
2. root login по ключу, `id -u`, `boot_id`;
3. SCP upload файлов 49 КБ и 1.2 МБ и sha256 (если `huTestUpload=1`).

Media-аккаунт на Android отдельно не проверяется — это делает desktop spike.

### 6.1. Собрать APK и test APK

```sh
./gradlew :androidApp:assembleDebug :androidApp:assembleDebugAndroidTest
```

Windows:

```powershell
.\gradlew.bat :androidApp:assembleDebug :androidApp:assembleDebugAndroidTest
```

Файлы:

```text
androidApp/build/outputs/apk/debug/androidApp-debug.apk
androidApp/build/outputs/apk/androidTest/debug/androidApp-debug-androidTest.apk
```

### 6.2. Подключить телефон и установить APK

```sh
adb devices
adb install -r androidApp/build/outputs/apk/debug/androidApp-debug.apk
adb install -r androidApp/build/outputs/apk/androidTest/debug/androidApp-debug-androidTest.apk
```

Телефон должен быть в том же Wi-Fi, что и ГУ.

Проверить доступность ГУ с телефона:

```sh
adb shell ping -c 3 <HU_IP>
```

Если `ping` заблокирован, это ещё не значит, что SSH недоступен. Но если
телефон не в сети ГУ, тест не пройдёт.

### 6.3. Передать приватный ключ в тест

Ключ передаётся в base64. Используйте тот же ключ, которым root реально
работает на ГУ.

Linux:

```sh
KEY=$(base64 -w0 "$HOME/.ssh/hu_patcher_ed25519")
```

macOS:

```sh
KEY=$(base64 < "$HOME/.ssh/hu_patcher_ed25519" | tr -d '\n')
```

Windows PowerShell:

```powershell
$key = [Convert]::ToBase64String(
  [IO.File]::ReadAllBytes("$HOME\.ssh\hu_patcher_ed25519"))
```

### 6.4. Запустить instrumentation test

Полный тест:

```sh
adb shell am instrument -w \
  -e huHost <HU_IP> \
  -e huPort 22 \
  -e huRootKeyPemBase64 "$KEY" \
  -e huTestUpload 1 \
  -e huRemoteDir /tmp/hu-patcher-spike/android \
  com.hu.patcher.test/androidx.test.runner.AndroidJUnitRunner
```

Windows PowerShell:

```powershell
adb shell am instrument -w `
  -e huHost <HU_IP> `
  -e huPort 22 `
  -e huRootKeyPemBase64 $key `
  -e huTestUpload 1 `
  -e huRemoteDir /tmp/hu-patcher-spike/android `
  com.hu.patcher.test/androidx.test.runner.AndroidJUnitRunner
```

Ожидается:

```text
OK (2 tests)
```

Если запустить без `huHost` и `huRootKeyPemBase64`, тест соединения будет
пропущен, а keygen-тест всё равно выполнится:

```sh
adb shell am instrument -w \
  com.hu.patcher.test/androidx.test.runner.AndroidJUnitRunner
```

Ожидается, что keygen-тест прошёл, а сетевой тест skipped.

### 6.5. Важно про ключ в командной строке

Команда `am instrument` содержит приватный ключ в base64. Он может попасть в
историю shell и в список процессов. Не публикуйте эту команду и очистите
историю, если это важно для вас.

---

## 7. Если что-то не работает

| Симптом | Что проверить |
|---|---|
| `connection timed out` | IP ГУ, Wi-Fi, firewall, порт 22 |
| `Connection refused` | SSH выключен или неверный порт |
| `authentication failed for root` | Ключ не тот, не установлен, или root не разрешён |
| `authentication failed for media` | Пароль media; передать `--media-password` |
| `SCP upload failed ... scp: not found` | Запустить `--upload-mode cat` и записать результат |
| `host key mismatch` | Неверный fingerprint или подмена; проверить host key на ГУ |
| Долгие SSH-сеансы | Нет Fast Login; это нормально, увеличить timeout |
| Timeout, но ключ не отвергнут | Это не «неверный ключ»; проверить сеть и повторить |
| Android: `INSTRUMENTATION_FAILED` | Установлен ли test APK, совпадает ли package |
| Android: тест пропущен | Не переданы `huHost`/`huRootKeyPemBase64` |
| Android не видит ГУ | Мобильный интернет, VPN, гостевая Wi-Fi-сеть |

Если SCP не работает, это важный результат Stage 1. Нужно записать:

- полный текст ошибки;
- сработал ли `cat` fallback;
- размер файла и sha256;
- версию ГУ и наличие Fast Login.

---

## 8. Что можно и нельзя делать

Можно:

- загружать тестовые файлы в `/tmp/hu-patcher-spike`;
- читать `id`, `boot_id`, `sha256`;
- смотреть host key fingerprint;
- удалить тестовый каталог после проверки:
  ```sh
  ssh -i ~/.ssh/hu_patcher_ed25519 root@<HU_IP> \
    'rm -rf /tmp/hu-patcher-spike'
  ```

Нельзя:

- указывать `--remote-dir` в `/svp`, `/etc`, `/home` или другие системные
  каталоги;
- устанавливать патчи в рамках Stage 1;
- отправлять reboot-команды;
- публиковать приватный ключ или пароль media;
- считать timeout доказательством неверного ключа.

---

## 9. Что прислать по результатам

Для закрытия Stage 1 нужны:

1. Лог desktop spike с `SPIKE: OK` или полный текст ошибки.
2. IP/модель ГУ и версию ПО, если известна.
3. Установлен ли Fast Login.
4. Host key fingerprint.
5. Результат SCP для 49 КБ и 1.2 МБ.
6. Результат `--upload-mode cat`.
7. Вывод Android instrumentation:
   - `OK (2 tests)`, или
   - какой тест упал и на каком шаге.
8. `adb logcat` при падении Android-теста.

Не присылать:

- приватный ключ;
- пароль media;
- base64-строку приватного ключа.

---

## 10. Чек-лист Stage 1

- [ ] Компьютер и телефон в сети ГУ.
- [ ] Порт 22 доступен.
- [ ] Root-ключ установлен и даёт `id -u = 0`.
- [ ] Desktop: media login проходит.
- [ ] Desktop: root login проходит.
- [ ] Desktop: `boot_id` читается.
- [ ] Desktop: 49 КБ загружен, sha256 совпал.
- [ ] Desktop: 1.2 МБ загружен, sha256 совпал.
- [ ] Desktop: `--fingerprint` принимает правильный ключ.
- [ ] Desktop: неправильный fingerprint отклоняется.
- [ ] Desktop: `--upload-mode cat` проходит.
- [ ] Android: keygen-тест проходит.
- [ ] Android: root/SCP-тест проходит.
- [ ] Логи сохранены, ключи и пароли не попали в отчёт.
