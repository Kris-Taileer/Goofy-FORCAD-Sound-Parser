import json
import re
import shutil
from collections.abc import Callable, Iterable
from contextlib import ExitStack, contextmanager
from http.client import HTTPException
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .events import SERVICE_STATES, Service, Snapshot


class BoardError(Exception):
    """The scoreboard could not be read as a complete snapshot."""


STATUS_CODES = dict(zip((101, 102, 103, 104, 110),
                        ("UP", "CORRUPT", "MUMBLE", "DOWN", "CHECK FAILED")))
STATUS_CLASSES = {
    "status_up": "UP",
    "status_corrupt": "CORRUPT",
    "status_mumble": "MUMBLE",
    "status_down": "DOWN",
    "status_": "CHECK FAILED",
}
COLORS = {
    (125, 252, 116): "UP", (168, 223, 142): "UP",
    (81, 145, 255): "CORRUPT", (117, 194, 246): "CORRUPT",
    (255, 145, 20): "MUMBLE", (217, 137, 181): "MUMBLE",
    (255, 91, 91): "DOWN", (252, 41, 71): "DOWN",
    (255, 255, 0): "CHECK FAILED", (248, 222, 34): "CHECK FAILED",
}
RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\b", re.I)
BACKGROUND = re.compile(r"(?:^|;)\s*background(?:-color)?\s*:\s*([^;]+)", re.I)
FLAG_PAIR = re.compile(r"(?<![\d+-])\+?(\d+)\s*/\s*-?(\d+)(?!\d)")
ROUND = re.compile(r"\bround\s*:\s*(\d+)", re.I)
RESPONSE_LIMIT = 64 * 1024 * 1024


def status_of(value: str) -> str | None:
    value = value.strip()
    label = value.upper().replace("_", " ")
    if label in SERVICE_STATES:
        return label
    if value in STATUS_CLASSES:
        return STATUS_CLASSES[value]
    code = value.removeprefix("status-")
    if code.isdecimal():
        return STATUS_CODES.get(int(code))
    rgb = RGB.search(value)
    if rgb:
        return COLORS.get(tuple(map(int, rgb.groups())))
    hex_color = re.search(r"#([0-9a-f]{6})(?![0-9a-f])", value, re.I)
    if hex_color:
        color = hex_color[1]
        return COLORS.get(tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)))
    return None


def select_team(teams: Iterable[tuple[str, str, object]], team: str):
    matches = [(name, row) for name, ip, row in teams if team in (name, ip)]
    if len(matches) != 1:
        problem = "не найдена" if not matches else "найдено несколько совпадений"
        raise BoardError(f"Команда {team!r}: {problem}. Укажи точное имя или IP/ID с борды.")
    return matches[0]


def unique_services(items: Iterable[tuple[str, Service]]) -> dict[str, Service]:
    services = {}
    for name, service in items:
        if not name or name in services:
            raise BoardError(f"Пустое или повторяющееся имя сервиса: {name!r}")
        services[name] = service
    if not services:
        raise BoardError("На борде нет данных сервисов.")
    return services


def nonnegative(value: object) -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise BoardError(f"Ожидалось целое неотрицательное число, получено {value!r}")


def parse_parser_api(raw: bytes, team: str) -> Snapshot:
    """Read the upstream /api/info response or a single full snapshot."""
    try:
        data = json.loads(raw)
        # Upstream appends snapshots in collection order.
        latest = data[-1] if isinstance(data, list) else data
        round_number = nonnegative(latest["round"])
        name, row = select_team(
            ((t["name"], str(t.get("ip", "")), t) for t in latest["teams_info"]), team,
        )
        services = unique_services(
            (s["name"], Service(status_of(str(s["status"])), nonnegative(s["flags"]["got"]),
                                nonnegative(s["flags"]["lost"])))
            for s in row["services"]
        )
        rank = nonnegative(row["place"]) or None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise BoardError("Ответ не похож на снимок /api/info исходного парсера.") from exc
    return Snapshot(name, round_number, rank, services)


def node_text(node, selector: str | None = None) -> str:
    if selector:
        node = node.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""


def parse_rank(text: str, node=None) -> int | None:
    number = re.match(r"^\s*(\d+)\b", text)
    if number:
        return int(number[1]) or None
    if node is not None:
        image = node.select_one("img[src]")
        if image:
            for rank, metal in enumerate(("gold", "silver", "bronze"), 1):
                if f"{metal}-medal" in image["src"]:
                    return rank
    return None


def parse_flags(text: str, *, allow_single: bool = False) -> tuple[int, int]:
    pair = FLAG_PAIR.search(text)
    if pair:
        return int(pair[1]), int(pair[2])
    if allow_single and re.fullmatch(r"\+?\d+", text):
        return int(text), 0
    raise BoardError(f"Не удалось прочитать счётчики флагов: {text[:100]!r}")


