"""Construccion y ejecucion de comandos ffmpeg/ffprobe con reporte de progreso."""

import json
import subprocess
import tempfile
from array import array
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
# Forma de onda: cantidad de barras y frecuencia de muestreo usada para calcularla.
WAVEFORM_POINTS = 160
WAVEFORM_RATE = 8000


class PermanentError(Exception):
    """El archivo no se puede convertir; reintentar no sirve de nada."""


class FfmpegError(Exception):
    pass


class Canceled(Exception):
    """El usuario cancelo el trabajo mientras se convertia."""


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


def parse_progress(lines: Iterable[str], total: float, on_progress: Callable[[float | None], None]):
    """Lee la salida de `-progress` y reporta un porcentaje 0-100.

    Si no se conoce la duracion reporta None: no hay porcentaje, pero el
    callback igual se ejecuta (lo usa el worker para detectar cancelaciones).
    """
    for line in lines:
        key, _, value = line.strip().partition("=")
        # out_time_ms tambien viene en microsegundos (bug historico de ffmpeg).
        if key in ("out_time_us", "out_time_ms"):
            try:
                seconds = int(value) / 1_000_000
            except ValueError:
                continue
            on_progress(min(max(seconds / total * 100, 0.0), 99.9) if total > 0 else None)
        elif key == "progress" and value == "end":
            on_progress(100.0)


def convert(
    src: str, dst: str, options: dict, total: float, on_progress: Callable[[float | None], None]
) -> None:
    cmd = build_command(src, dst, options)
    # stderr a un archivo: si fuera un pipe sin leer podria llenarse y bloquear ffmpeg.
    with tempfile.TemporaryFile(mode="w+") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True)
        assert proc.stdout is not None
        try:
            parse_progress(proc.stdout, total, on_progress)
        except BaseException:
            # Cancelacion (o cualquier error en el callback): no dejar ffmpeg huerfano.
            proc.kill()
            proc.wait()
            raise
        code = proc.wait()
        if code != 0:
            err.seek(0)
            detail = err.read().strip().splitlines()
            raise FfmpegError(detail[-1] if detail else f"ffmpeg salio con codigo {code}")


def waveform(path: str, duration: float, points: int = WAVEFORM_POINTS) -> list[float]:
    """Envolvente de picos normalizada (0-1) para dibujar la forma de onda.

    Decodifica a PCM mono de 16 bits en streaming y toma el pico de cada tramo,
    asi la memoria usada no depende de la duracion del audio.
    """
    bucket = max(int(duration * WAVEFORM_RATE) // points, 1)
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", path,
        "-ac", "1", "-ar", str(WAVEFORM_RATE), "-f", "s16le", "-",
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    peaks: list[int] = []
    try:
        while data := proc.stdout.read(bucket * 2):
            samples = array("h")
            samples.frombytes(data[: len(data) // 2 * 2])
            if samples:
                peaks.append(max(max(samples), -min(samples)))
    finally:
        proc.stdout.close()
        proc.wait()
    return normalize_peaks(peaks, points)


def normalize_peaks(peaks: list[int], points: int) -> list[float]:
    """Reduce a `points` barras (maximo por grupo) y escala a 0-1."""
    if len(peaks) > points:
        size = len(peaks) / points
        peaks = [
            max(peaks[int(i * size) : max(int((i + 1) * size), int(i * size) + 1)])
            for i in range(points)
        ]
    top = max(peaks, default=0) or 1
    return [round(p / top, 3) for p in peaks]
