# Changelog

Chaque version publiée avec `uv run python release.py X.Y.Z` reprend la section correspondante :
c'est ce texte que Claude Pilot affiche quand il propose la mise à jour.

## 1.0.6

- **« là » ne prend plus de capture** : trop courant à l'oral, il en déclenchait à tout bout de champ. « regarde »,
  « tu vois », « ici » et « capture » restent. Tu peux toujours le remettre dans Réglages → Captures.

## 1.0.5

- **« Démarrer un retour » en haut à gauche**, au-dessus de la liste de tes retours : le nouveau retour vient
  s'y ajouter. Pendant l'enregistrement, ce bouton devient « Terminer le retour », et le bandeau avec Capturer,
  Pause et le chrono n'apparaît qu'à ce moment-là : plus de place pour lire tes retours le reste du temps.

## 1.0.4

- **Plus de cercle rouge autour de la souris** sur les captures : l'IA pouvait le prendre pour un élément de
  l'écran. Il reste disponible en option : Réglages → Captures → « Entourer ma souris en rouge ».

## 1.0.3

- **Mise à jour depuis l'appli réparée** : elle s'arrêtait sur « Claude Pilot est ouvert », alors que l'appli
  était bien fermée. L'installateur, lancé depuis le dossier de l'appli, empêchait lui-même de le remplacer.

Si tu passes directement de la 1.0.1 à cette version, tu as aussi les nouveautés de la 1.0.2 :

- « tu vois », « là » et « ici » capturent l'écran, seulement s'il a changé ou si la souris a bougé.
- « Retire la capture » (ou annule, supprime, efface…) retire la dernière capture et disparaît du texte.
- Clique sur une capture dans la fenêtre pour la voir en plein écran.

## 1.0.2

- **Nouveaux mots pour capturer** : « tu vois », « là » et « ici », en plus de « capture » et « regarde ». Ce sont
  des mots de tous les jours : ils restent dans le texte et ne capturent que si l'écran ou la souris ont bougé
  depuis la dernière capture. Un « regarde » juste après un « là » garde le moment du « regarde ».
- **« Retire la capture »** retire bien la dernière capture, comme « annule », « supprime » ou « efface la
  capture (d'écran) ». Avant, cette phrase en prenait une nouvelle. Les commandes disparaissent entièrement du
  texte, même quand le mot « capture » est mal reconnu.
- **Parler d'une capture n'en prend plus** : « comme sur la capture d'avant » reste tel quel dans le texte.
- **Captures en grand** : clique sur une capture dans la fenêtre pour l'afficher en plein écran, puis passe aux
  autres avec les flèches ou la molette. Échap ou un clic pour fermer.

## 1.0.1

- **Désinstallation corrigée** : elle pouvait s'arrêter sur certains fichiers du Python privé (chemins de
  plus de 260 caractères, raccourci de dossier créé par uv) et laisser le dossier d'installation en place.

## 1.0.0

Première version publique.

- **Retours vocaux pour Claude Code** : tu parles en montrant ton écran, la transcription arrive phrase par
  phrase, et chaque capture est placée au mot près (`[C2 · 0:31]`). À l'arrêt, le message est copié :
  `Ctrl+V` dans Claude Code, et il lit le texte puis chaque capture.
- **Captures** au raccourci (`F8`), au bouton ou à la voix (« capture », « screen », « regarde » à
  l'impératif). Une « boîte noire » garde les dernières secondes d'écran : la capture vocale prend l'image
  du moment exact du mot. Seul l'écran sous la souris est capturé, avec la souris entourée en rouge.
- **Commandes vocales** : « annule la capture », « point suivant ».
- **Transcription locale** avec trois moteurs au choix : Parakeet TDT v3 (par défaut), Whisper large-v3 et
  Whisper large-v3 turbo, sur carte graphique NVIDIA ou sur processeur. Comparaison et retranscription
  d'un retour dans Réglages → Transcription.
- **Fenêtre** avec transcription en direct, miniatures, historique des retours et test du micro ; barre
  flottante invisible dans les captures pendant l'enregistrement.
- **Bruitages** discrets pour suivre chaque action.
- **Commande `/retour`** pour Claude Code, à installer depuis les Réglages.
- **Installateur** qui récupère la dernière version, et **mises à jour** proposées au lancement avec ce
  changelog.
