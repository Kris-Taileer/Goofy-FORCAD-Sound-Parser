# SIREN

SIREN следит за командой на Attack/Defense CTF и озвучивает изменения на устройстве, где запущена утилита. Нужны Python 3.11+, доступ к борде и `ffplay` из FFmpeg в `PATH`. Звук идёт через стандартный аудиовыход устройства.

## Установка SIREN с нуля на Linux

1. Установи Python 3.11 или новее, FFmpeg с `ffplay` и, для ForcAD/ASCIS, Chromium.

   На Arch Linux:

   ```bash
   sudo pacman -S --needed python ffmpeg chromium
   ```

   На Debian 12 или Ubuntu 24.04:

   ```bash
   sudo apt update
   sudo apt install python3 python3-venv ffmpeg
   ```

2. Скопируй или распакуй весь проект, включая `sounds/`, и открой папку проекта в терминале.

   ```bash
   python3 --version
   ffplay -version
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -e '.[browser]'
   ```

   Если Chromium уже установлен и есть в `PATH`, этого достаточно. Иначе установи Chromium через Playwright. На Debian/Ubuntu команда с `--with-deps` также поставит системные библиотеки браузера:

   ```bash
   python -m playwright install --with-deps chromium
   ```

   Для HTML-борд и API можно установить только `python -m pip install -e .`.

3. Проверь звук, затем доступ к борде. Подставь её адрес и точное имя, IP или ID своей команды с таблицы.

   ```bash
   siren sound flag_got
   siren watch http://10.10.0.1:8080/ --team 'Red Cadets' --once
   ```

   `--once` должен показать имя команды, раунд и сервисы. Первый снимок всегда тихий. Чтобы оставить монитор запущенным со звуком, повтори команду без `--once`:

   ```bash
   siren watch http://10.10.0.1:8080/ --team 'Red Cadets'
   ```

   В новом терминале снова активируй `.venv` или вызови `.venv/bin/siren`. Остановка — Ctrl+C. Для реального турнира отдельный сервер ForcAD поднимать не нужно.

## Проверка на локальном ForcAD

Если турнирной борды под рукой нет, в проекте есть скрипт, который скачивает конкретную версию ForcAD и создаёт стенд в `/tmp`. Для стенда на Linux нужны Git, `uv`, PostgreSQL (команды `initdb`, `postgres`, `pg_isready`) и Valkey (`valkey-server`, `valkey-cli`). На Arch можно установить пакеты командой `sudo pacman -S --needed git uv postgresql valkey`. На Debian/Ubuntu при необходимости добавь каталог с бинарниками PostgreSQL: `export PATH="$(pg_config --bindir):$PATH"`.

Из папки проекта запусти:

```bash
LAB=$(mktemp -d /tmp/siren-forcad.XXXXXX)
./tools/forcad-lab/setup "$LAB"
echo "$LAB"
"$LAB/start"
```

