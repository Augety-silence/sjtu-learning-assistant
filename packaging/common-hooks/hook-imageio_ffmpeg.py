"""Override the PyInstaller wheel hook: wrapper code, never vendor FFmpeg."""
# Specs collect the verified source-built FFmpeg explicitly. The stock hook
# collects imageio_ffmpeg/binaries, whose wheel executables may contain GPL code.
datas = []
binaries = []
excludedimports = ["imageio_ffmpeg.binaries"]
