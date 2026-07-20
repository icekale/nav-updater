from pathlib import Path


def test_login_inputs_share_the_panel_width() -> None:
    css = Path("app/static/app.css").read_text()
    template = Path("app/templates/login.html").read_text()

    assert ".login-panel input {" in css
    assert ".login-panel input { width: 100%; }" in css
    assert '<input type="text" name="username"' in template
