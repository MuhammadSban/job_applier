"""A signed-in LinkedIn session, borrowed from a real browser window.

LinkedIn serves a guest nothing useful. A logged-out job page carries no apply URL at all —
not an offsite link, not an Easy Apply target, nothing — so scout's LinkedIn source has no
form to read and says so. Signing in is the only way through, and signing in means a
password, usually a second factor and sometimes a captcha. None of that is worth
automating, and none of it should be typed into anything but a real browser.

So the browser is used for exactly one thing: it opens a window, you sign in by hand, and
the cookies it collects are kept here. Everything afterwards is plain HTTP again, which is
how the rest of the app already works — the window is a door, not a dependency.

Selenium is imported inside login() so it stays optional. Without it installed the app runs
exactly as it does now, minus this one button.
"""

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(HERE, ".chrome_profile")  # keeps you signed in between launches
JAR = os.path.join(HERE, ".linkedin_session.json")
LOGIN_URL = "https://www.linkedin.com/login"
COOKIE = "li_at"  # LinkedIn sets this only once a session is genuinely signed in
TIMEOUT = 300
POLL = 1.0
WINDOW = "1100,850"


def login(timeout=TIMEOUT):
    """Open a real Chrome window, wait for the sign-in, keep the cookies. Blocking, so
    callers hand it to run_async. Returns the line to show the user."""
    try:
        from selenium import webdriver
    except ImportError:
        return ("Signing in needs Selenium — pip install selenium — and Chrome. "
                "Everything else in the app works without it.")

    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument(f"--window-size={WINDOW}")  # the default 800x600 crops the form
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    try:
        driver = webdriver.Chrome(options=options)  # no headless flag: this one is seen
    except Exception as exc:
        return f"Could not start Chrome: {str(exc).splitlines()[0]}"

    try:
        driver.get(LOGIN_URL)
        jar = _await_login(driver, timeout)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if not jar:
        return "Not signed in — the window closed, or the sign-in did not finish in time."
    _save(jar)
    return f"Signed in to LinkedIn. {len(jar)} cookies kept; applications can be read now."


def _await_login(driver, timeout):
    """Polls until the session cookie appears. Reading cookies doubles as a check that the
    window is still open: once it is closed the call raises, which ends the wait."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            jar = driver.get_cookies()
        except Exception:
            return None  # window closed
        if any(c.get("name") == COOKIE for c in jar):
            time.sleep(2)  # let the redirect finish writing the rest of the jar
            try:
                return driver.get_cookies()
            except Exception:
                return jar
        time.sleep(POLL)
    return None


def _save(jar):
    kept = [{"name": c["name"], "value": c["value"], "expiry": c.get("expiry")}
            for c in jar if c.get("name") and c.get("value")]
    with open(JAR, "w", encoding="utf-8") as f:
        json.dump(kept, f)
    os.chmod(JAR, 0o600)  # a session cookie is as good as the password


def cookies():
    """The jar as requests wants it, minus anything that has expired."""
    try:
        with open(JAR, encoding="utf-8") as f:
            jar = json.load(f)
    except Exception:
        return {}
    now = time.time()
    return {c["name"]: c["value"] for c in jar
            if not c.get("expiry") or c["expiry"] > now}


def signed_in():
    return COOKIE in cookies()


def forget():
    try:
        os.remove(JAR)
        return "Signed out — the saved LinkedIn session was deleted."
    except FileNotFoundError:
        return "No saved LinkedIn session."
