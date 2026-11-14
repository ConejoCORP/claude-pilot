"""Import d'un audio ou d'une vidéo, et zip d'un retour. La vidéo de test est fabriquée ici avec PyAV."""
import zipfile
from fractions import Fraction

import av
import numpy as np
from PIL import Image

from claudepilot import importer
from claudepilot.config import Config
from claudepilot.export import export_session, make_zip
from claudepilot.model import Word

COLORS = [(220, 38, 38), (37, 99, 235), (22, 163, 74), (234, 179, 8)]  # une couleur par seconde


def make_video(path, seconds: int = 4, audio_start: float = 0.0) -> None:
    with av.open(str(path), "w") as out:
        video = out.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 320, 240, "yuv420p"
        audio = out.add_stream("aac", rate=44100)
        audio.layout = "mono"
        for i in range(seconds * 10):
            frame = av.VideoFrame.from_image(Image.new("RGB", (320, 240), COLORS[i // 10 % len(COLORS)]))
            out.mux(video.encode(frame))
        out.mux(video.encode())
        t = np.arange(int(44100 * seconds)) / 44100
        tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        for start in range(0, len(tone), 1024):
            frame = av.AudioFrame.from_ndarray(tone[None, start:start + 1024], format="fltp", layout="mono")
            frame.sample_rate, frame.pts, frame.time_base = 44100, start, Fraction(1, 44100)
            out.mux(audio.encode(frame))
        out.mux(audio.encode())


class FakeEngine:
    """Transcrit toujours la même phrase : « Regarde » à 2,5 s."""

    label = "moteur de test"

    def transcribe(self, audio, offset: float = 0.0, lexicon=None):
        return [Word("Bon,", offset + 0.2, offset + 0.5), Word("regarde", offset + 2.2, offset + 2.5),
                Word("ce", offset + 2.6, offset + 2.8), Word("bouton.", offset + 2.9, offset + 3.2)]


def test_audio_et_images_d_une_video(tmp_path):
    path = tmp_path / "partie.mp4"
    make_video(path)
    pcm, start = importer.decode_audio(path)
    assert abs(len(pcm) / 16000 - 4.0) < 0.15 and pcm.dtype == np.int16
    assert importer.has_video(path)
    frames = importer.VideoFrames(path, start)
    for t, color in ((0.5, COLORS[0]), (2.5, COLORS[2]), (3.9, COLORS[3])):
        frame = frames.frame_at(t)
        assert abs(frame.t - t) < 0.15
        r, g, b = frame.image().resize((1, 1)).getpixel((0, 0))
        assert max(abs(r - color[0]), abs(g - color[1]), abs(b - color[2])) < 30, (t, (r, g, b))
    frames.close()


def test_import_d_une_video_puis_zip(tmp_path, monkeypatch):
    path = tmp_path / "Ma partie.mp4"
    make_video(path)
    monkeypatch.setattr(importer.vad, "segment", lambda pcm: [(0, len(pcm))])  # un bip n'est pas de la parole
    steps: list[str] = []
    session = importer.import_media(path, tmp_path / "retours", FakeEngine(), Config(), steps.append)
    assert session.source == "Ma partie.mp4" and steps[0].startswith("Lecture")
    assert [(c.trigger, c.where) for c in session.captures] == [("regarde", "vidéo")]
    result = export_session(session, Config())
    md = result.md_path.read_text(encoding="utf-8")
    assert md.startswith("# Transcription de « Ma partie.mp4 »") and "regarde **[C1 · 0:02]**" in md

    with zipfile.ZipFile(make_zip(session.folder)) as archive:
        names = archive.namelist()
        inside = archive.read(names[0]).decode("utf-8")
    assert [n.split("/")[1] for n in names] == ["retour.md", "C1_0m02s.png"]
    assert str(session.folder) not in inside and "`C1_0m02s.png`" in inside


def test_un_mp3_n_a_pas_d_images(tmp_path):
    path = tmp_path / "memo.mp3"
    with av.open(str(path), "w") as out:
        stream = out.add_stream("libmp3lame", rate=44100)
        stream.layout = "mono"
        tone = (0.3 * np.sin(np.arange(44100 * 2) / 44100 * 2 * np.pi * 440)).astype(np.float32)
        for start in range(0, len(tone), 1152):
            frame = av.AudioFrame.from_ndarray(tone[None, start:start + 1152], format="fltp", layout="mono")
            frame.sample_rate, frame.pts, frame.time_base = 44100, start, Fraction(1, 44100)
            out.mux(stream.encode(frame))
        out.mux(stream.encode())
    assert not importer.has_video(path)
    assert abs(len(importer.decode_audio(path)[0]) / 16000 - 2.0) < 0.15
    assert importer.is_media(path) and not importer.is_media(tmp_path / "notes.txt")
