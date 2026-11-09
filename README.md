# Portfolio — Achref Zemzemi

Site statique (`index.html`) hébergé sur GitHub Pages, domaine `achrefzemzmi.com`.

## Fonctionnement
- `projects.json` : liste des projets affichés (lue par `index.html`).
- `scripts/sync_behance.py` : lancé chaque jour par GitHub Actions (`.github/workflows/site.yml`).
  Il ajoute les nouveaux projets Behance en tête de `projects.json` et télécharge leurs miniatures dans `img/`.
- `overrides.json` : corrections manuelles (catégorie / titre / vues) qui s'appliquent après la synchro.
  Exemple : `{"257400569": {"cat": "m", "title": "Nouveau titre"}}` — catégories : `m` Motion, `s` Social Media, `g` Graphic, `c` Campaigns & Branding.
- Formulaire : Formspree. L'identifiant se règle dans `index.html` (`FORMSPREE_ID`).

## Test en local
```
python -m http.server 8000     # puis ouvrir http://localhost:8000
python scripts/sync_behance.py # lancer la synchro à la main
```

## Ajouter / retirer un projet à la main
Éditer `projects.json` (un bloc par projet), puis commit + push. Pour une miniature : mettre le nom du fichier Behance dans `img`.
