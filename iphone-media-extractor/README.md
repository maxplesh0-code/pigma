# iPhone Media Extractor

Plug in an iPhone. Pick a folder. Copy every photo and video.

## Use

1. Open the app
2. Plug in the iPhone with a USB cable
3. Unlock it and tap **Trust**
4. Pick a folder with **Browse** (your Desktop works)
5. Press **Extract all**

Run it again later and it only copies new files.

## Get the app

Download the build from the GitHub Actions run of this branch (Artifacts section):

- `iPhone-Media-Extractor-Windows` is a Setup installer for Windows 11
- `iPhone-Media-Extractor-macOS` is a zipped `.app`

Windows also needs the free **Apple Devices** app (or iTunes) from the Microsoft Store. It carries the USB driver.

## Run from source

```
pip install -r requirements.txt
python app.py
```

Preview the screens in a browser: open `ui/index.html?demo=ready`
(also `disconnected`, `trust`, `scanning`, `extracting`, `done`, `error`).

## What it does

- Reads the camera roll (`/DCIM`) over USB
- Copies photos and videos as the original files, no conversion
- Keeps the original file dates
- Sorts into `Photos` and `Videos`, then year, then month:

```
My folder/
  Photos/
    2026/
      03 March/
      04 April/
  Videos/
    2026/
      03 March/
```
- Skips files already in the folder
- Checks free disk space before it starts
- Never deletes or changes anything on the phone

## Limits

- If **Optimize iPhone Storage** is on, full size originals live in iCloud. Only what is on the phone gets copied
- Photos in HEIC and videos in MOV stay in those formats
- Media inside other apps (WhatsApp, Telegram) is not in the camera roll unless saved to Photos

## Tests

```
pip install pytest
python -m pytest tests
```
