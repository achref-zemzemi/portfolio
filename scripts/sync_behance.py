#!/usr/bin/env python3
"""
Synchronise projects.json avec ton profil Behance et retélécharge TOUTES les miniatures dans img/.

À chaque exécution :
- l'ORDRE des projets suit exactement celui de Behance ;
- les couvertures sont toujours retéléchargées (l'ancienne image est remplacée,
  même si Behance a gardé le même nom de fichier) ;
- les nouveaux projets sont ajoutés, le nombre de vues est mis à jour ;
- le titre suit Behance, SAUF si tu l'as retouché à la main ;
- la catégorie ("cat") que tu as choisie est conservée.

Projets présents dans projects.json mais plus trouvés sur Behance :
- gardés à la fin de la liste par sécurité (si Behance renvoie une liste partielle) ;
- supprimés (JSON + image) si tu lances avec --prune.

Corrections manuelles : overrides.json  ->  {"ID_DU_PROJET": {"cat": "g", "title": "Nouveau titre"}}

Stdlib uniquement (aucun pip install). Usage :
    python scripts/sync_behance.py
    python scripts/sync_behance.py --prune
"""
import glob
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser

USERNAME = os.environ.get("BEHANCE_USER", "achrefzemzemi")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PATH = os.path.join(ROOT, "projects.json")
OVERRIDES_PATH = os.path.join(ROOT, "overrides.json")
IMG_DIR = os.path.join(ROOT, "img")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

IMG_RE = re.compile(
    r"mir-s3-cdn-cf\.behance\.net/projects/[^/\s\"'<>]+/([^/\s\"'<>?]+\.(?:png|jpe?g|gif|webp))", re.I)
GALLERY_RE = re.compile(r"/gallery/(\d{6,12})/?([^/?#\"'\s<>]*)")

# ---------------------------------------------------------------- réseau


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------- parsing


def _views_of(node):
    st = node.get("stats")
    if isinstance(st, dict):
        v = st.get("views")
        if isinstance(v, dict):
            v = v.get("all", v.get("total"))
        if isinstance(v, (int, float)):
            return int(v)
    return None


def _walk(node, out, seen):
    """Cherche récursivement des objets 'projet' dans un JSON d'état de page."""
    if isinstance(node, dict):
        url = node.get("url") if isinstance(node.get("url"), str) else ""
        m = GALLERY_RE.search(url)
        title = node.get("name") or node.get("title")
        if m and isinstance(title, str):
            owners = node.get("owners")
            owner_ok = True
            if isinstance(owners, list) and owners:
                names = [str(o.get("username", "")).lower() for o in owners if isinstance(o, dict)]
                owner_ok = (not names) or USERNAME.lower() in names
            im = IMG_RE.search(json.dumps(node))
            pid = m.group(1)
            if owner_ok and im and pid not in seen:
                seen.add(pid)
                out.append({"id": pid, "title": unescape(title).strip(), "slug": m.group(2),
                            "img": im.group(1), "views": _views_of(node)})
        for v in node.values():
            _walk(v, out, seen)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out, seen)


