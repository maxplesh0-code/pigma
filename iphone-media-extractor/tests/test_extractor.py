import asyncio, os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import extractor

T1 = datetime(2026, 3, 5, 12, 0)
T2 = datetime(2026, 4, 9, 12, 0)


class FakeAfc:
    def __init__(self, files, bad=()):
        self.files, self.bad, self.h = files, set(bad), {}

    async def listdir(self, d):
        d = d.rstrip("/")
        return sorted({p[len(d) + 1:].split("/")[0] for p in self.files if p.startswith(d + "/")})

    async def stat(self, p):
        if p in self.files:
            data, t = self.files[p]
            return {"st_ifmt": "S_IFREG", "st_size": len(data), "st_mtime": t}
        return {"st_ifmt": "S_IFDIR", "st_size": 0, "st_mtime": T1}

    async def fopen(self, p, mode="r"):
        if p in self.bad:
            raise RuntimeError("afc error")
        self.h[1] = [self.files[p][0], 0]
        return 1

    async def fread(self, h, n):
        data, pos = self.h[h]
        self.h[h][1] = pos + n
        return data[pos:pos + n]

    async def fclose(self, h):
        pass


FILES = {
    "/DCIM/100APPLE/IMG_0001.HEIC": (b"a" * 10, T1),
    "/DCIM/100APPLE/IMG_0001.AAE": (b"x", T1),
    "/DCIM/100APPLE/IMG_0002.MOV": (b"b" * 5000, T1),
    "/DCIM/101APPLE/IMG_0001.HEIC": (b"c" * 20, T1),  # same name, other file
    "/DCIM/101APPLE/IMG_0003.JPG": (b"d" * 30, T2),
    "/DCIM/.MISC/Info.plist": (b"p", T1),
}


def run(c):
    return asyncio.run(c)


def test_scan_finds_media_only():
    items = run(extractor.scan(FakeAfc(FILES)))
    assert [i.path for i in items] == [
        "/DCIM/100APPLE/IMG_0001.HEIC", "/DCIM/100APPLE/IMG_0002.MOV",
        "/DCIM/101APPLE/IMG_0001.HEIC", "/DCIM/101APPLE/IMG_0003.JPG"]
    assert [i.kind for i in items] == ["photo", "video", "photo", "photo"]


def test_copy_by_month_then_resume():
    afc = FakeAfc(FILES)
    items = run(extractor.scan(afc))
    with tempfile.TemporaryDirectory() as d:
        r = run(extractor.copy_all(afc, items, d))
        assert (r["copied"], r["skipped"], r["failed"]) == (4, 0, [])
        assert sorted(os.listdir(d)) == ["2026-03", "2026-04"]
        assert sorted(os.listdir(os.path.join(d, "2026-03"))) == ["IMG_0001.HEIC", "IMG_0001_1.HEIC", "IMG_0002.MOV"]
        p = os.path.join(d, "2026-03", "IMG_0001_1.HEIC")
        assert open(p, "rb").read() == b"c" * 20
        assert os.path.getmtime(p) == T1.timestamp()
        r = run(extractor.copy_all(afc, items, d))  # second run copies nothing
        assert (r["copied"], r["skipped"]) == (0, 4)


def test_copy_keeps_phone_folders():
    afc = FakeAfc(FILES)
    items = run(extractor.scan(afc))
    with tempfile.TemporaryDirectory() as d:
        run(extractor.copy_all(afc, items, d, by_month=False))
        assert sorted(os.listdir(d)) == ["100APPLE", "101APPLE"]


def test_bad_file_is_skipped_and_cancel_cleans_up():
    afc = FakeAfc(FILES, bad=["/DCIM/100APPLE/IMG_0002.MOV"])
    items = run(extractor.scan(afc))
    with tempfile.TemporaryDirectory() as d:
        r = run(extractor.copy_all(afc, items, d))
        assert r["copied"] == 3 and r["failed"] == ["IMG_0002.MOV"]
    with tempfile.TemporaryDirectory() as d:
        try:
            run(extractor.copy_all(FakeAfc(FILES), items, d, cancelled=lambda: True))
            assert False
        except extractor.Cancelled:
            pass
        assert not any(f.endswith(".part") for _, _, fs in os.walk(d) for f in fs)
