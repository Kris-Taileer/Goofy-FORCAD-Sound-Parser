import argparse
import logging
from collections import defaultdict
from http.client import HTTPException
from math import isfinite
from pathlib import Path
from time import sleep
from urllib.parse import urlsplit, urlunsplit

from .audio import Audio, sound_settings
from .board import BoardError, source
from .events import EVENT_LABELS, Event, Service, Snapshot, Tracker


LOG = logging.getLogger("siren")


def positive(value: str) -> float:
    number = float(value)
    if not isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Укажи конечное число больше нуля.")
    return number


def port_number(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Порт должен быть в диапазоне 1–65535.")
    return port


def board_url(address: str, port: int | None, kind: str) -> str:
    if "://" not in address:
        address = "http://" + address
    parsed = urlsplit(address)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise ValueError("Укажи HTTP(S) URL или IP. IPv6-адрес заключи в квадратные скобки.")
    existing_port = parsed.port
    if existing_port == 0:
        raise ValueError("Порт должен быть в диапазоне 1–65535.")
    netloc = parsed.netloc
    if port is not None:
        host = parsed.hostname
        netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    default_paths = {"hackerdom": "/board", "parser-api": "/api/info"}
    path = parsed.path or default_paths.get(kind, "/")
    return urlunsplit(parsed._replace(netloc=netloc, path=path))


def show_events(events: list[Event], audio: Audio, *, sync: bool = False) -> None:
    groups = defaultdict(list)
    for event in events:
        groups[event.kind].append(event.detail)
    for kind, details in groups.items():
        LOG.info("%s — %s", EVENT_LABELS[kind], "; ".join(details))
        if sync:
            audio.play(kind)
        else:
            audio.notify(kind)


def watch(args: argparse.Namespace, audio: Audio) -> None:
    url = board_url(args.board, args.port, args.type)
    tracker = Tracker()
    delay = min(args.interval, 1.0)
    last_error = None
    LOG.info("Слежу за %s · команда %s · режим %s", url, args.team, args.type)
    with source(args.type, url, args.team, args.timeout, args.browser_executable,
                interval=args.interval) as read:
        while True:
            try:
                current = read()
                if current is None:
                    continue
                previous = tracker.previous
                if previous is None:
                    LOG.info("Вижу %s · раунд %s · место %s · сервисы: %s",
                             current.team, current.round, current.rank or "—", ", ".join(current.services))
                if last_error is not None:
                    LOG.info("Данные борды снова доступны.")
                show_events(tracker.observe(current), audio)
                last_error = None
                delay = min(args.interval, 1.0)
                if args.once:
                    return
            except (BoardError, OSError, HTTPException) as exc:
                if args.once:
                    raise
                message = str(exc)
                if message != last_error:
                    LOG.warning("Не удалось прочитать борду: %s Повторю через %.1f с.", message, delay)
                last_error = message
                sleep(delay)
                delay = min(delay * 2, 30)


def demo(audio: Audio) -> None:
    tracker = Tracker()
    snapshots = [
        Snapshot("Demo", 42, 4, {"web": Service("UP", 8, 1), "vault": Service("UP", 1, 0)}),
        Snapshot("Demo", 42, 5, {"web": Service("DOWN", 8, 3), "vault": Service("MUMBLE", 1, 0)}),
        Snapshot("Demo", 43, 3, {"web": Service("UP", 11, 3), "vault": Service("UP", 1, 0)}),
    ]
    for snapshot in snapshots:
        show_events(tracker.observe(snapshot), audio, sync=True)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(prog="siren", description="Звуковые события с A/D CTF борды.")
    commands = cli.add_subparsers(dest="command", required=True)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--config", type=Path, help="Путь к TOML с настройками звуков.")
    shared.add_argument("--dry-run", action="store_true", help="Только события в терминале, без звука.")

    monitor = commands.add_parser("watch", parents=[shared], help="Следить за бордой.")
    monitor.add_argument("board", help="IP или HTTP(S) URL борды, можно с портом и путём.")
    monitor.add_argument("--port", type=port_number, help="Порт, если задаётся отдельно.")
    monitor.add_argument("--team", required=True, help="Точное имя команды, её IP или ID с борды.")
    monitor.add_argument("--type", choices=("auto", "forcad", "ascis", "hackerdom", "parser-api"),
                         default="auto", help="Формат борды (по умолчанию: auto).")
    monitor.add_argument("--interval", type=positive, default=2.0, help="Пауза между опросами без WebSocket (2 с).")
    monitor.add_argument("--timeout", type=positive, default=10.0, help="Тайм-аут сетевых операций в секундах (10).")
    monitor.add_argument("--browser-executable", help="Путь к системному Chromium вместо версии Playwright.")
    monitor.add_argument("--once", action="store_true", help="Проверить адрес, формат и команду одним опросом.")

    commands.add_parser("demo", parents=[shared], help="Сымитировать события без борды.")
    sound = commands.add_parser("sound", parents=[shared], help="Проверить один звук.")
    sound.add_argument("event", choices=EVENT_LABELS)
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        with Audio(sound_settings(args.config), muted=args.dry_run or getattr(args, "once", False)) as audio:
            if args.command == "watch":
                watch(args, audio)
            elif args.command == "demo":
                demo(audio)
            else:
                LOG.info("%s (%s)", EVENT_LABELS[args.event], args.event)
                audio.play(args.event)
    except KeyboardInterrupt:
        LOG.info("SIREN остановлен.")
        return 130
    except (BoardError, ValueError, OSError, HTTPException) as exc:
        LOG.error("%s", exc)
        return 1
    return 0
