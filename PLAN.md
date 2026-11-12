# Claude Pilot : plan et décisions

## But

Faire des retours à Claude Code aussi vite qu'en partage d'écran avec un humain. Tu parles, tu captures l'écran (raccourci, bouton ou voix), la transcription est stable, et chaque capture est placée au bon moment du texte. L'utilisateur ne fait qu'un `Ctrl+V` ou un `/retour`.

## Décisions (et pourquoi)

| Décision | Raison |
|---|---|
| Python + PySide6, tout en local | Python est déjà installé, c'est le meilleur écosystème pour la transcription, et il donne un accès direct aux fonctions Windows |
| Parakeet TDT v3 par défaut, Whisper large-v3 / turbo au choix | Banc d'essai du 29/10/2026 (`data/bench/`) : Parakeet 0 % d'erreur, 69× le temps réel, rien d'inventé. Whisper invente parfois une fin de phrase |
| onnxruntime-gpu 1.24.x (CUDA 12) | Les librairies CUDA 13 n'ont pas de version Windows sur PyPI |
| L'audio (FLAC sur le disque) est la source de vérité, avec une seule horloge | Un plantage ne perd rien, on peut retranscrire, et mots et captures ne se décalent pas |
| Transcription phrase par phrase (détecteur de voix Silero) | Le texte ne bouge plus une fois écrit, et le résultat est prêt en 1 à 2 s à l'arrêt |
| Boîte noire de 60 s en mémoire (2 images/s), jamais écrite sur le disque | Une capture vocale prend l'image du moment exact du mot, pas celle d'une seconde plus tard |
| On capture l'écran sous la souris, pas les 3 écrans collés | 7 040 px de large seraient réduits et illisibles par Claude |
| Export à 2000 px max, avec un bandeau « Cn · m:ss · écran n » (souris entourée en option, désactivée par défaut : l'IA prenait le cercle pour un élément de l'appli) | La correspondance capture / phrase ne se perd jamais. 2000 px passe la règle de l'API au-delà de 20 images |
| Dossier lu par Claude Code (`retour.md` + PNG), plutôt qu'un collage dans le chat | Pas de limite de 20 images par message, et aucune étape manuelle |
| Barre flottante exclue des captures (`SetWindowDisplayAffinity`) | Vérifié par `tools/check_overlay.py` (écart 0,0 contre 170 pour une fenêtre normale) |
| Raccourcis par `RegisterHotKey`, `F8` pris seulement pendant l'enregistrement | Pas de hook clavier (rien de suspect pour l'antivirus), et `F8` reste libre le reste du temps |
| Bruitages synthétisés dans l'appli | Esprit « Apple » sans fichier emprunté. Le volume règle la génération |
| Vraie appli avec fenêtre, pas d'icône en arrière-plan | Demandé par l'utilisateur : fermer la fenêtre quitte l'appli. La barre flottante ne s'affiche que pendant l'enregistrement |
| Icônes dessinées en images (et non en caractères de police) | Une feuille de style qui imposait « Segoe UI » transformait les icônes en carrés |
| `/retour` installé comme skill (`~/.claude/skills/retour/SKILL.md`) | Doc Claude Code du 29/10/2026 : les commandes perso ont fusionné avec les skills |

## Fait (v0.1)

- Fenêtre principale :
  - historique des retours ;
  - transcription en direct avec miniatures ;
  - choix du moteur et du micro, avec test du micro ;
  - actions : copier, ouvrir, récupérer, supprimer.
- Réglages en onglets (Général, Captures, Transcription, Sons, Claude Code) ; comparer et retranscrire sont dans l'onglet Transcription.
- Barre flottante pendant l'enregistrement, réglages au thème sombre, bruitages.
- Captures par raccourci, bouton et voix (« capture », « screen », « regarde » à l'impératif, et « tu vois », « ici » qui ne capturent que si l'écran ou la souris ont bougé), plus les commandes « retire la capture » (annule, supprime, efface…) et « point suivant ».
- Captures cliquables dans la fenêtre : visionneuse plein écran, flèches pour passer d'une capture à l'autre.
- Trois moteurs interchangeables, comparaison et retranscription d'un retour.
- Export `retour.md` et `dernier_retour.md`, message pour Claude Code copié à l'arrêt, commande `/retour` installable depuis les Réglages.
- Reprise après plantage.
- Publication sur GitHub (StundZow/claude-pilot) : installateur qui récupère la dernière version, mises à jour proposées au lancement avec le changelog, désinstallation propre (`release.py X.Y.Z` publie une version).
- Tests :
  - 21 tests unitaires ;
  - simulation de bout en bout (Parakeet et Whisper) ;
  - banc d'essai des moteurs ;
  - test d'invisibilité de la barre ;
  - capture réelle sur les 3 écrans ;
  - photos de chaque écran de l'appli (`tools/snapshot_ui.py`).

## À faire ensuite

1. **Tests grandeur nature par l'utilisateur** (voix réelle), puis réglage du détecteur de voix (pause de 600 ms) et des déclencheurs.
2. Écran de relecture avant envoi : corriger le texte, déplacer ou supprimer une capture, en ajouter une en cliquant une phrase (boîte noire gardée plus longtemps), recadrer, flouter.
3. Capture d'une zone choisie, et capture au bouton latéral de la souris.
4. Serveur MCP : Claude récupère le dernier retour, texte et images dans l'ordre, sans fichier intermédiaire.
5. Exports pour humains : PDF, et vidéo MP4 (diaporama + voix + sous-titres).
6. Mode dictée partout (maintenir une touche pour écrire avec la voix) et mode tutoriel (une capture à chaque clic).
7. Historique avec recherche, lancement avec Windows (option).
