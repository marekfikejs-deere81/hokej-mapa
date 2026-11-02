#!/usr/bin/env python3
"""Stáhne zápasy HC Rytíři Vlašim (ročníky 2019, 2018, 2017) ze zapasy.ceskyhokej.cz
a uloží je do games.json, který čte mapa (index.html). Jen standardní knihovna Pythonu."""
import json, re, sys, time, difflib, unicodedata, urllib.request
from html.parser import HTMLParser
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEAM_ID = 2321            # HC Rytíři Vlašim
SEASON = 2026
OWN_CLUB = "20109"
YEARS = {                  # ročník -> (soutěž ID, popis)
    "2019": (143, "2. třída"),
    "2018": (144, "3. třída"),
    "2017": (145, "4. třída"),
}
BASE = "https://zapasy.ceskyhokej.cz/seznam-zapasu"

def url(league, start=0):
    q = (f"?filter%5Bseason%5D={SEASON}&filter%5BdateRange%5D=&filter%5BmanagingAuthorities%5D=all"
         f"&filter%5Bregion%5D=2&filter%5Bstadium%5D=all&filter%5BtimeShortcut%5D=this-season"
         f"&filter%5Bleague%5D={league}&filter%5BleaguePart%5D=all&filter%5Bnumber%5D=&filter%5Bstate%5D="
         f"&filter%5Bteam%5D={TEAM_ID}&filter%5BteamType%5D=all&filter%5Bsort%5D=&filter%5Bdirection%5D=ASC")
    if start:
        q += f"&start={start}&count=30&actionType=default&do=more"
    return BASE + q

class RowParser(HTMLParser):
    """Vytáhne texty buněk z řádků v <tbody id="snippet--matchesSnippet">."""
    def __init__(self):
        super().__init__()
        self.inbody = 0; self.row = None; self.cell = None; self.rows = []
    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == "tbody" and a.get("id") == "snippet--matchesSnippet": self.inbody = 1
        elif self.inbody and tag == "tr": self.row = []
        elif self.row is not None and tag == "td": self.cell = []
    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip()); self.cell = None
        elif tag == "tr" and self.row is not None:
            if len(self.row) >= 11: self.rows.append(self.row)
            self.row = None
        elif tag == "tbody" and self.inbody: self.inbody = 0
    def handle_data(self, d):
        if self.cell is not None: self.cell.append(d)

def fetch(u):
    req = urllib.request.Request(u, headers={"User-Agent": "hokej-mapa updater (GitHub Actions)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if attempt == 2: raise
            time.sleep(5)

def fetch_rows(league):
    rows, start = [], 0
    while start < 600:
        p = RowParser(); p.feed(fetch(url(league, start)))
        rows += p.rows
        if len(p.rows) < 30: break
        start += 30
    return rows

def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

def matcher():
    clubs = json.loads((ROOT / "data/clubs_cz.json").read_text("utf-8"))
    alias = {k: v for k, v in json.loads((ROOT / "data/aliases.json").read_text("utf-8")).items() if not k.startswith("_")}
    by = {norm(c["n"]): c["id"] for c in clubs}
    def find(name):
        if name in alias: return alias[name]
        n = norm(name)
        if n in by: return by[n]
        m = difflib.get_close_matches(n, list(by), n=1, cutoff=0.85)
        return by[m[0]] if m else None
    return find

def build(rows_by_year):
    find = matcher(); out = {}; unmatched = set()
    for y, rows in rows_by_year.items():
        games = {}
        for c in rows:
            # sloupce: 0 check,1 den,2 datum,3 začátek,4 ZS,5 soutěž,6 kolo,7 ČU,8 domácí,9 hosté,10 stav
            home, away = c[8], c[9]
            b = home.endswith(" B") and away.endswith(" B")
            h, a = re.sub(r" B$", "", home), re.sub(r" B$", "", away)
            hid, aid = find(h), find(a)
            if hid == OWN_CLUB: opp, oid, ha = a, aid, "doma"
            elif aid == OWN_CLUB: opp, oid, ha = h, hid, "venku"
            else: continue
            if not oid: unmatched.add(opp); continue
            state = c[10]
            key = (c[2], oid)
            g = games.get(key)
            if not g:
                g = games[key] = {"d": c[2], "t": c[3], "ha": ha, "club": oid, "opp": opp, "zs": c[4],
                                  "cu": [], "b": False, "x": ""}
            g["cu"].append(c[7]); g["b"] = g["b"] or b
            if "zrušen" in state.lower() and not b: g["x"] = "zrušen"
        def sk(g):
            d = g["d"][:10]; return d[6:10] + d[3:5] + d[0:2]
        out[y] = {"league": YEARS[y][0], "label": YEARS[y][1], "games": sorted(games.values(), key=sk)}
    return out, sorted(unmatched)

def main():
    rows_by_year = {}
    for y, (league, _) in YEARS.items():
        rows_by_year[y] = fetch_rows(league)
        print(f"{y}: {len(rows_by_year[y])} řádků", file=sys.stderr)
    years, unmatched = build(rows_by_year)
    if sum(len(v["games"]) for v in years.values()) == 0:
        sys.exit("Žádné zápasy – stránka se asi změnila, games.json nepřepisuji.")
    path = ROOT / "games.json"
    old = json.loads(path.read_text("utf-8")) if path.exists() else {}
    data = {"season": "2026/27", "source": BASE, "years": years, "unmatched": unmatched}
    if {k: v for k, v in old.items() if k != "updated"} == data:
        print("Beze změny."); return
    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    print("games.json aktualizován.", "Nespárované týmy: " + ", ".join(unmatched) if unmatched else "")

if __name__ == "__main__":
    main()
