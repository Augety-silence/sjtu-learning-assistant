"""Select only the verified dedicated FFmpeg before application imports."""
import os
import sys
from pathlib import Path

_root = getattr(sys, "_MEIPASS", None)
if not _root:
    raise RuntimeError("Frozen app is missing its FFmpeg resource root (_MEIPASS).")
_executable = Path(_root) / "ffmpeg" / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
if not _executable.is_file() or not os.access(_executable, os.X_OK):
    raise RuntimeError("Packaged FFmpeg is missing or not executable; reinstall the application.")
# Override even a user value: frozen apps never select PATH/wheel FFmpeg.
os.environ["IMAGEIO_FFMPEG_EXE"] = str(_executable)
