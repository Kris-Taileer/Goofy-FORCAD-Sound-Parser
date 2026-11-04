"""Drive real checker verdicts and flag submissions for the ForcAD lab."""
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATUS = {"up": 101, "down": 104, "mumble": 103, "corrupt": 102, "check-failed": 110}


def checker_status(status):
    path = ROOT / "checkers/127.0.0.2.status"
    temporary = path.with_suffix(".new")
    temporary.write_text(str(STATUS[status]))
    temporary.replace(path)
    print(f"beacon → {status.upper()} on the next checker cycle")


def submit_flag(direction):
    from lib import storage

    attacker, victim = (1, 2) if direction == "got" else (2, 1)
    with storage.utils.db_cursor() as (_, cursor):
        cursor.execute(
            """SELECT f.id, f.flag, t.token
               FROM flags f CROSS JOIN teams t CROSS JOIN gameconfig g
               WHERE f.team_id = %s AND t.id = %s
                 AND f.round > g.real_round - g.flag_lifetime
                 AND NOT EXISTS (
                     SELECT 1 FROM stolenflags s
                     WHERE s.flag_id = f.id AND s.attacker_id = t.id
                 )
               ORDER BY f.round DESC, f.id DESC LIMIT 1""",
            (victim, attacker),
        )
        row = cursor.fetchone()
    if row is None:
        raise SystemExit("No fresh flag yet. Wait for the next checker cycle.")
    flag_id, flag, token = row
    request = Request(
        f"{os.environ['LAB_BOARD_URL']}/flags/",
        data=json.dumps([flag]).encode(),
        headers={"Content-Type": "application/json", "X-Team-Token": token},
        method="PUT",
    )
    with urlopen(request, timeout=5) as response:
        reply = json.load(response)
    with storage.utils.db_cursor() as (_, cursor):
        cursor.execute("SELECT 1 FROM stolenflags WHERE flag_id = %s AND attacker_id = %s",
                       (flag_id, attacker))
        accepted = cursor.fetchone() is not None
    if not accepted:
        messages = [entry["msg"].split("] ", 1)[-1] for entry in reply]
        raise SystemExit(f"ForcAD rejected the submission: {messages}")
    print(f"ForcAD accepted one flag ({direction} for SIREN Lab).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("value", choices=STATUS)
    flag = commands.add_parser("flag")
    flag.add_argument("value", choices=("got", "lost"))
    args = parser.parse_args()
    {"status": checker_status, "flag": submit_flag}[args.command](args.value)
