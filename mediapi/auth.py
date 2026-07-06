import hmac

from flask import current_app, redirect, request, session, url_for

EXEMPT_ENDPOINTS = {"auth.login", "static"}


def check_credentials(username, password):
    cfg = current_app.config
    return hmac.compare_digest(username, cfg["USERNAME"]) and hmac.compare_digest(
        password, cfg["PASSWORD"]
    )


def is_authenticated():
    return bool(session.get("authenticated"))


def register_auth_gate(app):
    @app.before_request
    def _require_login():
        if request.endpoint in EXEMPT_ENDPOINTS or request.endpoint is None:
            return None
        if not is_authenticated():
            return redirect(url_for("auth.login"))
        return None
