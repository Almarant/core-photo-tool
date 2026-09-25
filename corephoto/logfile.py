"""
A rolling log next to the settings file.

When a colleague says "it didn't work", there is otherwise nothing for them to
send you: the app shows a message box, they click OK, and the evidence is gone.
One line per photo with the detection numbers turns that into a file you can
read over email.

Never raises. A logging failure must not stop someone processing core.
"""
import datetime
import os
import sys
import traceback

MAX_BYTES = 512 * 1024
KEEP = 2


def path():
    from . import settings
    return os.path.join(os.path.dirname(settings._path()), "log.txt")


def _roll(p):
    try:
        if os.path.exists(p) and os.path.getsize(p) > MAX_BYTES:
            old = p + ".1"
            if os.path.exists(old):
                os.remove(old)
            os.replace(p, old)
    except OSError:
        pass


def write(msg):
    try:
        p = path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        _roll(p)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {msg}\n")
    except Exception:
        pass


def exception(where):
    write(f"EXCEPTION in {where}\n{traceback.format_exc()}")


def session_header(version):
    write("=" * 66)
    write(f"Core Photo Tool {version}  |  python {sys.version.split()[0]}  "
          f"|  frozen={bool(getattr(sys, 'frozen', False))}")
