# SIREN · ForcAD Sound Parser

SIREN plays sound alerts when a team’s service status, flag counts, or rank changes in an Attack/Defense CTF. It supports ForcAD, ForcAD ASCIS, legacy HackerDom, and the [adctf-scoreboard-parser API](https://github.com/teebow1e/adctf-scoreboard-parser). Run it on the Linux machine that should play the sounds.

## Quick start

Install Git, Python 3.11 or later, and FFmpeg with `ffplay` on your `PATH`. For JavaScript scoreboards, you also need Chromium and the `browser` extra shown below.

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/Kris-Taileer/Goofy-FORCAD-Sound-Parser.git
cd Goofy-FORCAD-Sound-Parser
python3 -m venv "$HOME/.local/share/siren/venv"
source "$HOME/.local/share/siren/venv/bin/activate"
python -m pip install -e '.[browser]'
```

Use system Chromium if it is installed. Otherwise, run `python -m playwright install chromium`. On Debian or Ubuntu, add `--with-deps` if Chromium needs system libraries. For HTML scoreboards and the parser API, `python -m pip install -e .` is enough.

Replace the example URL and team name with yours. Run these commands from the project directory.

```bash
siren sound flag_got
siren watch http://10.10.0.1:8080/ --team 'Red Cadets' --once
siren watch http://10.10.0.1:8080/ --team 'Red Cadets'
```

The first command tests audio. The second checks the connection and team without playing a sound. The third starts monitoring. Stop it with **Ctrl+C**.

## Monitoring options

`--team` accepts the team’s exact name, IP, or ID as shown on the scoreboard. Use `--type` if format detection fails.

| Option | Effect |
| --- | --- |
| `--dry-run` | Log changes without playing sounds. |
| `--type forcad\|ascis\|hackerdom\|parser-api` | Select the scoreboard format explicitly. With no URL path, HackerDom uses `/board` and the parser API uses `/api/info`. |
| `--interval 1` | Set the polling delay to one second. The default is two seconds. |
| `--browser-executable /path/to/chromium` | Use a specific Chromium executable. |
| `--config siren.local.toml` | Load custom sound settings. |

SIREN uses the first snapshot as a silent baseline. When ForcAD delivers updates over WebSocket, SIREN reads the scoreboard as it changes. Other boards are polled. Alerts depend on when the scoreboard publishes the change.

## Sounds

Put clips in `sounds/<event>/`. SIREN chooses a clip at random and avoids repeating the last one when alternatives exist. Empty folders use built-in tones. Different event types can play at the same time.

| Folder | Plays when |
| --- | --- |
| `service_down` | A service changes to `DOWN`. |
| `service_up` | A service recovers to `UP`. |
| `service_degraded` | A service changes to `MUMBLE`, `CORRUPT`, or `CHECK FAILED`. |
| `flag_got` | The team gains flags. |
| `flag_lost` | The team loses flags. |
| `rank_up` | The team moves up the scoreboard. |
| `rank_down` | The team moves down the scoreboard. |

To override an event, copy [siren.example.toml](siren.example.toml) to `siren.local.toml` and pass `--config siren.local.toml`. Each entry can point to a file or directory. Use `false` to mute the event. Relative paths resolve from the configuration file’s directory.

## Local ForcAD lab

The lab requires Git, [uv](https://docs.astral.sh/uv/), PostgreSQL (`initdb`, `postgres`, `pg_isready`), and Valkey (`valkey-server`, `valkey-cli`) on your `PATH`.

```bash
LAB=$(mktemp -d /tmp/siren-forcad.XXXXXX)
./tools/forcad-lab/setup "$LAB"
printf '%s\n' "$LAB"
"$LAB/start"
```

Leave the lab running. In a second terminal, activate the SIREN environment and run `siren watch http://127.0.0.1:18080/ --team 'SIREN Lab'`. In a third terminal, open the lab directory printed above and trigger one event at a time.

```bash
./labctl status down
./labctl status up
./labctl flag got
./labctl flag lost
```

Wait for each change to appear before triggering the next one. The lab uses 10-second rounds. If a port is already in use, set `SIREN_LAB_HTTP_PORT` or `SIREN_LAB_VALKEY_PORT` before running setup.

Run the test suite with `python -m unittest discover -s tests -v`. To include the browser test, set `SIREN_TEST_CHROMIUM` to the path to Chromium.

[MIT License](LICENSE)
