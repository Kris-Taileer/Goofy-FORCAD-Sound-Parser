"""Serve the upstream board and APIs at one localhost origin."""
import sys
from pathlib import Path

from flask import Flask, send_from_directory

import lab_bootstrap
from services.api import views as client_views
from services.events.app import app as events_app

receiver_dir = Path(__file__).parent / "ForcAD/backend/services/http_receiver"
sys.path.insert(0, str(receiver_dir))
from services.http_receiver import views as receiver_views

dist = Path(__file__).parent / "ForcAD/front/dist"
app = Flask("forcad_lab")
app.register_blueprint(client_views.client_bp, url_prefix="/api/client/")
app.register_blueprint(receiver_views.receiver_bp, url_prefix="/flags")


@app.get("/")
@app.get("/<path:asset>")
def frontend(asset="index.html"):
    return send_from_directory(dist, asset if (dist / asset).is_file() else "index.html")


def application(environ, start_response):
    if environ["PATH_INFO"].startswith(("/socket.io/", "/api/events/")):
        return events_app(environ, start_response)
    return app(environ, start_response)
