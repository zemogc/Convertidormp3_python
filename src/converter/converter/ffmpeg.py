"""Construccion y ejecucion de comandos ffmpeg/ffprobe con reporte de progreso."""

import json
import subprocess
import tempfile
from collections.abc import Callable, Iterable

# formato -> argumentos del codec (sin bitrate)
CODECS = {
    "mp3": ["-c:a", "libmp3lame", "-id3v2_version", "3"],
    "m4a": ["-c:a", "aac", "-movflags", "+faststart"],
    "ogg": ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s16le"],
}
# Normalizacion de sonoridad EBU R128 (el estandar de streaming: -16 LUFS)
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


class PermanentError(Exception):
    """El archivo no se puede convertir; reintentar no sirve de nada."""


class FfmpegError(Exception):
    pass


def probe(path: str) -> float:
    """Devuelve la duracion en segundos. Falla si el archivo no tiene audio."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "format=duration:stream=codec_type",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PermanentError("el archivo no es un video o audio valido")
    info = json.loads(result.stdout or "{}")
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise PermanentError("el archivo no tiene pista de audio")
    try:
        return float(info["format"]["duration"])
    except (KeyError, ValueError):
        return 0.0


def clip_duration(total: float, options: dict) -> float:
    start = options.get("start") or 0.0
    end = options.get("end")
    stop = min(end, total) if end and total else (end or total)
    if total and start >= total:
        raise PermanentError("el inicio del recorte es posterior al final del video")
    return max(stop - start, 0.0)


def build_command(src: str, dst: str, options: dict) -> list[str]:
    fmt = options["format"]
    start, end = options.get("start"), options.get("end")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    # -ss/-t antes de -i: busqueda rapida, ffmpeg no decodifica lo que se descarta.
    if start:
        cmd += ["-ss", f"{start:g}"]
    if end:
        cmd += ["-t", f"{end - (start or 0):g}"]
    cmd += ["-i", src, "-vn", "-map", "0:a:0", "-map_metadata", "-1"]
    if options.get("normalize"):
        # loudnorm trabaja a 192 kHz; se vuelve a una frecuencia normal a la salida.
        cmd += ["-af", LOUDNORM, "-ar", "48000" if fmt == "opus" else "44100"]
    cmd += CODECS[fmt]
    if options.get("bitrate"):
        cmd += ["-b:a", f"{options['bitrate']}k"]
    for key in ("title", "artist"):
        if options.get(key):
            cmd += ["-metadata", f"{key}={options[key]}"]
    cmd += ["-progress", "pipe:1", "-nostats", dst]
    return cmd


def parse_progress(lines: Iterable[str], total: float, on_progress: Callable[[float], None]):
    """Lee la salida de `-progress` y reporta un porcentaje 0-100."""
    for line in lines:
        key, _, value = line.strip().partition("=")
        # out_time_ms tambien viene en microsegundos (bug historico de ffmpeg).
        if key in ("out_time_us", "out_time_ms") and total > 0:
            try:
                seconds = int(value) / 1_000_000
            except ValueError:
                continue
            on_progress(min(max(seconds / total * 100, 0.0), 99.9))
        elif key == "progress" and value == "end":
            on_progress(100.0)


def convert(
    src: str, dst: str, options: dict, total: float, on_progress: Callable[[float], None]
) -> None:
    cmd = build_command(src, dst, options)
    # stderr a un archivo: si fuera un pipe sin leer podria llenarse y bloquear ffmpeg.
    with tempfile.TemporaryFile(mode="w+") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True)
        assert proc.stdout is not None
        parse_progress(proc.stdout, total, on_progress)
        code = proc.wait()
        if code != 0:
            err.seek(0)
            detail = err.read().strip().splitlines()
            raise FfmpegError(detail[-1] if detail else f"ffmpeg salio con codigo {code}")
