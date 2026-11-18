"""Scan and copy photos and videos from an iPhone camera roll (DCIM over AFC)."""
import os
import posixpath
import shutil
import time
from dataclasses import dataclass

PHOTO_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".dng", ".tif", ".tiff", ".webp", ".bmp"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".3gp"}
ROOT = "/DCIM"
CHUNK = 4 * 1024 * 1024


class Cancelled(Exception):
    pass


@dataclass
class Item:
    path: str
    size: int
    mtime: float
    kind: str  # "photo" or "video"


def kind_of(name):
    ext = posixpath.splitext(name)[1].lower()
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    return None


async def scan(afc, on_found=None, cancelled=lambda: False):
    """Return every photo and video under /DCIM."""
    items = []
    stack = [ROOT]
    while stack:
        folder = stack.pop()
        try:
            names = await afc.listdir(folder)
        except Exception:
            continue
        for name in sorted(names):
            if cancelled():
                raise Cancelled()
            if name in (".", "..", "") or name.startswith("."):
                continue
            path = posixpath.join(folder, name)
            kind = kind_of(name)
            if kind is None and "." in name:
                continue  # sidecar files (.AAE etc)
            try:
                st = await afc.stat(path)
            except Exception:
                continue
            if st.get("st_ifmt") == "S_IFDIR":
                stack.append(path)
            elif kind:
                items.append(Item(path, int(st["st_size"]), st["st_mtime"].timestamp(), kind))
                if on_found:
                    on_found(items)
    items.sort(key=lambda i: i.path)
    return items


MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def folder_for(item):
    """Photos/2026/03 March, or Videos/2026/03 March."""
    t = time.localtime(item.mtime)
    top = "Photos" if item.kind == "photo" else "Videos"
    return os.path.join(top, str(t.tm_year), f"{t.tm_mon:02d} {MONTHS[t.tm_mon - 1]}")


def target_for(item, dest, taken):
    """Pick the local path. Returns (path, already_copied)."""
    folder = os.path.join(dest, folder_for(item))
    stem, ext = os.path.splitext(posixpath.basename(item.path))
    n = 0
    while True:
        name = f"{stem}{ext}" if n == 0 else f"{stem}_{n}{ext}"
        path = os.path.join(folder, name)
        key = os.path.normcase(path)
        if key not in taken:
            if not os.path.exists(path):
                taken.add(key)
                return path, False
            if os.path.getsize(path) == item.size:
                taken.add(key)
                return path, True
        n += 1


def free_space(dest):
    probe = dest
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return shutil.disk_usage(probe or ".").free


async def copy_all(afc, items, dest, on_progress=None, cancelled=lambda: False):
    """Copy items into dest. Skips files already there. Returns a result dict."""
    res = {"copied": 0, "skipped": 0, "failed": [], "bytes": 0}
    total_bytes = sum(i.size for i in items)
    done_bytes = 0
    taken = set()
    streak = 0  # failures in a row: many means the phone is gone

    def report(i, name):
        if on_progress:
            on_progress(i, len(items), done_bytes, total_bytes, name)

    for idx, item in enumerate(items):
        if cancelled():
            raise Cancelled()
        name = posixpath.basename(item.path)
        report(idx, name)
        path, exists = target_for(item, dest, taken)
        if exists:
            res["skipped"] += 1
            done_bytes += item.size
            continue
        part = path + ".part"
        start = done_bytes
        handle = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            handle = await afc.fopen(item.path, "r")
            left = item.size
            with open(part, "wb") as out:
                while left > 0:
                    if cancelled():
                        raise Cancelled()
                    data = await afc.fread(handle, min(CHUNK, left))
                    if not data:
                        raise IOError("short read")
                    out.write(data)
                    left -= len(data)
                    done_bytes += len(data)
                    report(idx, name)
            os.replace(part, path)
            os.utime(path, (item.mtime, item.mtime))
            res["copied"] += 1
            res["bytes"] += item.size
            streak = 0
        except Cancelled:
            _remove(part)
            raise
        except (ConnectionError, OSError) as e:
            _remove(part)
            if isinstance(e, ConnectionError) or getattr(e, "errno", None) == 28:
                raise  # phone unplugged or disk full: stop
            res["failed"].append(name)
            done_bytes = start + item.size
        except Exception:
            _remove(part)
            res["failed"].append(name)
            done_bytes = start + item.size
            streak += 1
            if streak >= 5:
                raise ConnectionError("device lost")
        finally:
            if handle is not None:
                try:
                    await afc.fclose(handle)
                except Exception:
                    pass
    report(len(items), "")
    return res


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