def parse_hackerdom(soup: BeautifulSoup, team: str) -> Snapshot:
    number = re.search(r"\d+", node_text(soup, "#round"))
    if not number:
        raise BoardError("Не найден номер раунда HackerDom.")
    names = [node_text(n) for n in soup.select("th.service_name")]
    name, row = select_team(
        ((node_text(r, ".team_name"), node_text(r, ".team_server"), r)
         for r in soup.select("tr.team") if r.select_one(".team_name")), team,
    )
    cells = row.select("td.team_service")
    if len(cells) != len(names):
        raise BoardError("Число ячеек сервисов HackerDom не совпадает с заголовком.")
    services = unique_services(
        (label, Service(
            next((status for cls in cell.get("class", []) if (status := status_of(cls))), None),
            *parse_flags(node_text(cell, ".flags .param_value"), allow_single=True),
        )) for label, cell in zip(names, cells)
    )
    return Snapshot(name, int(number[0]), parse_rank(node_text(row, ".place")), services)


def team_identity(row) -> tuple[str, str]:
    name = node_text(row, ".team-name")
    ip = node_text(row, ".ip")
    if name:
        return name, ip
    # ASCIS variants without named cells expose the same fields as rendered lines.
    lines = row.get("data-siren-lines", "").splitlines()
    lines = [line.strip() for line in lines if line.strip()]
    place = node_text(row, ".number")
    if lines and place and lines[0] == place:
        lines = lines[1:]
    return (lines[0], lines[1]) if len(lines) >= 2 else ("", "")


def parse_forcad(soup: BeautifulSoup, team: str) -> Snapshot:
    number = ROUND.search(node_text(soup))
    if not number:
        raise BoardError("Не найден номер раунда ForcAD/ASCIS.")
    header = soup.select_one(".service-name")
    if not header:
        raise BoardError("Не найден заголовок сервисов ForcAD/ASCIS.")
    names = [node_text(n) for n in header.select(".service-cell")]
    if not names:
        names = header.get("data-siren-lines", "").splitlines()
        names = [name.strip() for name in names if name.strip()]
    name, row = select_team(
        ((*team_identity(r), r) for r in soup.select(".row") if r.select_one(".service-cell")
         and not r.select_one(".service-name")), team,
    )
    cells = row.select(".service-cell")
    if len(cells) != len(names):
        raise BoardError("Число ячеек сервисов ForcAD/ASCIS не совпадает с заголовком.")
    services = []
    for label, cell in zip(names, cells):
        icon = cell.select_one(".status-circle")
        indicator = icon if icon else cell
        backgrounds = BACKGROUND.findall(indicator.get("style", ""))
        status = next((parsed for value in (
            indicator.get("data-siren-color", ""), backgrounds[-1] if backgrounds else "",
            *indicator.get("class", ()),
        ) if (parsed := status_of(value)) is not None), None)
        flags = cell.select_one(".flags")
        got, lost = parse_flags(node_text(flags if flags else cell))
        services.append((label, Service(status, got, lost)))
    place = row.select_one(".number")
    return Snapshot(name, int(number[1]), parse_rank(node_text(place), place), unique_services(services))