def parse_state_json(page):
    out, seen = [], set()
    for m in re.finditer(r"<script[^>]*type=[\"']application/json[\"'][^>]*>(.*?)</script>", page, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        _walk(data, out, seen)
    return out


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cur = None
        self.items = {}
        self.order = []

    def _rec(self, pid, slug):
        if pid not in self.items:
            self.items[pid] = {"id": pid, "slug": slug, "title": "", "img": "", "views": None}
            self.order.append(pid)
        return self.items[pid]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            m = GALLERY_RE.search(a.get("href") or "")
            if m:
                self.cur = m.group(1)
                r = self._rec(self.cur, m.group(2))
                if not r["title"] and a.get("title"):
                    r["title"] = a["title"].strip()
        elif tag in ("img", "source") and self.cur:
            src = " ".join(filter(None, [a.get("src"), a.get("srcset"), a.get("data-src")]))
            m = IMG_RE.search(src)
            if m:
                r = self.items[self.cur]
                if not r["img"]:
                    r["img"] = m.group(1)
                if not r["title"] and a.get("alt"):
                    r["title"] = a["alt"].strip()

    def handle_data(self, data):
        if self.cur and data.strip():
            r = self.items.get(self.cur)
            if r is not None and not r["title"]:
                r["title"] = data.strip()

    def handle_endtag(self, tag):
        if tag == "a":
            self.cur = None


def parse_html_anchors(page):
    p = _Anchors()
    p.feed(page)
    out = []
    for pid in p.order:
        r = p.items[pid]
        if r["img"]:
            if not r["title"]:
                r["title"] = r["slug"].replace("-", " ").strip() or pid
            out.append(r)
    return out


def parse_rss(xml_text):
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        m = GALLERY_RE.search(link)
        im = IMG_RE.search(unescape(it.findtext("description") or ""))
        if m and im:
            out.append({"id": m.group(1), "slug": m.group(2), "img": im.group(1),
                        "title": unescape((it.findtext("title") or "").strip()), "views": None})
    return out


def fetch_projects():
    """Essaie plusieurs sources dans l'ordre ; retourne la première liste non vide."""
    base = "https://www.behance.net/" + USERNAME
    attempts = [
        (base + "/projects", "state+html"),
        (base, "state+html"),
        ("https://www.behance.net/feeds/user?username=" + USERNAME, "rss"),
    ]
    for url, kind in attempts:
        try:
            page = get(url)
        except Exception as e:  # noqa: BLE001
            print(f"  [!] {url} -> {type(e).__name__}: {e}")
            continue
        if kind == "rss":
            try:
                found = parse_rss(page)
            except ET.ParseError as e:
                print(f"  [!] RSS illisible: {e}")
                continue
        else:
            found = parse_state_json(page) or parse_html_anchors(page)
        print(f"  {url}: {len(found)} projet(s) trouvé(s)")
        if found:
            return found
    return []


# ---------------------------------------------------------------- catégories

CAT_RULES = [
    ("g", r"\b(affiches?|posters?)\b"),
    ("m", r"\b(spot|animation|motion|vid[eé]o|film|reel|3d|jingle|teaser|clip|trailer)\b"),
    ("c", r"(brand|campagne|campaign|identity|identit|rebrand|pitch|logo)"),
    ("g", r"\b(banner|banni[eè]re|visuel|flyer|packaging|print|kv)\b"),
]


def guess_cat(title):
    t = title.lower()
    for cat, rx in CAT_RULES:
        if re.search(rx, t):
            return cat
    return "s"  # par défaut : Social Media


# ---------------------------------------------------------------- images


def _existing_files(pid):
    return glob.glob(os.path.join(IMG_DIR, f"{pid}.*"))


def download_all(projects):
    """Retélécharge TOUTES les couvertures. L'ancienne image n'est supprimée
    qu'une fois la nouvelle récupérée (pas de perte en cas d'échec réseau)."""
    os.makedirs(IMG_DIR, exist_ok=True)
    ok = fail = 0
    for p in projects:
        if not p.get("img"):
            continue
        ext = p["img"].rsplit(".", 1)[-1]
        dest = os.path.join(IMG_DIR, f"{p['id']}.{ext}")
        data = None
        for size in ("808", "404", "max_808"):
            try:
                req = urllib.request.Request(
                    f"https://mir-s3-cdn-cf.behance.net/projects/{size}/{p['img']}",
                    headers={"User-Agent": UA})
                data = urllib.request.urlopen(req, timeout=30).read()
                if data:
                    break
            except Exception:  # noqa: BLE001
                data = None
        if data:
            for old in _existing_files(p["id"]):  # supprime l'ancienne image (toute extension)
                if os.path.abspath(old) != os.path.abspath(dest):
                    os.remove(old)
            with open(dest, "wb") as fh:  # écrase la précédente
                fh.write(data)
            ok += 1
        else:
            fail += 1
            print(f"  [!] image introuvable pour {p['id']} (ancienne image conservée si elle existe)")
        time.sleep(0.2)
    print(f"Images : {ok} retéléchargée(s), {fail} échec(s)")


# ---------------------------------------------------------------- principal


def main():
    prune = "--prune" in sys.argv[1:]

    with open(JSON_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    old_projects = data["projects"]
    old_by_id = {p["id"]: p for p in old_projects}

    print(f"Lecture du profil Behance « {USERNAME} »…")
    found = fetch_projects()
    if not found:
        print("::error::Aucun projet récupéré depuis Behance (page bloquée ou structure changée).")
        return 1

    # On reconstruit la liste dans l'ordre exact de Behance
    rebuilt = []
    found_ids = set()
    for f in found:
        found_ids.add(f["id"])
        p = old_by_id.get(f["id"])
        if p is None:
            p = {"id": f["id"], "cat": guess_cat(f["title"]), "views": f.get("views") or 0,
                 "title": f["title"], "src_title": f["title"], "img": f["img"], "slug": f["slug"]}
            print(f"  + nouveau : {f['id']} · {f['title']} [{p['cat']}]")
        else:
            p = dict(p)
            if f.get("views") is not None:
                p["views"] = f["views"]
            if f["slug"]:
                p["slug"] = f["slug"]
            if f["img"] and f["img"] != p.get("img"):
                print(f"  ~ couverture changée : {f['id']} · {p.get('title', '')}")
            p["img"] = f["img"]

            # Titre : suivre Behance, sauf si tu l'as retouché à la main
            last = p.get("src_title")
            if last is None:
                p["src_title"] = f["title"]
            elif f["title"] and f["title"] != last:
                if p.get("title") == last:
                    p["title"] = f["title"]
                    print(f"  ~ titre mis à jour : {f['id']} · {f['title']}")
                p["src_title"] = f["title"]
        rebuilt.append(p)

    # Projets plus trouvés sur Behance
    missing = [p for p in old_projects if p["id"] not in found_ids]
    if prune:
        for p in missing:
            for fpath in _existing_files(p["id"]):
                os.remove(fpath)
            print(f"  - supprimé : {p['id']} · {p.get('title', '')}")
    else:
        if missing:
            print(f"  {len(missing)} projet(s) non trouvé(s) sur Behance, gardés à la fin "
                  f"(utilise --prune pour les supprimer)")
        rebuilt += missing

    projects = rebuilt
    by_id = {p["id"]: p for p in projects}

    if os.path.exists(OVERRIDES_PATH):
        with open(OVERRIDES_PATH, encoding="utf-8") as fh:
            for pid, patch in json.load(fh).items():
                if pid in by_id and isinstance(patch, dict):
                    by_id[pid].update({k: v for k, v in patch.items() if k in ("cat", "title", "views")})

    data["projects"] = projects
    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"projects.json mis à jour ({len(projects)} projets, ordre Behance)")

    download_all(projects)
    return 0


if __name__ == "__main__":
    sys.exit(main())
