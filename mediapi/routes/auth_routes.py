from flask import Blueprint, redirect, render_template, request, session, url_for

from ..auth import check_credentials, is_authenticated

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if is_authenticated():
            return redirect(url_for("pages.index"))
        return render_template("login.html", error=None)

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if check_credentials(username, password):
        session.permanent = True
        session["authenticated"] = True
        return redirect(url_for("pages.index"))

    return render_template("login.html", error="Invalid username or password"), 401


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
