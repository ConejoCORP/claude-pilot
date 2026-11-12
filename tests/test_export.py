from datetime import datetime
from pathlib import Path

from PIL import Image

from claudepilot.config import DEFAULT_KEPT, DEFAULT_TRIGGERS, Config
from claudepilot.export import capture_filename, compute_layout, export_session, render_markdown
from claudepilot.model import Capture, Utterance, Word
from claudepilot.session import Session

TRIGGERS, KEPT = DEFAULT_TRIGGERS, DEFAULT_KEPT


def utt(text: str, start: float, step: float = 0.4) -> Utterance:
    ws = [Word(w, round(start + i * step, 3), round(start + i * step + 0.3, 3)) for i, w in enumerate(text.split())]
    return Utterance(ws[0].start, ws[-1].end, ws)


def cap(t: float, source: str = "raccourci") -> Capture:
    return Capture(t, source, f"raw_{t}.png", 2, (2560, 1440), (100, 100))


def render(utterances, captures) -> str:
    names = [capture_filename(i + 1, c.t) for i, c in enumerate(captures)]
    layout = compute_layout(utterances, captures, TRIGGERS, KEPT)
    return render_markdown(
        utterances, captures, names, layout, datetime(2026, 10, 29, 16, 52), 60.0,
        "Parakeet TDT v3 (GPU)", Path("D:/retours/s"), lambda n: names[n - 1],
    )


def body(md: str) -> str:
    return md.split("fie-toi au sens général")[1].split("## Captures")[0]


def test_capture_au_milieu_d_une_phrase():
    md = render([utt("Et quand je clique dessus il ne se passe rien", 10.0)], [cap(11.9)])
    assert "clique dessus **[C1 · 0:11]** il ne se passe rien" in md


def test_capture_vocale_remplace_le_mot_capture():
    md = render([utt("une erreur rouge. Capture.", 20.0)], [cap(21.5, "voix")])
    assert "une erreur rouge. **[C1 · 0:21]**" in md
    assert "Capture." not in body(md)


def test_regarde_reste_et_le_marqueur_suit():
    md = render([utt("Regarde, le bouton Valider", 5.0)], [cap(5.3, "voix")])
    assert "Regarde, **[C1 · 0:05]** le bouton Valider" in md


def test_capture_dans_un_silence_ouvre_la_phrase_suivante():
    md = render([utt("première phrase", 1.0), utt("deuxième phrase", 10.0)], [cap(6.0)])
    assert "**[0:06]** **[C1 · 0:06]** deuxième phrase" in md


def test_capture_dite_seule_rejoint_la_phrase_precedente():
    utterances = [utt("une erreur rouge.", 20.0), utt("Capture.", 22.0), utt("Point suivant.", 24.0), utt("Le menu", 26.0)]
    md = render(utterances, [cap(22.3, "voix")])
    assert "une erreur rouge. **[C1 · 0:22]**" in md
    assert "### Point 2" in md and "**[0:26]** Le menu" in md


def test_capture_dite_seule_en_tout_debut_rejoint_la_phrase_suivante():
    md = render([utt("Capture.", 0.5), utt("le bouton est cassé", 3.0)], [cap(0.8, "voix")])
    assert "**[C1 · 0:00]** le bouton est cassé" in md


def test_capture_apres_la_derniere_phrase():
    md = render([utt("seule phrase", 1.0)], [cap(30.0)])
    assert "seule phrase **[C1 · 0:30]**" in md


def test_point_suivant_cree_des_sections():
    md = render([utt("le bouton est cassé. Point suivant. Le menu est bien", 0.0)], [])
    assert "### Point 1" in md and "### Point 2" in md
    assert "suivant" not in body(md)


def test_annule_la_capture_disparait_du_texte():
    md = render([utt("Capture. Annule la capture. ensuite", 0.0)], [])
    assert "Annule" not in body(md) and "ensuite" in body(md)


def test_retirer_la_capture_ne_laisse_rien_dans_le_texte():
    md = render([utt("le bouton rouge. Retire la capture. Supprime la capture d'écran. ensuite", 0.0)], [])
    assert "Retire" not in body(md) and "d'écran" not in body(md) and "ensuite" in body(md)


def test_ici_reste_et_le_marqueur_suit():
    md = render([utt("Le bouton ici est décalé", 2.0)], [cap(3.1, "voix")])
    assert "Le bouton ici **[C1 · 0:03]** est décalé" in md


def test_parler_de_la_capture_d_avant_garde_la_phrase():
    md = render([utt("comme sur la capture d'avant le menu", 0.0)], [])
    assert "comme sur la capture d'avant le menu" in md


def test_liste_finale_avec_chemins_absolus():
    md = render([utt("Regarde ici", 0.0)], [cap(0.3, "voix")])
    assert "C1_0m00s.png`" in md
    assert "écran 2 (2560×1440)" in md


def test_export_complet_et_reprise_apres_plantage(tmp_path):
    session = Session.create(tmp_path)
    Image.new("RGB", (2560, 1440), (200, 200, 200)).save(session.raw_dir / "a.png")
    session.add_capture(Capture(1.0, "raccourci", "a.png", 2, (2560, 1440), (1280, 720)))
    session.add_utterance(utt("Regarde le bouton ici", 0.5))
    session.finish(5.0, "Parakeet TDT v3 (GPU)")

    result = export_session(session, Config())
    with Image.open(result.images[0]) as out:
        assert max(out.size) <= 2000
    assert result.md_path.exists() and result.latest_path.exists()
    assert str(result.md_path) in result.prompt

    (session.folder / "session.json").unlink()  # simule un plantage avant la sauvegarde finale
    reloaded = Session.load(session.folder)
    assert len(reloaded.captures) == 1
    assert reloaded.utterances[0].text == "Regarde le bouton ici"
    assert reloaded.duration == 5.0


def test_cercle_rouge_seulement_si_demande(tmp_path):
    session = Session.create(tmp_path)
    Image.new("RGB", (2560, 1440), (200, 200, 200)).save(session.raw_dir / "a.png")
    session.add_capture(Capture(1.0, "raccourci", "a.png", 2, (2560, 1440), (1280, 720)))
    session.add_utterance(utt("Regarde le bouton", 0.5))
    session.finish(3.0, "Parakeet TDT v3 (GPU)")
    for ring in (False, True):
        result = export_session(session, Config(cursor_ring=ring))
        with Image.open(result.images[0]) as out:
            red = sum(n for n, (r, g, b) in out.getcolors(1 << 20) if r > 180 and g < 90 and b < 90)
        md = result.md_path.read_text(encoding="utf-8")
        assert (red > 0) == ring and ("cercle rouge" in md) == ring
    assert not Config().cursor_ring  # désactivé par défaut : l'IA peut prendre le cercle pour l'appli