def download(url: str, timeout: float) -> tuple[bytes, str | None]:
    request = Request(url, headers={"User-Agent": "SIREN-ADCTF/0.1", "Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(RESPONSE_LIMIT + 1)
            encoding = response.headers.get_content_charset()
    except HTTPError as exc:
        exc.close()
        raise
    if len(raw) > RESPONSE_LIMIT:
        raise BoardError("Ответ борды больше 64 МБ.")
    return raw, encoding


def download_html(url: str, timeout: float) -> BeautifulSoup:
    raw, encoding = download(url, timeout)
    return BeautifulSoup(raw, "html.parser", from_encoding=encoding or "utf-8")


@contextmanager
def source(kind: str, url: str, team: str, timeout: float, browser_executable: str | None = None,
           *, interval: float = 0):
    next_poll = 0.0

    def paced(read):
        nonlocal next_poll
        remaining = next_poll - monotonic()
        if remaining > 0:
            sleep(remaining)
        result = read()
        if result is not None:
            next_poll = monotonic() + interval
        return result

    if kind == "parser-api":
        def read_api():
            raw, _ = download(url, timeout)
            return parse_parser_api(raw, team)

        yield lambda: paced(read_api)
    elif kind == "hackerdom":
        yield lambda: paced(lambda: parse_hackerdom(download_html(url, timeout), team))
    else:
        with ExitStack() as resources:
            browser_read: Callable[[], Snapshot | None] | None = None
            hacker_url: str | None = None
            location = urlsplit(url)
            probe_url = (
                urlunsplit(location._replace(path="/board"))
                if kind == "auto" and location.path in ("", "/") and not location.fragment
                else None
            )

            def read_http():
                nonlocal hacker_url
                if hacker_url:
                    return parse_hackerdom(download_html(hacker_url, timeout), team)
                # Some forks serve the full scoreboard in the initial response.
                try:
                    html = download_html(url, timeout)
                except HTTPError as exc:
                    if exc.code != 404 or probe_url is None:
                        raise
                    html = download_html(probe_url, timeout)
                    if not html.select_one("th.service_name"):
                        raise
                    hacker_url = probe_url
                    return parse_hackerdom(html, team)
                if kind == "auto" and html.select_one("th.service_name"):
                    hacker_url = url
                    return parse_hackerdom(html, team)
                if html.select_one(".service-name") and html.select_one(".row .team-name"):
                    return parse_forcad(html, team)
                if probe_url and not html.select_one(".service-name"):
                    try:
                        probe = download_html(probe_url, timeout)
                    except (BoardError, OSError, HTTPException):
                        pass
                    else:
                        if probe.select_one("th.service_name"):
                            hacker_url = probe_url
                            return parse_hackerdom(probe, team)
                return None

            def read():
                nonlocal browser_read
                if browser_read is None:
                    snapshot = paced(read_http)
                    if snapshot is not None:
                        return snapshot
                    browser_read = resources.enter_context(
                        rendered_source(url, team, timeout, browser_executable, interval=interval)
                    )
                try:
                    return browser_read()
                except BoardError:
                    resources.close()
                    browser_read = None
                    raise

            yield read


@contextmanager
def rendered_source(url: str, team: str, timeout: float, executable: str | None, *, interval: float = 0):
    try:
        from playwright.sync_api import Error, TimeoutError as BrowserTimeout, WebSocket, sync_playwright
    except ImportError as exc:
        raise BoardError("Для JS-борды установи .[browser] и выполни playwright install chromium.") from exc

    with sync_playwright() as playwright:
        if executable is None and not Path(playwright.chromium.executable_path).is_file():
            executable = shutil.which("chromium") or shutil.which("chromium-browser")
        try:
            browser = playwright.chromium.launch(
                executable_path=executable, chromium_sandbox=True, timeout=timeout * 1000,
            )
        except Error as exc:
            raise BoardError(f"Chromium не запустился. Проверь playwright install chromium: {str(exc).splitlines()[0]}") from exc
        try:
            page = browser.new_page()
            page.set_default_timeout(timeout * 1000)
            live_socket: WebSocket | None = None
            next_reload = 0.0

            def on_socket(socket: WebSocket) -> None:
                def on_frame(payload: str | bytes) -> None:
                    nonlocal live_socket
                    if isinstance(payload, str) and payload.startswith((
                        '42/game_events,["init_scoreboard",',
                        '42/game_events,["update_scoreboard",',
                    )):
                        live_socket = socket

                socket.on("framereceived", on_frame)

            page.on("websocket", on_socket)

            def read():
                nonlocal next_reload
                try:
                    if live_socket is not None:
                        try:
                            page.wait_for_function(
                                "() => window.__sirenBoard?.changed", timeout=500,
                            ).dispose()
                        except BrowserTimeout:
                            if live_socket.is_closed():
                                raise BoardError("Соединение ForcAD с бордой закрылось.")
                            return None
                    else:
                        remaining = next_reload - monotonic()
                        if remaining > 0:
                            sleep(remaining)
                        response = page.goto(url, wait_until="domcontentloaded")
                        if response is not None and not response.ok:
                            raise BoardError(f"Борда ответила HTTP {response.status}.")
                        page.wait_for_function(r"""() =>
                            /\bround\s*:\s*\d+/i.test(document.body.innerText) &&
                            document.querySelector('.service-name') &&
                            [...document.querySelectorAll('.row .service-cell')].some(
                                cell => !cell.closest('.service-name') &&
                                    /\d+\s*\/\s*-?\d+/.test(cell.innerText))
                        """).dispose()
                    html = page.evaluate("""() => {
                        if (!window.__sirenBoard) {
                            const header = document.querySelector('.service-name');
                            const table = header?.closest('.table') ?? document.body;
                            const state = window.__sirenBoard = { changed: false };
                            new MutationObserver(records => {
                                if (!table.isConnected || records.some(record => table.contains(record.target))) {
                                    state.changed = true;
                                }
                            }).observe(document.body, {
                                subtree: true, childList: true, characterData: true,
                                attributes: true, attributeFilter: ['style', 'class', 'src'],
                            });
                        }
                        if (window.__sirenBoard) window.__sirenBoard.changed = false;
                        const selectors = '.row, .service-name, .service-cell, .status-circle';
                        const clone = document.documentElement.cloneNode(true);
                        const copies = clone.querySelectorAll(selectors);
                        document.querySelectorAll(selectors).forEach((element, i) => {
                            if (element.matches('.row, .service-name')) {
                                copies[i].dataset.sirenLines = element.innerText;
                            } else {
                                copies[i].dataset.sirenColor = getComputedStyle(element).backgroundColor;
                            }
                        });
                        return clone.outerHTML;
                    }""")
                except Error as exc:
                    raise BoardError(f"Не удалось прочитать JS-борду: {str(exc).splitlines()[0]}") from exc
                snapshot = parse_forcad(BeautifulSoup(html, "html.parser"), team)
                next_reload = monotonic() + interval
                return snapshot

            yield read
        finally:
            browser.close()
