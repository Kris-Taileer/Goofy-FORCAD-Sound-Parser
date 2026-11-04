from dataclasses import dataclass


SERVICE_STATES = frozenset({"UP", "DOWN", "CORRUPT", "MUMBLE", "CHECK FAILED"})
EVENT_LABELS = {
    "service_down": "Сервис упал",
    "service_up": "Сервис восстановился",
    "service_degraded": "Ошибка сервиса или проверки",
    "flag_got": "Флаги получены",
    "flag_lost": "Флаги потеряны",
    "rank_up": "Команда поднялась",
    "rank_down": "Команда опустилась",
}


@dataclass(frozen=True)
class Service:
    status: str | None
    got: int
    lost: int


@dataclass(frozen=True)
class Snapshot:
    team: str
    round: int
    rank: int | None
    services: dict[str, Service]


@dataclass(frozen=True)
class Event:
    kind: str
    detail: str


class Tracker:
    """Compare complete observations, with the first one as a silent baseline."""

    def __init__(self):
        self.previous: Snapshot | None = None
        self._statuses: dict[str, str] = {}

    def observe(self, current: Snapshot) -> list[Event]:
        previous = self.previous
        if previous is not None and current.round < previous.round:
            return []

        events = []
        statuses = {}
        for name, now in current.services.items():
            before = self._statuses.get(name)
            if now.status is not None:
                statuses[name] = now.status
                if before is not None and before != now.status:
                    kind = {
                        "UP": "service_up",
                        "DOWN": "service_down",
                    }.get(now.status, "service_degraded")
                    events.append(Event(kind, f"{name}: {before} → {now.status}"))
            elif before is not None:
                statuses[name] = before

            old = previous.services.get(name) if previous else None
            if old is not None:
                for kind, count in (("flag_got", now.got - old.got),
                                    ("flag_lost", now.lost - old.lost)):
                    if count > 0:
                        events.append(Event(kind, f"{name}: +{count}"))

        if previous and previous.rank is not None and current.rank is not None:
            if previous.rank != current.rank:
                kind = "rank_up" if current.rank < previous.rank else "rank_down"
                events.append(Event(kind, f"#{previous.rank} → #{current.rank}"))

        self.previous = current
        self._statuses = statuses
        return events
