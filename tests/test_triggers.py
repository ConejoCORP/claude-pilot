from claudepilot.config import DEFAULT_KEPT, DEFAULT_TRIGGERS
from claudepilot.model import Word
from claudepilot.triggers import detect


def words(text: str, step: float = 0.4) -> list[Word]:
    return [Word(w, i * step, i * step + 0.3) for i, w in enumerate(text.split())]


def actions(text: str, commands: bool = True):
    return detect(words(text), DEFAULT_TRIGGERS, DEFAULT_KEPT, commands)


def kinds(text: str) -> list[tuple[str, str, bool]]:
    return [(a.kind, a.phrase, a.remove) for a in actions(text)]


def test_regarde_imperatif_reste_dans_le_texte():
    assert kinds("Regarde, le bouton Valider") == [("capture", "regarde", False)]
    assert kinds("Et puis regarde ce menu") == [("capture", "regarde", False)]
    assert not actions("Regarde")[0].everyday  # « regarde » capture à chaque fois


def test_je_regarde_ne_declenche_rien():
    assert kinds("Je regarde aussi le fichier") == []
    assert kinds("je le regarde depuis hier") == []
    assert kinds("on regarde ça ensemble") == []
    assert kinds("qu'on regarde demain") == []
    assert kinds("ça regarde le disque") == []


def test_mots_du_quotidien_capturent_et_restent_dans_le_texte():
    found = actions("Tu vois, ici le bouton")
    assert [(a.phrase, a.remove, a.everyday) for a in found] == [("tu vois", False, True), ("ici", False, True)]


def test_la_ne_capture_plus():
    assert kinds("Là il y a un bug, là-dessus, ce bouton-là") == []


def test_mot_ajoute_avec_trait_d_union():
    found = detect(words("clique là-dessus, ce bouton-là"), ["là"], ["là"])
    assert [a.phrase for a in found] == ["là", "là"]


def test_la_voila_et_d_ici_ne_capturent_pas():
    assert kinds("la page de la console") == []
    assert kinds("voilà c'est tout d'ici demain") == []


def test_capture_est_retiree_du_texte():
    assert kinds("une erreur rouge. Capture.") == [("capture", "capture", True)]


def test_toutes_les_facons_de_retirer_une_capture():
    for text in (
        "Retire la capture.", "Annule la capture.", "annule capture", "Supprime la dernière capture d'écran.",
        "efface le screen", "Enlève cette capture", "Bon, retire-moi la capture",
        "Supprime la carte pure d'écran.",  # « capture » mal reconnu, mais « d'écran » ne trompe pas
    ):
        found = actions(text)
        assert [(a.kind, a.remove) for a in found] == [("cancel", True)], text
        assert found[0].words[-1] == len(text.split()) - 1, text  # toute la phrase disparaît du texte


def test_retirer_ne_prend_pas_de_capture():
    assert [a.kind for a in actions("Capture. Retire la capture.")] == ["capture", "cancel"]


def test_une_remarque_pour_claude_n_annule_rien():
    assert kinds("ça supprime la capture quand on clique") == []
    assert kinds("il faut supprimer la capture") == []
    assert kinds("supprime ce bouton") == []
    assert kinds("ne supprime pas la capture") == []


def test_parler_d_une_capture_n_en_prend_pas():
    assert kinds("comme sur la capture d'avant") == []
    assert kinds("dans l'autre capture") == []
    assert kinds("la dernière capture est floue") == []
    assert kinds("prends la capture") == [("capture", "capture", True)]
    assert kinds("fais un screen") == [("capture", "screen", True)]


def test_capture_d_ecran_ne_compte_qu_une_fois():
    found = actions("fais une capture d'écran ici")
    assert [(a.kind, a.words, a.everyday) for a in found] == [("capture", (2, 3), False), ("capture", (4,), True)]


def test_point_suivant():
    assert kinds("Point suivant. Le menu") == [("point", "point suivant", True)]
    assert [a.words for a in actions("Passons au point suivant.")] == [(0, 1, 2, 3)]


def test_moment_de_la_capture_est_la_fin_du_mot():
    w = words("Regarde ici")
    assert detect(w, DEFAULT_TRIGGERS, DEFAULT_KEPT)[0].t == w[0].end


def test_commandes_desactivables():
    assert actions("Point suivant.", commands=False) == []
    assert actions("Retire la capture.", commands=False) == []
