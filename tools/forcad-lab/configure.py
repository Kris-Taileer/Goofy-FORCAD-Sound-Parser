import os
import secrets
import shlex
import sys
from datetime import datetime
from pathlib import Path

import yaml


def port(variable, default):
    value = int(os.environ.get(variable, default))
    if not 1024 <= value <= 65535:
        raise ValueError(f"{variable}: expected a port between 1024 and 65535")
    return value


root = Path.cwd()
http_port = port("SIREN_LAB_HTTP_PORT", 18080)
valkey_port = port("SIREN_LAB_VALKEY_PORT", 16379)
if http_port == valkey_port:
    raise ValueError("ForcAD and Valkey need different ports")
password = secrets.token_hex(24)
env = {
    "POSTGRES_HOST": root / "sockets",
    "POSTGRES_PORT": 15432,
    "POSTGRES_USER": "forcad",
    "POSTGRES_PASSWORD": "unused-unix-socket",
    "POSTGRES_DB": "postgres",
    "REDIS_HOST": "127.0.0.1",
    "REDIS_PORT": valkey_port,
    "REDIS_PASSWORD": password,
    "CONFIG_PATH": root / "config.yml",
    "PYTHONPATH": f"{root}:{root / 'ForcAD/backend'}:{root / 'ForcAD/backend/services'}",
    "EVENTLET_NO_GREENDNS": "yes",
    "PYTHONUNBUFFERED": "1",
    "LAB_BOARD_URL": f"http://127.0.0.1:{http_port}",
    "LAB_WEB_BIND": f"127.0.0.1:{http_port}",
    "LAB_VALKEY_SERVER": sys.argv[1],
    "LAB_VALKEY_CLI": sys.argv[2],
}
(root / "lab.env").write_text("".join(f"export {key}={shlex.quote(str(value))}\n" for key, value in env.items()))
(root / "valkey.conf").write_text(
    f'bind 127.0.0.1\nport {valkey_port}\nrequirepass {password}\ndir "{root / "redis"}"\nappendonly yes\n'
)
config = {
    "game": {
        "mode": "classic", "start_time": datetime(2020, 1, 1), "timezone": "UTC", "round_time": 10,
        "flag_lifetime": 8, "default_score": 2500, "game_hardness": 10.0, "inflation": True,
        "volga_attacks_mode": False, "checkers_path": str(root / "checkers"),
        "env_path": str(root / ".venv/bin"),
    },
    "tasks": [{
        "name": "beacon", "checker": "beacon", "checker_timeout": 4,
        "checker_type": "hackerdom", "gets": 1, "puts": 1, "places": 1,
    }],
    "teams": [
        {"name": "SIREN Lab", "ip": "127.0.0.2"},
        {"name": "Sparring Team", "ip": "127.0.0.3"},
    ],
}
(root / "config.yml").write_text(yaml.safe_dump(config))
