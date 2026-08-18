"""
Tiny persistent settings file.

Stored per user, not next to the .exe, so it survives replacing the exe and
works when the app lives on a read-only network share.
"""
import json
import os


def _path():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "CorePhotoTool")
    else:
        d = os.path.join(os.path.expanduser("~"), ".config", "corephototool")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, "settings.json")


def load():
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            v = json.load(fh)
            return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def get(key, default=None):
    return load().get(key, default)


def put(key, value):
    d = load()
    d[key] = value
    try:
        with open(_path(), "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
    except Exception:
        pass          # settings are a convenience; never block the app
    return d
