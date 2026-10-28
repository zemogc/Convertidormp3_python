import pytest

from gateway.options import OptionsError, parse_options, parse_time


@pytest.mark.parametrize(
    "raw,expected",
    [("90", 90), ("12.5", 12.5), ("1:30", 90), ("01:02:03", 3723), (" 0:05 ", 5)],
)
def test_parse_time(raw, expected):
    assert parse_time(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "1:2:3:4", "-5", "1:"])
def test_parse_time_rejects_garbage(raw):
    with pytest.raises(OptionsError):
        parse_time(raw)


def test_defaults():
    assert parse_options({}) == {
        "format": "mp3",
        "bitrate": 192,
        "start": None,
        "end": None,
        "normalize": False,
        "title": None,
        "artist": None,
    }


def test_full_options():
    opts = parse_options(
        {
            "format": "OGG",
            "bitrate": "320",
            "start": "0:10",
            "end": "1:00",
            "normalize": "on",
            "title": "  Mi cancion\n",
            "artist": "Yo",
        }
    )
    assert opts == {
        "format": "ogg",
        "bitrate": 320,
        "start": 10,
        "end": 60,
        "normalize": True,
        "title": "Mi cancion",
        "artist": "Yo",
    }


def test_lossless_formats_drop_bitrate():
    assert parse_options({"format": "flac", "bitrate": "320"})["bitrate"] is None
    assert parse_options({"format": "wav"})["bitrate"] is None


def test_bitrate_snaps_to_nearest_allowed():
    assert parse_options({"format": "opus", "bitrate": "320"})["bitrate"] == 192
    assert parse_options({"bitrate": "200"})["bitrate"] == 192


def test_zero_start_is_canonicalized():
    # "start=0" y "sin start" deben producir la misma clave de cache.
    assert parse_options({"start": "0"}) == parse_options({})


@pytest.mark.parametrize(
    "form",
    [
        {"format": "exe"},
        {"bitrate": "mucho"},
        {"start": "10", "end": "5"},
        {"end": "0"},
        {"title": "x" * 201},
    ],
)
def test_invalid_options(form):
    with pytest.raises(OptionsError):
        parse_options(form)
