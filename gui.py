import threading
import tkinter as tk
from tkinter import ttk
from urllib.parse import quote_plus

from browser import close_browser, load_browser, load_preview

FIELDS = ["Full Name", "Email", "Phone", "Location", "Current Title", "Years Experience"]
SEARCH_URL = "https://www.linkedin.com/jobs/search/?keywords={title}&location={location}"


def disp():
    root = tk.Tk()
    root.title("Job Application Automator")
    root.geometry("1280x720")

    web = tk.Frame(root, bg="white", relief="solid", borderwidth=1)
    web.pack(side="left", fill="both", expand=True, padx=10, pady=10)
    preview = load_preview(web)

    form = tk.Frame(root, width=340)
    form.pack(side="right", fill="y", padx=(0, 10), pady=10)
    form.pack_propagate(False)

    entries = {}
    for label in FIELDS:
        tk.Label(form, text=label, anchor="w").pack(fill="x", pady=(8, 2))
        entry = ttk.Entry(form)
        entry.pack(fill="x")
        entries[label] = entry

    tk.Label(form, text="Cover Letter", anchor="w").pack(fill="x", pady=(8, 2))
    cover = tk.Text(form, height=10, wrap="word", relief="solid", borderwidth=1)
    cover.pack(fill="both", expand=True)

    status = tk.Label(form, text="", anchor="w", fg="gray")

    def set_status(text):
        root.after(0, lambda: status.configure(text=text))

    def open_browser():
        url = SEARCH_URL.format(
            title=quote_plus(entries["Current Title"].get()),
            location=quote_plus(entries["Location"].get()),
        )
        set_status("Launching Chrome...")

        def work():
            try:
                load_browser().get(url)
                set_status("Chrome ready.")
            except Exception as exc:
                set_status(f"Browser failed: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def show_preview():
        body = cover.get("1.0", "end").strip().replace("\n", "<br>")
        name = entries["Full Name"].get()
        preview.load_html(f"<html><body style='font:14px sans-serif;padding:32px'>"
                          f"<h2>{name}</h2><p>{body}</p></body></html>")

    ttk.Button(form, text="Open Browser", command=open_browser).pack(fill="x", pady=(10, 0))
    ttk.Button(form, text="Preview Letter", command=show_preview).pack(fill="x", pady=(6, 0))
    ttk.Button(form, text="Submit").pack(fill="x", pady=(6, 0))
    status.pack(fill="x", pady=(6, 0))

    root.protocol("WM_DELETE_WINDOW", lambda: (close_browser(), root.destroy()))
    root.mainloop()


def main():
    disp()


if __name__ == "__main__":
    main()
