import os
import runpy
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv/bin/python"
PROCESSES = []


def start(name, *command):
    with (ROOT / "logs" / f"{name}.log").open("ab") as output:
        process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=output, start_new_session=True)
    PROCESSES.append((name, process))


def check_children():
    for name, process in PROCESSES:
        if process.poll() is not None:
            raise RuntimeError(f"{name} stopped ({process.returncode}). See logs/{name}.log")


def wait_ready(command):
    for _ in range(80):
        check_children()
        if subprocess.run(command, capture_output=True).returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {command[0]}")


def run():
    try:
        start("postgres", "postgres", "-D", ROOT / "pgdata", "-h", "",
              "-k", ROOT / "sockets", "-p", os.environ["POSTGRES_PORT"])
        start("valkey", os.environ["LAB_VALKEY_SERVER"], ROOT / "valkey.conf")
        wait_ready(["pg_isready", "-U", "forcad", "-h", ROOT / "sockets", "-p", os.environ["POSTGRES_PORT"]])
        os.environ["REDISCLI_AUTH"] = os.environ["REDIS_PASSWORD"]
        wait_ready([os.environ["LAB_VALKEY_CLI"], "-p", os.environ["REDIS_PORT"], "ping"])
        if not (ROOT / "initialized").exists():
            with (ROOT / "logs/init.log").open("wb") as output:
                subprocess.run([PYTHON, __file__, "init"], check=True, stdout=output, stderr=output)
            (ROOT / "initialized").touch()
        start("web", PYTHON, "-m", "gunicorn", "-b", os.environ["LAB_WEB_BIND"],
              "-k", "eventlet", "--graceful-timeout", "2", "--access-logfile", "-", "lab_web:application")
        start("worker", PYTHON, __file__, "worker")
        start("ticker", PYTHON, __file__, "ticker")
        while True:
            check_children()
            time.sleep(1)
    finally:
        for name, process in reversed(PROCESSES):
            if process.poll() is None:
                process.send_signal(signal.SIGINT if name == "postgres" else signal.SIGTERM)
        for _, process in reversed(PROCESSES):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "run":
        def stop(*_):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        try:
            run()
        except KeyboardInterrupt:
            pass
    else:
        import lab_bootstrap

        if command == "init":
            runpy.run_path(str(ROOT / "ForcAD/backend/scripts/init_db.py"), run_name="__main__")
        elif command == "ticker":
            runpy.run_module("services.ticker", run_name="__main__")
        elif command == "worker":
            from tasks.app import celery_app

            celery_app.worker_main(["worker", "--loglevel=INFO", "--concurrency=2"])
