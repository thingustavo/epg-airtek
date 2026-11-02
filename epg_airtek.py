#!/usr/bin/env python3
"""Genera guide.xml (XMLTV) desde la API de Airtek, enlazado a los tvg-id de tu M3U.

Uso:
    python3 epg_airtek.py lista.m3u            # genera guide.xml
    python3 epg_airtek.py lista.m3u --report   # solo muestra el emparejamiento
    python3 epg_airtek.py lista.m3u --logos    # además crea Playlist-con-logos.m3u (logos de la API)
"""
import difflib, json, re, sys, unicodedata, urllib.request
from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

API = "https://api.cloud.airteknet.io/api/v2/channels-manager-ms/epg/getGuide"
CHANNEL_IDS = [1,5,6,4,2,3,7,8,9,10,11,12,13,15,71,72,81,82,96,88,89,90,91,92,16,114,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,34,36,61,65,69,98,99,106,107,40,41,42,43,44,45,83,86,87,93,109,112,46,47,48,49,50,66,74,67,113,51,52,53,103,104,108,115,111,54,55,102,56,57,58,73,80,60,70,100,101,105,62,63,64,116,33,110]
HOURS, PAGE_HOURS = 30, 6
MIN_RATIO = 0.85

# Emparejamientos manuales para los que no coincidan solos:
# {"nombre exacto del canal en el M3U": channel_id_de_la_API}
# Usa None para dejar un canal sin guía.
OVERRIDES = {
    "History Channel 2 HD (1080)": 52,   # H2
    "Telehit musica plus": 57,           # TELEHIT MUSIC
    "MTV HITS (1080)": None,             # no existe en la API
    "TLNovelas": 55,                     # mismo stream que Telenovelas HD
}


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\((?:1080|720|4k)\)|\b(?:hd|fhd|uhd)\b", " ", s)
    return re.sub(r"[^a-z0-9+]", "", s)


def read_m3u(path):
    out = []
    for line in open(path, encoding="utf-8"):
        if line.startswith("#EXTINF"):
            m = re.search(r'tvg-id="([^"]*)"', line)
            out.append({"tvg_id": m.group(1) if m else "", "name": line.rsplit(",", 1)[1].strip()})
    return out


def fetch(page):
    body = json.dumps({"channel_ids": CHANNEL_IDS, "hours": HOURS,
                       "page_hours": PAGE_HOURS, "page": page}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get_guide():
    chans = {}
    for page in range(HOURS // PAGE_HOURS):
        for ch in fetch(page)["channels"]:
            c = chans.setdefault(ch["channel_id"], {"name": ch["channel_name"], "progs": {},
                                                    "icon": ch.get("epg_icon_url") or ""})
            for p in ch["programmes"]:
                c["progs"][p["start_at"]] = p
    return chans


def match(entries, chans):
    by_norm = {norm(c["name"]): cid for cid, c in chans.items()}
    result = {}
    for e in entries:
        if e["name"] in OVERRIDES:
            if OVERRIDES[e["name"]] is not None:
                result[e["name"]] = (OVERRIDES[e["name"]], "manual")
            continue
        for key in (norm(e["name"]), norm(e["tvg_id"])):
            if key and key in by_norm:
                result[e["name"]] = (by_norm[key], "exacto"); break
        else:
            digits = lambda x: re.sub(r"\D", "", x)   # ESPN2 no puede ser ESPN
            cands = [k for k in difflib.get_close_matches(norm(e["name"]), by_norm, n=3, cutoff=MIN_RATIO)
                     if digits(k) == digits(norm(e["name"]))]
            if cands:
                result[e["name"]] = (by_norm[cands[0]], "parecido")
    return result


def write_m3u_logos(path, res, chans, out_path="Playlist-con-logos.m3u"):
    """Pone en tvg-logo el logo de la API para todos los canales emparejados que lo tengan."""
    lines, changed = open(path, encoding="utf-8", newline="").read().split("\n"), 0
    for i, l in enumerate(lines):
        if not l.startswith("#EXTINF"):
            continue
        name = l.rsplit(",", 1)[1].strip()
        icon = chans[res[name][0]].get("icon") if name in res else ""
        if not icon:
            continue
        m = re.search(r'tvg-logo="([^"]*)"', l)
        new = (l[:m.start()] + f'tvg-logo="{icon}"' + l[m.end():]) if m else \
              l.replace("#EXTINF:-1", f'#EXTINF:-1 tvg-logo="{icon}"', 1)
        lines[i], changed = new, changed + 1
    open(out_path, "w", encoding="utf-8", newline="").write("\n".join(lines))
    print(f"{out_path}: {changed} canales con logo de la API")


def xt(iso):
    return datetime.fromisoformat(iso).strftime("%Y%m%d%H%M%S %z")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    entries, chans = read_m3u(sys.argv[1]), get_guide()
    res = match(entries, chans)

    if "--logos" in sys.argv:
        write_m3u_logos(sys.argv[1], res, chans)
    print(f"API: {len(chans)} canales | M3U: {len(entries)} canales | emparejados: {len(res)}")
    for e in entries:
        if e["name"] in res:
            cid, how = res[e["name"]]
            if how != "exacto":
                print(f"  [{how}] {e['name']}  ->  {chans[cid]['name']} (id {cid})")
        else:
            print(f"  [SIN EMPAREJAR] {e['name']}")
    if "--report" in sys.argv:
        print("\nCanales de la API:", ", ".join(f"{i}={c['name']}" for i, c in sorted(chans.items())))
        return

    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="airtek-epg">']
    used = {}   # id XMLTV -> channel_id de la API
    for e in entries:
        if e["name"] not in res:
            continue
        cid = res[e["name"]][0]
        xid = e["tvg_id"] or e["name"]          # sin tvg-id: usa el nombre
        if xid in used and used[xid] != cid:    # mismo tvg-id para otro canal
            print(f"  AVISO: '{e['name']}' comparte tvg-id '{xid}' con otro canal. "
                  f"Su guía queda con id '{e['name']}': pon ese valor como tvg-id.")
            xid = e["name"]
        if xid in used:
            continue
        used[xid] = cid
        icon = f'<icon src={quoteattr(chans[cid]["icon"])}/>' if chans[cid].get("icon") else ""
        out.append(f'<channel id={quoteattr(xid)}><display-name>{escape(e["name"])}</display-name>'
                   f'<display-name>{escape(chans[cid]["name"])}</display-name>{icon}</channel>')
        for start in sorted(chans[cid]["progs"]):
            p = chans[cid]["progs"][start]
            out.append(f'<programme start="{xt(p["start_at"])}" stop="{xt(p["end_at"])}" channel={quoteattr(xid)}>')
            out.append(f'  <title lang="es">{escape(p["title"])}</title>')
            if p.get("description"):
                out.append(f'  <desc lang="es">{escape(p["description"])}</desc>')
            for c in dict.fromkeys(p.get("categories") or []):
                out.append(f'  <category lang="es">{escape(c)}</category>')
            if p.get("icon_url"):
                out.append(f'  <icon src={quoteattr(p["icon_url"])}/>')
            out.append("</programme>")
    out.append("</tv>")
    open("guide.xml", "w", encoding="utf-8").write("\n".join(out))
    print(f"\nguide.xml generado con {len(used)} canales.")


if __name__ == "__main__":
    main()
