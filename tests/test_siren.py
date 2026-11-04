import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from threading import Event as ThreadEvent, Thread
from unittest.mock import patch

from siren.audio import Audio, sound_settings
from siren.board import BoardError, parse_parser_api, source
from siren.cli import board_url
from siren.events import Event, Service, Snapshot, Tracker


def snapshot(round_number, *, status="UP", got=10, lost=3, rank=4):
    return Snapshot("My Team", round_number, rank, {"web": Service(status, got, lost)})


def hackerdom(status=101, got=10, lost=3, rank=4):
    return f"""
        <div id="round">Round: 17</div>
        <table>
        <tr class="team"><th class="service_name">web app</th></tr>
        <tr class="team">
          <td class="place">{rank}(+1)</td>
          <td><div class="team_name">Моя команда</div><div class="team_server">10.0.0.2</div></td>
          <td class="team_service other status-{status}">
            <div class="flags"><div class="param_value">+{got} / -{lost}</div></div>
          </td>
        </tr>
        </table>
    """.encode()


@contextmanager
def serve(respond, *, content_type="text/html; charset=utf-8"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code, body = respond(self.path)
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


class TrackerTests(unittest.TestCase):
    def test_changes_in_same_round_and_recovery(self):
        tracker = Tracker()
        self.assertEqual(tracker.observe(snapshot(5)), [])
        changed = snapshot(5, status="DOWN", lost=5, rank=8)
        self.assertEqual(tracker.observe(changed), [
            Event("service_down", "web: UP → DOWN"),
            Event("flag_lost", "web: +2"),
            Event("rank_down", "#4 → #8"),
        ])
        self.assertEqual(tracker.observe(changed), [])
        self.assertEqual(tracker.observe(snapshot(6, got=12, lost=5, rank=6)), [
            Event("service_up", "web: DOWN → UP"),
            Event("flag_got", "web: +2"),
            Event("rank_up", "#8 → #6"),
        ])

    def test_stale_round_and_unknown_status_do_not_trigger_alerts(self):
        tracker = Tracker()
        tracker.observe(snapshot(5, status="DOWN"))
        self.assertEqual(tracker.observe(snapshot(4, got=99)), [])
        self.assertEqual(tracker.observe(snapshot(5, status=None)), [])
        self.assertEqual(tracker.observe(snapshot(5)), [Event("service_up", "web: DOWN → UP")])

    def test_counter_reset_and_service_reordering(self):
        tracker = Tracker()
        tracker.observe(Snapshot("My Team", 5, 4, {
            "web": Service("UP", 10, 3),
            "vault": Service("UP", 18, 2),
        }))
        self.assertEqual(tracker.observe(Snapshot("My Team", 5, 4, {
            "vault": Service("UP", 18, 2),
            "web": Service("UP", 0, 3),
            "new service": Service("DOWN", 7, 1),
        })), [])
        self.assertEqual(tracker.observe(snapshot(5, got=2)), [Event("flag_got", "web: +2")])


class AdapterTests(unittest.TestCase):
    def test_upstream_api_selects_team_by_id_and_reads_latest_snapshot(self):
        raw = [
            {"round": 100, "teams_info": []},
            {"round": 101, "teams_info": [
                {"name": "Other Team", "ip": "1", "place": 1, "services": []},
                {"name": "My Team", "ip": "2", "place": 3, "services": [
                    {"name": "web", "status": "status-102", "flags": {"got": 12, "lost": 4}},
                ]},
            ]},
        ]
        body = json.dumps(raw).encode()
        with serve(lambda _: (200, body), content_type="application/json") as port:
            url = board_url("127.0.0.1", port, "parser-api")
            with source("parser-api", url, "2", 2) as read:
                self.assertEqual(read(), snapshot(101, status="CORRUPT", got=12, lost=4, rank=3))
        with self.assertRaisesRegex(BoardError, "не найдена"):
            parse_parser_api(body, "3")

    def test_hackerdom_reads_round_status_and_flags_over_http(self):
        body = hackerdom().decode().encode("windows-1251")

        def respond(path):
            return (200, body) if path == "/board" else (404, b"")

        with serve(respond, content_type="text/html; charset=windows-1251") as port:
            url = board_url("127.0.0.1", port, "hackerdom")
            with source("hackerdom", url, "10.0.0.2", 2) as read:
                self.assertEqual(read(), Snapshot("Моя команда", 17, 4, {"web app": Service("UP", 10, 3)}))
            with source("auto", board_url("127.0.0.1", port, "auto"), "Моя команда", 2) as read:
                self.assertEqual(read().services["web app"], Service("UP", 10, 3))

    def test_forcad_static_markup_preserves_service_names_and_flags(self):
        html = b"""
            <div class="tp">Round: 17</div>
            <div class="row">
                <div class="service-name">
                    <div class="service-cell">web</div><div class="service-cell">vault 2</div>
                </div>
            </div>
            <div class="row">
                <div class="team-group">
                    <div class="number">4</div>
                    <div class="team"><div class="team-name">My Team</div><div class="ip">10.0.0.2</div></div>
                    <div class="score">100.0</div>
                </div>
                <div class="service">
                    <div class="service-cell" style="color: rgb(255, 91, 91); background-color: rgb(125, 252, 116)">
                        <div class="flags">+10/-3</div>
                    </div>
                    <div class="service-cell" style="background-color: #5191ff">
                        <div class="flags">+1/-5</div>
                    </div>
                </div>
            </div>
        """
        with serve(lambda path: (200, html)) as port:
            with source("auto", f"http://127.0.0.1:{port}/", "My Team", 2) as read:
                self.assertEqual(read(), Snapshot("My Team", 17, 4, {
                    "web": Service("UP", 10, 3), "vault 2": Service("CORRUPT", 1, 5),
                }))

    @unittest.skipUnless(os.environ.get("SIREN_TEST_CHROMIUM"), "Set SIREN_TEST_CHROMIUM to run browser test")
    def test_javascript_ascis_uses_computed_status_color_and_medal_rank(self):
        html = b"""
            <!doctype html><style>
                .red { background-color: rgba(252, 41, 71, .8) }
            </style><body><div id="app"></div><script>
                setTimeout(() => app.innerHTML = `
                    <div>Round: 17</div>
                    <div class="row"><div class="service-name"><div class="service-cell">web</div></div></div>
                    <div class="row">
                        <div class="number"><img src="gold-medal.svg"></div>
                        <div>My Team</div><div>106</div><div>123.40</div>
                        <div class="service-cell"><div class="status-circle red"></div>
                            <div>SLA: 50%</div><div>FP: 15</div><div>+10 / -3</div>
                        </div>
                    </div>`, 75);
            </script></body>
        """
        with serve(lambda path: (200, html)) as port:
            with source("ascis", f"http://127.0.0.1:{port}/", "106", 5,
                        os.environ["SIREN_TEST_CHROMIUM"]) as read:
                self.assertEqual(read(), snapshot(17, status="DOWN", rank=1))


class CLITests(unittest.TestCase):
    def test_url_and_port(self):
        self.assertEqual(board_url("https://host:123/scores?q=1", 8443, "auto"),
                         "https://host:8443/scores?q=1")
        self.assertEqual(board_url("[2001:db8::5]", 8080, "parser-api"),
                         "http://[2001:db8::5]:8080/api/info")
        with self.assertRaises(ValueError):
            board_url("ftp://host", None, "auto")

    def test_custom_sound_paths_are_relative_to_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "siren.toml"
            clip = config.parent / "clip.wav"
            clip.touch()
            config.write_text('[sounds]\nflag_got = "clip.wav"\nrank_up = false\n')
            self.assertEqual(sound_settings(config), {"flag_got": (clip,), "rank_up": None})
            clip.unlink()
            with self.assertRaisesRegex(ValueError, "Не найден файл"):
                sound_settings(config)

    def test_sound_pool_uses_event_folder_and_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "siren.toml"
            config.write_text('[sounds]\nrank_up = "other-clips"\nrank_down = false\n')
            default_pool = root / "sounds" / "flag_got"
            default_pool.mkdir(parents=True)
            first = default_pool / "first.MP3"
            second = default_pool / "second.wav"
            for path in (first, second, default_pool / ".hidden.mp3", default_pool / "readme.txt"):
                path.touch()
            (root / "sounds" / "rank_down").mkdir()
            (root / "sounds" / "rank_down" / "disabled.wav").touch()
            (root / "other-clips").mkdir()
            custom_clip = root / "other-clips" / "custom.ogg"
            custom_clip.touch()
            settings = sound_settings(config)
            self.assertEqual(settings, {
                "flag_got": (first, second), "rank_up": (custom_clip,), "rank_down": None,
            })

            with patch("siren.audio.shutil.which", return_value="ffplay"), patch("siren.audio.subprocess.Popen") as player:
                player.return_value.communicate.return_value = (None, b"")
                player.return_value.returncode = player.return_value.poll.return_value = 0
                with Audio(settings) as audio:
                    for _ in range(3):
                        audio.play("flag_got")
                played = [Path(call.args[0][-1]) for call in player.call_args_list]
            self.assertEqual(set(played), {first, second})
            self.assertNotEqual(played[0], played[1])
            self.assertNotEqual(played[1], played[2])

    def test_audio_starts_other_events_and_replaces_repeated_event(self):
        started = Queue()

        class PlayingClip:
            def __init__(self, command):
                self.path = Path(command[-1])
                self.returncode = None
                self.finished = ThreadEvent()
                started.put(self)

            def communicate(self, timeout=None):
                if not self.finished.wait(timeout):
                    raise subprocess.TimeoutExpired("ffplay", timeout)
                return None, b""

            def poll(self):
                return self.returncode

            def kill(self):
                self.returncode = -9
                self.finished.set()

        with patch("siren.audio.shutil.which", return_value="ffplay"), patch(
            "siren.audio.subprocess.Popen", side_effect=lambda command, **_: PlayingClip(command),
        ):
            with Audio({"service_down": (Path("down.wav"),), "flag_got": (Path("flag.wav"),)}) as audio:
                audio.notify("service_down")
                first = started.get(timeout=3)
                self.assertEqual(first.path, Path("down.wav"))
                audio.notify("flag_got")
                second = started.get(timeout=3)
                self.assertEqual(second.path, Path("flag.wav"))
                self.assertFalse(first.finished.is_set())
                audio.notify("service_down")
                replacement = started.get(timeout=3)
                self.assertEqual(replacement.path, first.path)
                self.assertTrue(first.finished.is_set())
                self.assertFalse(second.finished.is_set())

    @unittest.skipIf(os.name == "nt", "SIGINT subprocess test needs POSIX")
    def test_watch_preserves_baseline_during_error_and_ignores_repeats(self):
        visits = 0
        seen_repeats = ThreadEvent()

        def respond(path):
            nonlocal visits
            visits += 1
            if visits == 1:
                return 200, hackerdom()
            if visits == 2:
                return 503, b"Temporary error"
            if visits >= 5:
                seen_repeats.set()
            return 200, hackerdom(status=104, got=13, lost=4)

        with serve(respond) as port:
            with subprocess.Popen(
                [sys.executable, "-m", "siren", "watch", "127.0.0.1", "--port", str(port),
                 "--team", "10.0.0.2", "--type", "hackerdom", "--dry-run", "--interval", "0.1"],
                stderr=subprocess.PIPE, text=True,
            ) as child:
                try:
                    reached = seen_repeats.wait(8)
                    child.send_signal(signal.SIGINT)
                    _, logs = child.communicate(timeout=3)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()
        self.assertTrue(reached, logs)
        self.assertEqual(child.returncode, 130, logs)
        self.assertIn("HTTP Error 503", logs)
        self.assertEqual(logs.count("Сервис упал"), 1, logs)
        self.assertEqual(logs.count("Флаги получены — web app: +3"), 1, logs)
        self.assertEqual(logs.count("Флаги потеряны — web app: +1"), 1, logs)


if __name__ == "__main__":
    unittest.main()
