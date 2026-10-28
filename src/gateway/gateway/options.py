"""Validacion de las opciones de conversion que envia el cliente."""

import re

# formato -> bitrates permitidos (kbps). None = formato sin perdida, no lleva bitrate.
FORMATS: dict[str, tuple[int, ...] | None] = {
    "mp3": (64, 96, 128, 160, 192, 256, 320),
    "m4a": (64, 96, 128, 160, 192, 256),
    "ogg": (64, 96, 128, 160, 192, 256, 320),
    "opus": (32, 48, 64, 96, 128, 160, 192),
    "flac": None,
    "wav": None,
}
DEFAULT_BITRATE = 192
MAX_TEXT = 200
_TIME_RE = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class OptionsError(ValueError):
    pass


def parse_time(value: str) -> float:
    """Acepta segundos ("90", "12.5"), "mm:ss" o "hh:mm:ss"."""
    match = _TIME_RE.match(value.strip())
    if not match:
        raise OptionsError(f"tiempo invalido: {value!r}")
    a, b, seconds = match.groups()
    hours, minutes = (a, b) if b is not None else (None, a)
    total = float(seconds) + int(minutes or 0) * 60 + int(hours or 0) * 3600
    return round(total, 3)


def _flag(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "on", "yes", "si"}


def _text(value) -> str | None:
    text = _CONTROL_CHARS.sub("", str(value or "")).strip()
    if len(text) > MAX_TEXT:
        raise OptionsError(f"maximo {MAX_TEXT} caracteres en titulo/artista")
    return text or None


def parse_options(form) -> dict:
    """Normaliza un dict-like (form multipart) en opciones canonicas.

    El resultado es determinista para que sirva como parte de la clave de cache.
    """
    fmt = str(form.get("format") or "mp3").strip().lower()
    if fmt not in FORMATS:
        raise OptionsError(f"formato no soportado: {fmt} (usa {', '.join(FORMATS)})")

    bitrate = None
    allowed = FORMATS[fmt]
    if allowed is not None:
        raw = form.get("bitrate")
        try:
            bitrate = int(raw) if raw not in (None, "") else DEFAULT_BITRATE
        except ValueError as err:
            raise OptionsError("bitrate debe ser un numero") from err
        if bitrate not in allowed:
            bitrate = min(allowed, key=lambda b: abs(b - bitrate))

    start = parse_time(form["start"]) if form.get("start") else None
    end = parse_time(form["end"]) if form.get("end") else None
    if start == 0:
        start = None
    if end is not None and end <= (start or 0):
        raise OptionsError("el fin del recorte debe ser mayor que el inicio")

    return {
        "format": fmt,
        "bitrate": bitrate,
        "start": start,
        "end": end,
        "normalize": _flag(form.get("normalize", "")),
        "title": _text(form.get("title")),
        "artist": _text(form.get("artist")),
    }
