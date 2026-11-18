"""iPhone Media Extractor. One window: plug in, pick a folder, extract."""
import asyncio
import os
import subprocess
import sys
import threading
import time

import webview

import extractor

DEFAULT_DEST = os.path.join(os.path.expanduser("~"), "Pictures", "iPhone Media")


def resource(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


class App:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "phase": "searching",  # searching no_driver disconnected locked trust denied
            #                        scanning ready extracting done error
            "device": "",
            "photos": 0,
            "videos": 0,
            "bytes": 0,
            "dest": DEFAULT_DEST,
            "free": extractor.free_space(DEFAULT_DEST),
            "index": 0,
            "total": 0,
            "done_bytes": 0,
            "total_bytes": 0,
            "file": "",
            "speed": 0,
            "result": None,
            "message": "",
        }
        self._start = threading.Event()
        self._cancel = threading.Event()
        self._rescan = threading.Event()
        self._window = None

    # ---- called from the UI ----
    def get_state(self):
        with self._lock:
            return dict(self._state)

    def browse(self):
        picked = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=self._existing(self._state["dest"]))
        if picked:
            path = picked[0] if isinstance(picked, (list, tuple)) else picked
            self._set(dest=path, free=extractor.free_space(path))
        return self.get_state()

    def start(self):
        self._cancel.clear()
        self._start.set()

    def cancel(self):
        self._cancel.set()

    def again(self):
        self._rescan.set()

    def open_folder(self):
        path = self._state["dest"]
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # ---- helpers ----
    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    @staticmethod
    def _existing(path):
        while path and not os.path.isdir(path):
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
        return path

    # ---- device worker ----
    def _run_worker(self):
        asyncio.run(self._worker())

    async def _worker(self):
        from pymobiledevice3 import exceptions as ex
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.afc import AfcService
        from pymobiledevice3.usbmux import list_devices

        while True:
            try:
                devices = await list_devices()
            except ex.ConnectionFailedToUsbmuxdError:
                self._set(phase="no_driver")
                await asyncio.sleep(2)
                continue
            except Exception:
                devices = []
            devices = [d for d in devices if d.connection_type == "USB"] or devices
            if not devices:
                self._set(phase="disconnected", device="")
                await asyncio.sleep(1)
                continue
            serial = devices[0].serial
            try:
                lockdown = await create_using_usbmux(serial=serial, autopair=True, pair_timeout=0)
            except ex.PasswordRequiredError:
                self._set(phase="locked")
                await asyncio.sleep(1.5)
                continue
            except (ex.PairingDialogResponsePendingError, ex.NotPairedError):
                self._set(phase="trust")
                await asyncio.sleep(1.5)
                continue
            except ex.UserDeniedPairingError:
                self._set(phase="denied")
                await asyncio.sleep(3)
                continue
            except Exception:
                self._set(phase="disconnected")
                await asyncio.sleep(1.5)
                continue

            name = lockdown.display_name or "iPhone"
            try:
                async with AfcService(lockdown) as afc:
                    await self._session(afc, name)
            except extractor.Cancelled:
                pass
            except OSError as e:
                if getattr(e, "errno", None) == 28:
                    self._set(phase="error", message="The folder ran out of space. Pick another folder and try again.")
                    await self._wait_again()
                else:
                    self._set(phase="error", message="The iPhone was disconnected. Plug it back in. Copied files are kept.")
                    await asyncio.sleep(2.5)
            except Exception:
                self._set(phase="error", message="The iPhone was disconnected. Plug it back in. Copied files are kept.")
                await asyncio.sleep(2.5)
            finally:
                try:
                    await lockdown.close()
                except Exception:
                    pass

    async def _wait_again(self):
        self._rescan.clear()
        while not self._rescan.is_set():
            await asyncio.sleep(0.3)

    async def _session(self, afc, name):
        items = None
        while True:
            self._rescan.clear()
            self._start.clear()
            if items is None:
                self._set(phase="scanning", device=name, photos=0, videos=0, bytes=0)

                def found(found_items):
                    if len(found_items) % 50 == 0:
                        self._count(found_items)

                items = await extractor.scan(afc, on_found=found)
                self._count(items)
            self._set(phase="ready", free=extractor.free_space(self._state["dest"]))

            # wait for Extract; a light ping tells us if the cable was pulled
            ticks = 0
            while not self._start.is_set():
                await asyncio.sleep(0.25)
                ticks += 1
                if ticks % 8 == 0:
                    await afc.listdir("/")
            self._start.clear()

            dest = self._state["dest"]
            started = time.time()
            self._set(phase="extracting", index=0, total=len(items), done_bytes=0,
                      total_bytes=sum(i.size for i in items), file="", speed=0, result=None)

            def progress(i, total, done, total_bytes, fname):
                elapsed = max(time.time() - started, 0.5)
                self._set(index=i, done_bytes=done, file=fname, speed=done / elapsed)

            try:
                result = await extractor.copy_all(
                    afc, items, dest,
                    on_progress=progress, cancelled=self._cancel.is_set,
                )
            except extractor.Cancelled:
                self._cancel.clear()
                continue  # back to ready, copied files stay
            self._set(phase="done", result={**result, "failed": len(result["failed"])})
            await self._wait_again()
            items = None  # scan again for the next run

    def _count(self, items):
        self._set(
            photos=sum(1 for i in items if i.kind == "photo"),
            videos=sum(1 for i in items if i.kind == "video"),
            bytes=sum(i.size for i in items),
        )


def main():
    app = App()
    app._window = webview.create_window(
        "iPhone Media Extractor", resource("ui", "index.html"), js_api=app,
        width=460, height=640, resizable=False, background_color="#F5F5F7",
    )
    threading.Thread(target=app._run_worker, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
