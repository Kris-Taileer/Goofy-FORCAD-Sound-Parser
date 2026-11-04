import logging
import math
import shutil
import struct
import subprocess
import tempfile
import tomllib
import wave
from contextlib import ExitStack
from pathlib import Path
from random import choice
from threading import Condition, Event, Thread
from time import monotonic

from .events import EVENT_LABELS


LOG = logging.getLogger(__name__)
TONES = {
    "service_down": (410, 285, 180),
    "service_up": (390, 560, 780),
    "service_degraded": (470, 290, 470),
    "flag_got": (690, 960),
    "flag_lost": (660, 345),
    "rank_up": (440, 540, 660, 880),
    "rank_down": (650, 525, 430, 335),
}
MAX_SOUND_TIME = 20
AUDIO_EXTENSIONS = {".aac", ".aif", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"}
SoundSettings = dict[str, tuple[Path, ...] | None]


def clips_in(directory: Path) -> tuple[Path, ...]:
    return tuple(sorted(
        path for path in directory.iterdir()
        if not path.name.startswith(".") and path.suffix.lower() in AUDIO_EXTENSIONS and path.is_file()
    ))


def sound_settings(config: Path | None) -> SoundSettings:
    table = {}
    if config is None:
        sounds_dir = Path.cwd() / "sounds"
        if not sounds_dir.is_dir():
            sounds_dir = Path(__file__).resolve().parents[1] / "sounds"
    else:
        sounds_dir = config.parent.resolve() / "sounds"
        with config.open("rb") as file:
            table = tomllib.load(file).get("sounds", {})
    if not isinstance(table, dict):
        raise ValueError("В конфиге нужна секция [sounds].")

    for kind in table:
        if kind not in EVENT_LABELS:
            raise ValueError(f"Неизвестное событие в [sounds]: {kind}")

    paths: SoundSettings = {}
    for kind in EVENT_LABELS:
        if kind not in table:
            directory = sounds_dir / kind
            if directory.is_dir() and (clips := clips_in(directory)):
                paths[kind] = clips
            continue

        value = table[kind]
        if value is False:
            paths[kind] = None
        elif isinstance(value, str) and value.strip():
            path = (config.parent / Path(value).expanduser()).resolve()
            if path.is_file():
                paths[kind] = (path,)
            elif path.is_dir():
                if clips := clips_in(path):
                    paths[kind] = clips
            else:
                raise ValueError(f"Не найден файл или папка со звуками для {kind}: {path}")
        else:
            raise ValueError(f"Для {kind} нужен путь к файлу, папке или false.")
    return paths


def write_tone(path: Path, kind: str) -> None:
    rate = 24000
    note_size = int(0.11 * rate)
    fade_size = int(0.012 * rate)
    buffer = bytearray()
    for frequency in TONES[kind]:
        for i in range(note_size):
            envelope = min(1, i / fade_size, (note_size - 1 - i) / fade_size)
            sample = 6500 * envelope * math.sin(math.tau * frequency * i / rate)
            buffer.extend(struct.pack("<h", int(sample)))
        buffer.extend(bytes(int(0.025 * rate) * 2))
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(buffer)


class Audio:
    """Play event types independently, keeping at most one player per type."""

    def __init__(self, settings: SoundSettings, *, muted: bool = False):
        self.paths = settings.copy()
        self._last_played: dict[str, Path] = {}
        self.muted = muted
        self._resources = ExitStack()
        self._stop = Event()
        self._condition = Condition()
        self._pending: set[str] = set()
        self._workers: dict[str, Thread] = {}

    def __enter__(self):
        if self.muted or all(kind in self.paths and self.paths[kind] is None for kind in EVENT_LABELS):
            return self
        self.player = shutil.which("ffplay")
        if not self.player:
            raise ValueError("Не найден ffplay. Установи FFmpeg с ffplay или запусти с --dry-run.")
        folder = Path(self._resources.enter_context(tempfile.TemporaryDirectory(prefix="siren-")))
        try:
            for kind in EVENT_LABELS.keys() - self.paths.keys():
                path = folder / f"{kind}.wav"
                write_tone(path, kind)
                self.paths[kind] = (path,)
        except BaseException:
            self._resources.close()
            raise
        return self

    def __exit__(self, *_):
        with self._condition:
            self._stop.set()
            self._condition.notify_all()
        for worker in self._workers.values():
            worker.join()
        self._resources.close()

    def play(self, kind: str, *, superseded=None) -> None:
        paths = self.paths.get(kind)
        if self.muted or not paths:
            return
        previous = self._last_played.get(kind)
        path = choice([clip for clip in paths if clip != previous]) if len(paths) > 1 else paths[0]
        proc = subprocess.Popen(
            [self.player, "-hide_banner", "-loglevel", "error", "-nostats", "-nodisp", "-autoexit",
             "-t", str(MAX_SOUND_TIME), "-i", str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self._last_played[kind] = path
        deadline = monotonic() + MAX_SOUND_TIME + 2
        try:
            while not self._stop.is_set() and monotonic() < deadline:
                if superseded is not None and superseded():
                    return
                try:
                    _, errors = proc.communicate(timeout=0.05 if superseded is not None else 0.2)
                except subprocess.TimeoutExpired:
                    continue
                if proc.returncode or errors:
                    detail = errors.decode(errors="replace").strip()
                    raise OSError(detail or f"ffplay завершился с кодом {proc.returncode}")
                return
            if not self._stop.is_set():
                raise OSError("ffplay не завершился вовремя.")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()

    def notify(self, kind: str) -> None:
        if self.muted or self.paths.get(kind) is None:
            return
        with self._condition:
            self._pending.add(kind)
            if kind not in self._workers:
                worker = Thread(target=self._work, args=(kind,), name=f"siren-{kind}", daemon=True)
                self._workers[kind] = worker
                worker.start()
            self._condition.notify_all()

    def _work(self, kind: str) -> None:
        def superseded() -> bool:
            with self._condition:
                return kind in self._pending

        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._stop.is_set() or kind in self._pending)
                if self._stop.is_set():
                    return
                self._pending.remove(kind)
            try:
                self.play(kind, superseded=superseded)
            except OSError as exc:
                LOG.warning("Не удалось проиграть %s: %s", kind, exc)
