# Development

## Run from source

```bat
D:
cd D:\Documents\Projects\MediaReel
venv\Scripts\activate
python main.py
```

## Build

```bat
pyinstaller --onedir --windowed --name "MediaReel" --icon assets/icon.ico main.py
xcopy /E /I vendor dist\MediaReel\vendor
```
