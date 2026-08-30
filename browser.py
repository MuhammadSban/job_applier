"""Two browser surfaces: a real Chrome for live job sites, an embedded pane for local HTML."""

import atexit
import os
import re
import threading
import tkinter as tk

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chrome_profile")

_driver = None
_lock = threading.Lock()


def _alive(driver):
    try:
        driver.current_url
        return True
    except Exception:
        return False


def load_browser(headless=False, profile_dir=PROFILE_DIR):
    global _driver
    with _lock:
        if _driver is not None and _alive(_driver):
            return _driver
        _driver = None

        from selenium import webdriver

        opts = webdriver.ChromeOptions()
        opts.add_argument(f"--user-data-dir={profile_dir}")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        if headless:
            opts.add_argument("--headless=new")

        _driver = webdriver.Chrome(options=opts)
        atexit.register(close_browser)
        return _driver


def close_browser():
    global _driver
    with _lock:
        if _driver is None:
            return
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


class _PlainPreview(tk.Text):
    """Fallback used when tkinterweb has no Tkhtml build for this Tk (e.g. Tk 9)."""

    def load_html(self, html):
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.insert("1.0", re.sub(r"<[^>]+>", "", html).strip())
        self.configure(state="disabled")

    def load_url(self, url):
        self.load_html(f"Embedded preview unavailable. Open externally: {url}")


def load_preview(parent):
    try:
        from tkinterweb import HtmlFrame

        widget = HtmlFrame(parent, messages_enabled=False)
    except Exception:
        for child in parent.winfo_children():
            child.destroy()
        widget = _PlainPreview(parent, wrap="word", relief="flat", borderwidth=0)
    widget.pack(fill="both", expand=True)
    return widget
