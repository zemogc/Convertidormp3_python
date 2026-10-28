import shutil
import subprocess
from pathlib import Path

import pytest

from converter import ffmpeg

BASE = {"format": "mp3", "bitrate": 192, "start": None, "end": None, "normalize": False}


def opts(**kw):
    return {**BASE, "title": None, "artist": None, **kw}


def test_minimal_mp3_command():
    cmd = ffmpeg.build_command("in", "out.mp3", opts())
    assert cmd[:2] == ["ffmpeg", "-hide_banner"]
    assert "-ss" not in cmd and "-t" not in cmd and "-af" not in cmd
    assert cmd[cmd.index("-c:a") + 1] == "libmp3lame"
    assert cmd[cmd.index("-b:a") + 1] == "192k"
    assert cmd[-1] == "out.mp3"


def test_trim_uses_fast_seek_before_input():
    cmd = ffmpeg.build_command("in", "out.mp3", opts(start=10.5, end=40))
    i = cmd.index("-i")
    assert cmd.index("-ss") < i and cmd[cmd.index("-ss") + 1] == "10.5"
    assert cmd.index("-t") < i and cmd[cmd.index("-t") + 1] == "29.5"


def test_only_end_trims_from_beginning():
    cmd = ffmpeg.build_command("in", "out.mp3", opts(end=30))
    assert "-ss" not in cmd
    assert cmd[cmd.index("-t") + 1] == "30"


def test_normalize_and_metadata():
    cmd = ffmpeg.build_command(
        "in", "out.opus", opts(format="opus", bitrate=96, normalize=True, title="T", artist="A")
    )
    assert cmd[cmd.index("-af") + 1].startswith("loudnorm")
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert "title=T" in cmd and "artist=A" in cmd


@pytest.mark.parametrize("fmt,codec", [("flac", "flac"), ("wav", "pcm_s16le")])
def test_lossless_has_no_bitrate(fmt, codec):
    cmd = ffmpeg.build_command("in", f"out.{fmt}", opts(format=fmt, bitrate=None))
    assert cmd[cmd.index("-c:a") + 1] == codec
    assert "-b:a" not in cmd


def test_metadata_value_is_a_single_argument():
    # Sin shell: un titulo con comillas o ; no puede inyectar argumentos.
    cmd = ffmpeg.build_command("in", "o.mp3", opts(title='x"; rm -rf / #'))
    assert 'title=x"; rm -rf / #' in cmd


def test_parse_progress():
    seen = []
    lines = [
        "frame=0",
        "out_time_us=5000000",
        "progress=continue",
        "out_time_ms=10000000",
        "out_time_us=N/A",
        "out_time_us=25000000",
        "progress=end",
    ]
    ffmpeg.parse_progress(lines, 20.0, seen.append)
    assert seen == [25.0, 50.0, 99.9, 100.0]


def test_parse_progress_unknown_duration_reports_none():
    seen = []
    ffmpeg.parse_progress(["out_time_us=1000", "progress=end"], 0.0, seen.append)
    assert seen == [None, 100.0]


@pytest.mark.parametrize(
    "total,options,expected",
    [
        (100.0, {}, 100.0),
        (100.0, {"start": 10}, 90.0),
        (100.0, {"start": 10, "end": 30}, 20.0),
        (100.0, {"end": 500}, 100.0),
        (0.0, {"end": 30}, 30.0),
    ],
)
def test_clip_duration(total, options, expected):
    assert ffmpeg.clip_duration(total, options) == expected


def test_clip_start_after_end_of_video():
    with pytest.raises(ffmpeg.PermanentError):
        ffmpeg.clip_duration(10.0, {"start": 20})


def test_normalize_peaks_downsamples_with_max():
    assert ffmpeg.normalize_peaks([1, 4, 2, 8, 3, 2], 3) == [0.5, 1.0, 0.375]


def test_normalize_peaks_short_and_silent():
    assert ffmpeg.normalize_peaks([10, 20], 160) == [0.5, 1.0]
    assert ffmpeg.normalize_peaks([0, 0, 0], 2) == [0.0, 0.0]
    assert ffmpeg.normalize_peaks([], 160) == []


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no esta instalado")


@needs_ffmpeg
def test_real_conversion(tmp_path):
    video = tmp_path / "in.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=64x64",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=3",
            "-shortest",
            str(video),
        ],
        check=True,
    )
    total = ffmpeg.clip_duration(ffmpeg.probe(str(video)), {"start": 1})
    seen = []
    out = tmp_path / "out.mp3"
    ffmpeg.convert(str(video), str(out), opts(start=1, normalize=True), total, seen.append)
    assert out.stat().st_size > 0
    assert seen[-1] == 100.0
    assert ffmpeg.probe(str(out)) == pytest.approx(2.0, abs=0.2)


@needs_ffmpeg
def test_video_without_audio_is_permanent_error(tmp_path):
    video = tmp_path / "mute.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1", str(video)],
        check=True,
    )
    with pytest.raises(ffmpeg.PermanentError):
        ffmpeg.probe(str(video))


@needs_ffmpeg
def test_real_waveform_follows_volume(tmp_path):
    audio = tmp_path / "fade.wav"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=4", "-af", "afade=t=in:d=4", str(audio),
        ],
        check=True,
    )  # fmt: skip
    peaks = ffmpeg.waveform(str(audio), 4.0)
    assert len(peaks) == ffmpeg.WAVEFORM_POINTS
    assert max(peaks) == 1.0
    assert peaks[10] < peaks[80] < peaks[-10]  # fade in: el volumen sube


@needs_ffmpeg
def test_cancel_kills_ffmpeg(tmp_path):
    video = tmp_path / "long.mkv"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=600",
            str(video),
        ],
        check=True,
    )  # fmt: skip

    def cancel(_):
        raise ffmpeg.Canceled()

    with pytest.raises(ffmpeg.Canceled):
        ffmpeg.convert(str(video), str(tmp_path / "o.mp3"), opts(), 600.0, cancel)
    # ffmpeg murio: no queda ningun proceso llamado ffmpeg
    alive = []
    for comm in Path("/proc").glob("[0-9]*/comm"):
        try:
            alive += [comm] if comm.read_text().strip() == "ffmpeg" else []
        except OSError:
            pass
    assert alive == []


@needs_ffmpeg
def test_probe_measures_streamed_files_without_duration(tmp_path):
    # Matroska escrito por pipe: igual que un .webm grabado en el navegador.
    data = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=7",
            "-f", "matroska", "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout  # fmt: skip
    video = tmp_path / "streamed.mkv"
    video.write_bytes(data)
    assert ffmpeg.probe(str(video)) == pytest.approx(7.0, abs=0.1)
