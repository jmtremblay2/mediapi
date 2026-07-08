from flask import Flask

from .auth import register_auth_gate
from .config import Config
from .kodi import KodiClient
from .player import PlayerStateManager
from .routes.api_routes import bp as api_bp
from .routes.auth_routes import bp as auth_bp
from .routes.pages import bp as pages_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)

    register_auth_gate(app)

    kodi = KodiClient(
        app.config["KODI_URL"],
        username=app.config["KODI_USER"],
        password=app.config["KODI_PASSWORD"],
    )
    app.player = PlayerStateManager(kodi, app.config["VIDEO_EXTENSIONS"])
    app.player.start()

    return app
