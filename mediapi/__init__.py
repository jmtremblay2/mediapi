from flask import Flask

from .auth import register_auth_gate
from .config import Config
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

    app.player = PlayerStateManager(app.config["MPV_SOCKET"], app.config["VIDEO_EXTENSIONS"])
    app.player.start()

    return app