Оставь этот терминал открытым. Борда будет на [127.0.0.1:18080](http://127.0.0.1:18080/). Если там уже работает другой стенд, укажи свободные порты при настройке: `SIREN_LAB_HTTP_PORT=18081 SIREN_LAB_VALKEY_PORT=16380 ./tools/forcad-lab/setup "$LAB"`.

Во втором терминале, из папки проекта, запусти SIREN:

```bash
.venv/bin/siren watch http://127.0.0.1:18080/ --team 'SIREN Lab'
```

В третьем терминале перейди в каталог стенда, который напечатала команда `echo "$LAB"`. Вызывай события по одному:

```bash
./labctl status down
./labctl status up
./labctl status mumble
./labctl status up
./labctl flag got
./labctl flag lost
```

После смены статуса дождись уведомления перед следующей командой. В тестовой игре один раунд длится 10 секунд, вердикт обычно появляется на борде через 10–20 секунд. Для отправки флага дождись, пока чекер положит его в сервис, обычно это первый раунд. Ctrl+C в терминале стенда завершает его процессы. Для повторного запуска достаточно `./start` из каталога стенда. Логи лежат там же в `logs/`.

## Адрес борды и задержка

Можно передать IP, DNS-имя или полный HTTP(S) URL с путём. Порт указывается в URL или через `--port` (если заданы оба, используется `--port`). Если схемы нет, используется HTTP. Точное имя команды, IP или ID бери с борды. Для IPv6 используй скобки: `[2001:db8::5]`.

По умолчанию SIREN ищет HTML борды, затем пробует отрисовать JavaScript через Chromium. Поддержаны форматы ForcAD, ForcAD ASCIS и старый HackerDom. Если передан только адрес, SIREN проверит корневую страницу и при необходимости попробует `/board` для HackerDom. Для нестандартного адреса укажи полный путь. С `--type hackerdom` без пути SIREN сразу откроет `/board`.

Если уже работает [adctf-scoreboard-parser](https://github.com/teebow1e/adctf-scoreboard-parser), SIREN может читать его снимки. Передавай адрес самого парсера; без пути используется `/api/info`:

```bash
siren watch http://127.0.0.1:8081 --type parser-api --team 'Red Cadets'
```

С ForcAD через WebSocket SIREN держит страницу открытой и читает таблицу сразу после обновления. Для HTML, API и JavaScript-борд без такого WebSocket остаётся опрос с паузой 2 секунды. `--interval` меняет только паузу опроса, например `--interval 1`. Путь к системному Chromium можно указать через `--browser-executable`.

Звук возможен после публикации события бордой. При опросе краткая смена статуса между снимками может остаться незамеченной. При временном сбое SIREN пробует подключиться снова и сравнивает новый снимок с последним удачным. Счётчики флагов накопительные: рост даёт уведомление, уменьшение становится новой точкой отсчёта. При сбросе номера раунда для новой игры перезапусти наблюдение.

## Звуки

| Событие | Когда звучит |
| --- | --- |
| `service_down` | Чекер перевёл сервис в DOWN |
| `service_up` | Сервис вернулся в UP |
| `service_degraded` | Сервис перешёл в MUMBLE, CORRUPT или CHECK FAILED |
| `flag_got` | Команда получила один или больше флагов на сервисе |
| `flag_lost` | Команда потеряла один или больше флагов на сервисе |
| `rank_up` | Команда поднялась в таблице |
| `rank_down` | Команда опустилась в таблице |

В терминале видны сервисы и количество флагов. События одного типа в одном снимке звучат одним сигналом. Разные типы могут звучать одновременно, поэтому длинный клип не задерживает другое событие. Если тот же тип сработал снова, новый клип заменит предыдущий. Максимум — 20 секунд на клип.

Положи несколько клипов в подпапку события, например `sounds/flag_got/` или `sounds/service_down/`. SIREN читает файлы прямо из этих подпапок при запуске. Для каждого срабатывания выбирается случайный клип. Если в папке два файла или больше, один и тот же клип не звучит два раза подряд для этого события. Для пустой папки используется встроенный сигнал. Поддерживаются, например, MP3, WAV, OGG, FLAC и M4A.

При запуске из папки проекта конфиг для этой схемы не нужен. С `--config` SIREN ищет `sounds` рядом с TOML. В секции `[sounds]` можно явно выбрать другую папку или один файл. Относительные пути считаются от папки конфига. Значение `false` отключает звук, сохраняя событие в терминале.

```toml
[sounds]
service_down = "sounds/service_down"
flag_got = "sounds/flag_got/plus_flag.mp3"
rank_down = false
```

```bash
siren sound service_down
siren demo
siren watch http://10.10.0.1:8080/ --team 'Red Cadets' --dry-run
siren watch 10.10.0.1:8080 --team 'Red Cadets' --config siren.local.toml
```

Тесты: `python -m unittest discover -s tests -v`.
