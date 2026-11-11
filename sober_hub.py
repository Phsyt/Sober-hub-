#!/usr/bin/env python3
"""Sober Hub - a Roblox-style launcher and companion for Sober (Roblox on Linux).

A single file: a standard-library Python backend plus an embedded HTML/CSS/JS front end.

    python3 sober_hub.py              native window if pywebview is installed, else a browser app window
    python3 sober_hub.py --browser    always use the browser window
    python3 sober_hub.py --install    add a desktop shortcut
    python3 sober_hub.py --no-update  skip the GitHub update check for this run

Layout
    Python    helpers for Roblox/Sober/data storage, then the R dict: every entry is a JSON route served at /api/<name>
    HTML      CSS, then one <script>; pages are functions in PAGES, post-render hooks in AFTER, `go(tab)` switches page

Data and security model
    - Hub data lives in ~/.config/sober-hub/data.json (mode 600). Sober's own config.json is edited in place.
    - The server binds to 127.0.0.1 only and rejects requests whose Host/Origin is not local.
    - The .ROBLOSECURITY cookie is stored locally, attached only to *.roblox.com, and never exported or sent to the UI.
    - Third-party endpoints (Rolimons, a user-supplied recommendations API) never receive the cookie.

Auto update
    - On start (and every 6 hours) the hub reads VERSION from sober_hub.py in the GitHub repo. If the repo's version is higher, the file is
      downloaded, syntax-checked, and written over this one (the old file is kept as sober_hub.py.bak). To publish an update, bump VERSION below
      and push the file. The cookie is never sent to GitHub.

Login
    - Quick Login: the hub shows a code, you confirm it on roblox.com while logged in there (the same flow Roblox uses to sign in on a TV).
    - Cookie: paste a .ROBLOSECURITY cookie.
"""
import ast, base64, json, math, os, re, shlex, sys, shutil, subprocess, threading, time, urllib.parse, urllib.request, urllib.error, uuid, webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

VERSION = "5.6.0"   # bump this and push the file to GitHub to roll out an update
WHATS_NEW = [   # shown in the "Sober Hub updated automatically!" popup after an update; edit this list for each release (keep each line under 200 characters)
    "New Server hop: jump to a different public server (skips your recent ones and full or empty ones, prefers low ping) from the game page, the game right-click menu and Home",
    "New Play later list: add games from the right-click menu or the game page. They show on Home and in Library and leave the list when you play them",
    "New Healthy play settings: break reminders every 30 to 120 minutes and an optional daily play limit, with a pop-up and a desktop alert",
    "New Mutual friends on every profile you look up, with a count in the profile header",
    "New friend nicknames: right-click a friend > Set nickname. Shown in Friends, Home and notifications; the real name stays on their card",
    "New Health check in Hub Settings: tests Sober, your Roblox session, the connection and the optional tools (ffmpeg, desktop alerts) and tells you what to fix",
    "Block / Unblock removed: Roblox rejects it when it comes from outside the app. Blocked users can still be managed on roblox.com (Roblox Settings > Privacy)",
]
CHANGELOG_OLD = [   # the previous version's notes, shown under Settings > Updates
    "Friends now show when they were last online: Sober Hub remembers the last time it saw each friend online (tracked while the hub is open). Shown on Friends, the friend card and Profile",
    "If a friend went offline while playing, you also see which game they were in (\"was playing Adopt Me!\"). Profile also shows the last game seen",
    "Offline friends are sorted by who was online most recently",
    "New Recent activity feed on the Friends page: who came online, started a game or went offline, with times. You can clear it any time",
    "Last-online tracking keeps running even when friend notifications are switched off",
    "New Quiet hours in Settings: no friend pop-ups, chimes or desktop alerts during the hours you pick",
    "Friends CSV export now includes last online time and last game",
    "Custom death sound fixed: the hub checks the audio format (Ogg Vorbis for ouch.ogg), refuses Opus and WAV files with a clear reason, and never installs a file under the wrong format",
    "Installing a custom death sound warns you if the built-in old-oof switch is on, because the two can conflict",
    "New update log: Settings > Updates lists what is new in this version and the previous one",
]
REPO_RAW = "https://raw.githubusercontent.com/Phsyt/Sober-hub-/main/sober_hub.py"
ARGV = list(sys.argv)   # sys.argv[0] is overwritten later for the window class, so keep the real command line for restarts
D = os.path.expanduser("~/.config/sober-hub"); F = D + "/data.json"
SC = os.path.expanduser("~/.var/app/org.vinegarhq.Sober/config/sober/config.json")
APP = "org.vinegarhq.Sober"
DEF = {"user": None, "favs": [], "history": [], "play": {}, "notes": {}, "css": "", "daily": {}, "pinned": [], "collections": {}, "hidden_games": [], "hidden_chat": {}, "privs": [], "upd_notice": None, "nicks": {}, "later": [], "capi": {"url": "", "method": "GET", "headers": "", "body": "", "path": ""},
       "opts": {"cmd": "flatpak run " + APP, "recs": "better", "hero": "hub", "notify": "pinned", "autoupd": "1", "nsound": "1", "nonline": "1", "ndesk": "1", "backhub": "1", "qon": "0", "qs": "23:00", "qe": "07:00", "brk": "0", "brkmin": "60", "lim": "0"},
       "theme": {"bg": "#1a1210", "panel": "#271a16", "accent": "#ff8a3d", "green": "#ff8a3d", "text": "#fff0e6", "radius": "8", "font": "system-ui", "tile": "150", "compact": "0"}}
OLD_THEME = {"bg": "#18191d", "panel": "#232527", "accent": "#335fff", "green": "#00b06f", "text": "#f7f7f8"}   # the previous default (the "Roblox Dark" preset)
lock = threading.Lock()
ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="6 6 222 222"><path fill="#FDD758" fill-rule="evenodd" d="M57.0 16.0 L218.0 58.0 L172.0 219.0 L15.0 177.0ZM106.4 99.9 L135.1 107.4 L127.6 136.1 L98.9 128.6Z"/></svg>"""
WMCLASS = "SoberHub"
WIN = [None]   # the pywebview window, when there is one
CLK = threading.Lock(); COL = {"n": 0}   # close_on_leave bookkeeping for games launched from the hub
ACTIVE = {}   # place id -> {"name", "t0"} for games launched from the hub that are still running

def chunks(l, n):
    """Yield n-sized slices of l; Roblox endpoints cap how many ids one request may carry."""
    for i in range(0, len(l), n): yield l[i:i + n]

NAMES = {}   # user id -> (username, display name), cache for blank friend-list names (also kept in names.json between runs)
NF = D + "/names.json"
def _nload():
    try:
        for k, v in json.load(open(NF)).items(): NAMES.setdefault(int(k), (v[0], v[1]))
    except Exception: pass
def _nsave():
    try:
        os.makedirs(D, mode=0o700, exist_ok=True); tmp = NF + ".tmp"
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: json.dump({str(k): list(v) for k, v in NAMES.items()}, f)
        os.replace(tmp, NF)
    except Exception: pass
_nload()
def names(fl, singles=40):
    """The friends list can come back with blank names, so missing ones are resolved through the users API (cached, retried, saved to disk)."""
    need = [f["id"] for f in fl if not (f.get("name") and f.get("displayName")) and f["id"] not in NAMES]
    if need: resolve(need, singles)
    out = {}
    for f in fl:
        n, dn = f.get("name") or "", f.get("displayName") or ""
        if not (n and dn):
            n2, d2 = NAMES.get(f["id"], ("", "")); n = n or n2; dn = dn or d2 or n
        out[f["id"]] = (n or "User " + str(f["id"]), dn or n or "User " + str(f["id"]))
    return out

def load():
    """Read data.json. A corrupt file is copied to data.json.bak and replaced by defaults; missing keys get defaults."""
    try: d = json.load(open(F)) if os.path.exists(F) else {}
    except Exception:
        try: shutil.copy(F, F + ".bak")
        except Exception: pass
        d = {}
    for k, v in DEF.items():
        d.setdefault(k, json.loads(json.dumps(v)))
        if k in ("opts", "theme"):
            for kk, vv in v.items(): d[k].setdefault(kk, vv)
    if not d.get("sunset_default"):
        d["sunset_default"] = True
        if all(d["theme"].get(k) == v for k, v in OLD_THEME.items()):
            for k in OLD_THEME: d["theme"][k] = DEF["theme"][k]
    return d
def store(d):
    """Write data.json atomically (temp file + rename) with owner-only permissions."""
    os.makedirs(D, mode=0o700, exist_ok=True)
    tmp = F + ".tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: json.dump(d, f, indent=1)
    os.replace(tmp, F)

class _NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None   # a redirect could carry the cookie to another host, so none are followed
OP = urllib.request.build_opener(_NR)

def req(url, body=None, method=None, timeout=12, ref=None):
    """Call a JSON API and return the parsed body. The session cookie is attached only to *.roblox.com URLs; a 403 is retried once
    with the x-csrf-token Roblox returns. Failures raise Exception(<Roblox's own error message>)."""
    host = urllib.parse.urlparse(url).hostname or ""
    ck = load().get("cookie") if host.endswith(".roblox.com") else None
    def go(tok=None):
        h = {"User-Agent": "SoberHub", "Content-Type": "application/json"}
        if ck: h["Cookie"] = ".ROBLOSECURITY=" + ck
        if ck and (ref or host in ("apis.roblox.com", "accountsettings.roblox.com")): h["Origin"] = "https://www.roblox.com"; h["Referer"] = ref or "https://www.roblox.com/"
        if tok: h["x-csrf-token"] = tok
        r = urllib.request.Request(url, headers=h, method=method, data=json.dumps(body).encode() if body is not None else None)
        t = OP.open(r, timeout=timeout).read()
        return json.loads(t) if t else {}
    try:
        try: return go()
        except urllib.error.HTTPError as e:
            if e.code == 403 and e.headers.get("x-csrf-token"): return go(e.headers["x-csrf-token"])
            raise
    except urllib.error.HTTPError as e:
        m = None
        try:
            j = json.loads(e.read()); er = j.get("errors")
            m = (er[0].get("message") if er else None) or j.get("message") or j.get("errorMsg") or j.get("reason") or j.get("title")
        except Exception: pass
        if e.code == 403 and e.headers.get("rblx-challenge-id"): m = "Roblox wants a verification challenge for this action (HTTP 403)"
        m = m or f"Roblox returned HTTP {e.code}"
        raise Exception(m)

def thumbs(kind, ids, size="150x150"):
    """Batch thumbnail lookup (100 ids per request) -> {targetId: imageUrl}. kind is e.g. 'users/avatar-headshot' or 'games/icons'."""
    out = {}
    for i in range(0, len(ids), 100):
        r = req(f"https://thumbnails.roblox.com/v1/{kind}?{'universeIds' if 'games' in kind else 'userIds'}={','.join(map(str, ids[i:i+100]))}&size={size}&format=Png")
        for t in r["data"]: out[t["targetId"]] = t.get("imageUrl")
    return out

def meta(places):
    """Icon + name for place ids (works even for games we only know by place id)."""
    ids = sorted({int(p) for p in places if str(p).isdigit()})[:100]
    out = {i: {} for i in ids}
    if not ids: return out
    try:
        r = req(f"https://thumbnails.roblox.com/v1/places/gameicons?placeIds={','.join(map(str, ids))}&returnPolicy=PlaceHolder&size=150x150&format=Png")
        for t in r["data"]:
            if t.get("imageUrl"): out[t["targetId"]]["icon"] = t["imageUrl"]
    except Exception: pass
    try:
        r = req("https://games.roblox.com/v1/games/multiget-place-details?" + "&".join(f"placeIds={i}" for i in ids))
        for x in r:
            if x.get("name"): out[x["placeId"]]["name"] = x["name"]
    except Exception: pass
    return out

def heal(places):
    """Fill in missing logos/names on everything we stored (history, favorites, playtime, collections)."""
    m = meta(places)
    with lock:
        d = load(); ch = False
        def fix(e, p):
            nonlocal ch
            try: x = m.get(int(p), {})
            except Exception: return
            for k in ("icon", "name"):
                if x.get(k) and not e.get(k): e[k] = x[k]; ch = True
        for e in d["history"] + d["favs"]: fix(e, e.get("place"))
        for p, e in d["play"].items(): fix(e, p)
        for l in d["collections"].values():
            for e in l: fix(e, e.get("place"))
        if ch: store(d)
    return {"ok": True}

def ginfo(pid):
    """Details card for a place: resolves its universe, then game info, vote ratio and icon."""
    u = req(f"https://apis.roblox.com/universes/v1/places/{pid}/universe")["universeId"]
    g = req(f"https://games.roblox.com/v1/games?universeIds={u}")["data"][0]
    v = req(f"https://games.roblox.com/v1/games/votes?universeIds={u}")["data"][0]
    t = v["upVotes"] + v["downVotes"]
    return {"uid": u, "place": int(pid), "name": g["name"], "playing": g["playing"], "visits": g["visits"], "favs": g["favoritedCount"],
            "maxp": g["maxPlayers"], "creator": g["creator"]["name"], "updated": g["updated"][:10],
            "like": round(v["upVotes"] / t * 100) if t else None, "desc": (g.get("description") or "")[:400],
            "icon": thumbs("games/icons", [u]).get(u)}

def search(a):
    """Omni-search for games -> up to 36 of {place, name, playing, uid, icon}."""
    r = req(f"https://apis.roblox.com/search-api/omni-search?searchQuery={urllib.parse.quote(a['q'])}&sessionId={uuid.uuid4()}&pageType=all")
    out = [{"place": c["rootPlaceId"], "name": c.get("name"), "playing": c.get("playerCount", 0), "uid": c.get("universeId")}
           for g in r.get("searchResults", []) for c in g.get("contents", []) if c.get("rootPlaceId")][:36]
    try:
        ic = thumbs("games/icons", [o["uid"] for o in out if o["uid"]])
        for o in out: o["icon"] = ic.get(o["uid"])
    except Exception: pass
    return out

RM = {"t": 0, "games": []}     # Rolimons game list; a heavy endpoint, so it is cached for 15 minutes
UC = {}                        # place id -> {"uid", "genre", "like", "votes"}; filled lazily, lives for the session
BR = {"t": 0, "d": None}       # finished "better recommendations" result, cached for 5 minutes

def rolimons():
    """Community-made list of every tracked game (name, player count, icon). Tries both known endpoints; serves a stale cache if both fail."""
    if RM["games"] and time.time() - RM["t"] < 900: return RM["games"]
    r = None
    for u in ("https://api.rolimons.com/games/v1/gamelist", "https://www.rolimons.com/gameapi/gamelist"):
        try: r = req(u, timeout=30)
        except Exception: continue
        if r and r.get("games"): break
    if not r or not r.get("games"):
        if RM["games"]: return RM["games"]
        raise Exception("Rolimons game list unavailable")
    RM["t"] = time.time()
    RM["games"] = [{"place": int(k), "name": v[0], "playing": v[1], "icon": v[2] if len(v) > 2 else None}
                   for k, v in r["games"].items() if len(v) > 1 and isinstance(v[1], int) and str(k).isdigit()]
    return RM["games"]

def enrich(places):
    """Fill UC with genre and like ratio per place using Roblox's own APIs (the community list only has name/players/icon)."""
    need = sorted({int(p) for p in places if int(p) not in UC})
    pu = {}
    def a(c):
        try: return req("https://games.roblox.com/v1/games/multiget-place-details?" + "&".join(f"placeIds={i}" for i in c))
        except Exception: return []
    with ThreadPoolExecutor(4) as ex:
        for rows in ex.map(a, chunks(need, 50)):
            for x in rows:
                if x.get("universeId"): pu[x["placeId"]] = x["universeId"]
    uids = sorted(set(pu.values())); info = {}
    def b(c):
        s = ",".join(map(str, c))
        try: g = req("https://games.roblox.com/v1/games?universeIds=" + s)["data"]
        except Exception: g = []
        try: v = req("https://games.roblox.com/v1/games/votes?universeIds=" + s)["data"]
        except Exception: v = []
        return g, v
    with ThreadPoolExecutor(4) as ex:
        for g, v in ex.map(b, chunks(uids, 50)):
            for x in g: info.setdefault(x["id"], {})["genre"] = x.get("genre_l1") or x.get("genre") or "All"
            for x in v:
                t = x["upVotes"] + x["downVotes"]; e = info.setdefault(x["id"], {})
                e["votes"] = t; e["like"] = x["upVotes"] / t if t else None
    for p, u in pu.items(): UC[p] = {"uid": u, **info.get(u, {})}

def better_recs():
    """Built-in "better recommendations" from the Rolimons list.

    Sections: Picked for you (candidates ranked by genre match with your favourites/playtime/history, like ratio and a small
    popularity term), Trending now, Hidden gems (mid-size games with >=85% likes) and More <your top genre>. All ranking runs
    locally; nothing about you is sent to Rolimons. A game appears in only one section."""
    if BR["d"] and time.time() - BR["t"] < 300: return BR["d"]
    d = load(); hid = {str(x) for x in d["hidden_games"]}
    def pi(x):
        try: return int(x)
        except Exception: return None
    gl = sorted((g for g in rolimons() if str(g["place"]) not in hid and g["playing"] >= 30), key=lambda g: -g["playing"])
    top = gl[:40]; mid = [g for g in gl[40:] if 150 <= g["playing"] <= 6000][:120]
    w = {}   # place -> taste weight: favourites count 3, playtime up to 5, recent launches 1 each
    for e in d["favs"]:
        p = pi(e.get("place"))
        if p: w[p] = w.get(p, 0) + 3
    for k, e in d["play"].items():
        p = pi(k)
        if p: w[p] = w.get(p, 0) + min(5, 1 + e.get("secs", 0) / 3600)
    for e in d["history"][:40]:
        p = pi(e.get("place"))
        if p: w[p] = w.get(p, 0) + 1
    taste = dict(sorted(w.items(), key=lambda kv: -kv[1])[:40])
    try: enrich([g["place"] for g in top + mid] + list(taste))
    except Exception: pass
    gw = {}
    for p, wt in taste.items():
        g = (UC.get(p) or {}).get("genre")
        if g and g != "All": gw[g] = gw.get(g, 0) + wt
    mx = max(gw.values()) if gw else 1
    aff = {g: v / mx for g, v in gw.items()}
    def q(g):
        i = UC.get(g["place"]) or {}; l = i.get("like")
        return 0.7 if l is None else (l if i.get("votes", 0) >= 100 else l * 0.5 + 0.35)
    def card(g):
        l = (UC.get(g["place"]) or {}).get("like")
        return {"place": g["place"], "name": g["name"], "icon": g["icon"], "playing": g["playing"], "like": round(l * 100) if l is not None else None}
    seen, out = set(), []
    def add(name, items):
        items = [g for g in items if g["place"] not in seen][:18]
        if items:
            seen.update(g["place"] for g in items); out.append({"name": name, "games": [card(g) for g in items]})
    pool = [g for g in top + mid if g["place"] not in taste]
    if aff:
        sc = lambda g: 0.5 * aff.get((UC.get(g["place"]) or {}).get("genre"), 0) + 0.35 * (q(g) - 0.5) * 2 + 0.15 * math.log10(max(g["playing"], 10)) / 5
        add("✨ Picked for you", sorted((g for g in pool if q(g) >= 0.7), key=lambda g: -sc(g)))
    add("🔥 Trending now", top)
    add("💎 Hidden gems", sorted((g for g in mid if g["place"] not in taste and q(g) >= 0.85 and (UC.get(g["place"]) or {}).get("votes", 0) >= 150),
                                 key=lambda g: -(q(g) * math.log10(max(g["playing"], 10)))))
    if aff:
        tg = max(aff, key=aff.get)
        add(f"🎯 More {tg}", sorted((g for g in top + mid if (UC.get(g["place"]) or {}).get("genre") == tg and g["place"] not in taste), key=lambda g: -g["playing"]))
    if not out: raise Exception("no recommendations")
    BR.update(t=time.time(), d=out)
    return out

CR = {"t": 0, "d": None}       # custom API cache (60 s)

def _num(x):
    """int(x) or None; booleans are rejected because JSON true would otherwise become 1."""
    if isinstance(x, bool): return None
    try: return int(x)
    except Exception: return None

def _norm_game(g):
    """Map one API game (a dict with any common key spelling, or a bare place id) to {place, name, icon, playing, like}; None if it has no place id."""
    if not isinstance(g, dict):
        p = _num(g); return {"place": p} if p else None
    p = None
    for k in ("place", "placeId", "rootPlaceId", "place_id", "id"):
        if g.get(k) is not None and _num(g[k]): p = _num(g[k]); break
    if not p: return None
    like = next((g[k] for k in ("like", "likes", "rating", "like_ratio") if isinstance(g.get(k), (int, float))), None)
    if like is not None and like <= 1: like *= 100
    pl = next((g[k] for k in ("playing", "playerCount", "players", "player_count") if isinstance(g.get(k), (int, float))), 0)
    return {"place": p, "name": g.get("name") or g.get("title") or "", "icon": g.get("icon") or g.get("imageUrl") or g.get("thumbnail") or "",
            "playing": int(pl), "like": round(like) if like is not None else None}

def _norm_sections(r, path):
    """Turn a custom-API response into [{name, games}]. Accepts a list, {"games"|"data"|"results"|"items": [...]} or {"sections": [...]}; `path` is an optional dotted path to the list."""
    for k in filter(None, (path or "").strip().split(".")):
        try: r = r[int(k)] if isinstance(r, list) else r[k]
        except Exception: raise Exception(f"Path '{path}' not found in the response")
    if isinstance(r, dict):
        if isinstance(r.get("sections"), list): r = r["sections"]
        else:
            for k in ("games", "data", "results", "items", "recommendations"):
                if isinstance(r.get(k), list): r = r[k]; break
    if not isinstance(r, list): raise Exception("Couldn't find a list of games in the response. Set 'Path to list' (e.g. data.games).")
    gk = lambda s: next((s[k] for k in ("games", "items") if isinstance(s.get(k), list)), None)
    if r and all(isinstance(s, dict) and gk(s) is not None for s in r):
        secs = [{"name": s.get("name") or s.get("title") or "Recommended", "games": gk(s)} for s in r]
    else: secs = [{"name": "Recommended", "games": r}]
    out = []
    for s in secs:
        gs = [x for x in (_norm_game(g) for g in s["games"][:36]) if x]
        if gs: out.append({"name": str(s["name"])[:60], "games": gs})
    if not out: raise Exception("The response had no games with a place ID (looked for place / placeId / rootPlaceId / id).")
    need = [g["place"] for s in out for g in s["games"] if not (g["name"] and g["icon"])]
    if need:
        m = meta(need[:100])
        for s in out:
            for g in s["games"]:
                x = m.get(g["place"], {}); g["name"] = g["name"] or x.get("name") or str(g["place"]); g["icon"] = g["icon"] or x.get("icon") or ""
    return out

def custom_recs(force=False):
    """Fetch recommendations from the user-configured API (GET or POST) and normalise them. Placeholders {userId} {username} {history} {favs} are filled in; the Roblox cookie is never sent."""
    if CR["d"] and not force and time.time() - CR["t"] < 60: return CR["d"]
    d = load(); c = d.get("capi") or {}; url = (c.get("url") or "").strip()
    if not url.startswith(("http://", "https://")): raise Exception("Enter an http:// or https:// URL")
    u = d.get("user") or {}
    sub = {"userId": u.get("id", ""), "username": u.get("name", ""), "history": ",".join(str(h["place"]) for h in d["history"][:20]),
           "favs": ",".join(str(f.get("place")) for f in d["favs"][:20])}
    def fill(s, q):
        for k, v in sub.items(): s = s.replace("{" + k + "}", urllib.parse.quote(str(v)) if q else str(v))
        return s
    post = c.get("method") == "POST"
    h = {"User-Agent": "SoberHub", "Accept": "application/json"}
    for line in (c.get("headers") or "").splitlines():
        if ":" in line: k, v = line.split(":", 1); h[k.strip()] = v.strip()
    data = None
    if post:
        data = fill(c.get("body") or "", False).encode()
        if not any(k.lower() == "content-type" for k in h): h["Content-Type"] = "application/json"
    # the Roblox cookie is never attached here, and redirects are not followed
    try: raw = OP.open(urllib.request.Request(fill(url, True), data=data, headers=h, method="POST" if post else "GET"), timeout=20).read(8_000_000)
    except urllib.error.HTTPError as e: raise Exception(f"Your API returned HTTP {e.code}" + (" (redirects are not followed - use the final URL)" if 300 <= e.code < 400 else ""))
    except Exception as e: raise Exception("Could not reach your API: " + str(getattr(e, "reason", e)))
    try: r = json.loads(raw)
    except Exception: raise Exception("Your API did not return JSON")
    out = _norm_sections(r, c.get("path"))
    CR.update(t=time.time(), d=out)
    return out

def capi_test(a):
    """Settings-page 'Save & test' button: bypass the cache and report what the custom API returned."""
    out = custom_recs(True)
    return {"ok": True, "sections": len(out), "games": sum(len(s["games"]) for s in out), "first": out[0]["games"][0]["name"]}

def discover(a):
    """Discover page data. Source depends on opts.recs: custom API -> built-in better recs -> Roblox; each falls back to the next on failure."""
    mode = (load().get("opts") or {}).get("recs", "better")
    if mode == "custom":
        try: return custom_recs()
        except Exception: pass     # your API failed -> fall back to the built-in better recommendations
    if mode in ("better", "custom"):
        try: return better_recs()
        except Exception: pass     # community API down -> fall back to Roblox's own
    return roblox_discover(a)

def roblox_discover(a):
    """Roblox's own Discover sorts (the 'normal' recommendations)."""
    sid, base, out = str(uuid.uuid4()), "https://apis.roblox.com/explore-api/v1/", []
    for s in req(base + f"get-sorts?sessionId={sid}&device=computer&country=all").get("sorts", [])[:10]:
        try:
            gs = req(base + f"get-sort-content?sessionId={sid}&sortId={s['sortId']}&device=computer&country=all").get("games", [])[:18]
            if not gs: continue
            ic = thumbs("games/icons", [g["universeId"] for g in gs])
            out.append({"name": s.get("sortDisplayName", "Games"), "games": [
                {"place": g["rootPlaceId"], "name": g["name"], "icon": ic.get(g["universeId"]), "playing": g.get("playerCount", 0),
                 "like": round(g["totalUpVotes"] / (g["totalUpVotes"] + g["totalDownVotes"]) * 100) if g.get("totalUpVotes") else None} for g in gs]})
        except Exception: continue
    return out

def servers(a):
    """One page of public servers for a place, with the cursor for the next page."""
    sort = "Asc" if a.get("sort") == "Asc" else "Desc"; c = a.get("cursor") or ""
    r = req(f"https://games.roblox.com/v1/games/{int(a['place'])}/servers/Public?sortOrder={sort}&limit=50" + (f"&cursor={urllib.parse.quote(c)}" if c else ""))
    return {"list": [{"id": s["id"], "playing": s["playing"], "max": s["maxPlayers"], "ping": s.get("ping"), "fps": round(s.get("fps") or 0)} for s in r["data"]],
            "next": r.get("nextPageCursor")}

def fill_user(i, name, display):
    """Build and store the hub user record (profile, friend count, Robux when logged in)."""
    u = {"id": i, "name": name, "display": display,
         "avatar": thumbs("users/avatar-headshot", [i]).get(i),
         "friends": req(f"https://friends.roblox.com/v1/users/{i}/friends/count")["count"],
         "created": req(f"https://users.roblox.com/v1/users/{i}").get("created", "")[:10]}
    try: u["robux"] = req(f"https://economy.roblox.com/v1/users/{i}/currency")["robux"]
    except Exception: pass
    with lock: d = load(); d["user"] = u; store(d)
    return u

def user(a):
    """Look up a public profile by username (read-only mode, no login)."""
    r = req("https://users.roblox.com/v1/usernames/users", {"usernames": [a["name"]], "excludeBannedUsers": False})["data"]
    if not r: raise Exception("User not found")
    return fill_user(r[0]["id"], r[0]["name"], r[0]["displayName"])

def _finish_login(ck):
    """Store a session cookie, validate it against Roblox and discard it again if rejected. Shared by cookie and Quick Login."""
    with lock: d = load(); d["cookie"] = ck; store(d)
    try: me = req("https://users.roblox.com/v1/users/authenticated")
    except Exception:
        with lock: d = load(); d.pop("cookie", None); store(d)
        raise Exception("Roblox rejected that login")
    return fill_user(me["id"], me["name"], me["displayName"])

def connect(a):
    """Log in with a pasted .ROBLOSECURITY cookie."""
    ck = a["cookie"].strip().replace(".ROBLOSECURITY=", "")
    if not ck: raise Exception("Paste your .ROBLOSECURITY cookie first")
    return _finish_login(ck)

def _rpost(url, body):
    """POST JSON to Roblox WITHOUT any stored cookie (used before we are logged in). Returns (json, response headers)."""
    def go(tok=None):
        h = {"User-Agent": "SoberHub", "Content-Type": "application/json"}
        if tok: h["x-csrf-token"] = tok
        r = OP.open(urllib.request.Request(url, headers=h, method="POST", data=json.dumps(body).encode()), timeout=15)
        t = r.read(); return (json.loads(t) if t else {}), r.headers
    try:
        try: return go()
        except urllib.error.HTTPError as e:
            if e.code == 403 and e.headers.get("x-csrf-token"): return go(e.headers["x-csrf-token"])
            raise
    except urllib.error.HTTPError as e:
        m = None
        try:
            j = json.loads(e.read()); er = j.get("errors")
            m = (er[0].get("message") if er else None) or j.get("message") or j.get("errorMsg") or j.get("reason") or j.get("title")
        except Exception: pass
        m = m or f"Roblox returned HTTP {e.code}"
        raise Exception(m)

QL = {}   # the one Quick Login in progress: {"code", "key", "t0"}; the key never leaves this process
QLA = "https://apis.roblox.com/auth-token-service/v1/login/"
def ql_start(_):
    """Quick Login step 1: ask Roblox for a one-time code. The person confirms it on roblox.com/crossdevicelogin/ConfirmCode."""
    r, _h = _rpost(QLA + "create", {})
    if not r.get("code") or not r.get("privateKey"): raise Exception("Roblox didn't give a login code")
    QL.clear(); QL.update(code=r["code"], key=r["privateKey"], t0=time.time())
    return {"code": r["code"], "ttl": 300}

def ql_poll(_):
    """Quick Login step 2 (polled by the UI): waiting -> linked -> done. On confirmation the session cookie is taken from Roblox's reply and stored."""
    if not QL: return {"state": "none"}
    if time.time() - QL["t0"] > 320: QL.clear(); return {"state": "expired"}
    s, _h = _rpost(QLA + "status", {"code": QL["code"], "privateKey": QL["key"]})
    st = s.get("status")
    if st == "Cancelled": QL.clear(); return {"state": "cancelled"}
    if st != "Validated": return {"state": "linked" if st == "UserLinked" else "waiting"}
    code, key = QL["code"], QL["key"]; QL.clear()
    _b, h = _rpost("https://auth.roblox.com/v2/login", {"ctype": "AuthToken", "cvalue": code, "password": key})
    ck = next((c.split(";")[0].split("=", 1)[1] for c in (h.get_all("Set-Cookie") or []) if c.startswith(".ROBLOSECURITY=")), None)
    if not ck: raise Exception("Roblox confirmed the code but didn't return a session")
    return {"state": "done", "user": _finish_login(ck)}

def ql_cancel(_): QL.clear(); return {"ok": True}

# ---- auto update from GitHub
UPD = {"state": "idle", "version": None, "error": None, "notes": [], "shown": True}   # idle | checking | current | found | installed | failed | offline | nover
ULOCK = threading.Lock()
def _vt(v): return tuple(int(x) for x in re.findall(r"\d+", v))

def _notes(text):
    """Read the WHATS_NEW list out of the downloaded file without running it (ast.literal_eval only accepts plain literals)."""
    try:
        for n in ast.parse(text).body:
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "WHATS_NEW" for t in n.targets):
                v = ast.literal_eval(n.value)
                return [str(x)[:200] for x in v if isinstance(x, str)][:12] if isinstance(v, (list, tuple)) else []
    except Exception: pass
    return []

def _install_update(raw, text, rv):
    """Verify a downloaded sober_hub.py and atomically replace this file with it (old one kept as .bak). Raises with a readable reason."""
    me = os.path.abspath(__file__)
    if len(raw) < 20000 or "ThreadingHTTPServer" not in text or "__main__" not in text: raise Exception("the downloaded file looks incomplete")
    try: compile(text, "sober_hub.py", "exec")
    except SyntaxError as e: raise Exception(f"the downloaded file has a syntax error (line {e.lineno})")
    if not os.access(os.path.dirname(me), os.W_OK) or not os.access(me, os.W_OK): raise Exception("no permission to write to " + me)
    tmp = me + ".new"
    try:
        with open(tmp, "wb") as f: f.write(raw)
        try: shutil.copymode(me, tmp)
        except Exception: pass
        shutil.copy2(me, me + ".bak")
        os.replace(tmp, me)
    except OSError as e:
        try: os.remove(tmp)
        except Exception: pass
        raise Exception(e.strerror or str(e))

def update_check(startup=False):
    """Compare VERSION with the one on GitHub; if the repo is newer, download and install it. The result is kept in UPD for the UI."""
    with ULOCK:
        UPD.update(state="checking", error=None)
        try:
            r = urllib.request.Request(REPO_RAW + "?t=" + str(int(time.time())), headers={"User-Agent": "SoberHub-updater", "Cache-Control": "no-cache"})
            raw = urllib.request.urlopen(r, timeout=6 if startup else 15).read(); text = raw.decode("utf-8")
        except Exception as e:
            UPD.update(state="offline", error="Couldn't reach GitHub: " + str(getattr(e, "reason", e))); return dict(UPD)
        m = re.search(r'^VERSION\s*=\s*["\']([0-9][\w.\-]*)["\']', text, re.M)
        if not m: UPD.update(state="nover", error="The file on GitHub has no VERSION line"); return dict(UPD)
        rv = m.group(1)
        if _vt(rv) <= _vt(VERSION): UPD.update(state="current", version=rv); return dict(UPD)
        UPD.update(state="found", version=rv)
        try: _install_update(raw, text, rv)
        except Exception as e:
            UPD.update(state="failed", error=str(e) or e.__class__.__name__, shown=False); return dict(UPD)
        notes = _notes(text)
        UPD.update(state="installed", notes=notes, shown=False)
        with lock: d = load(); d["upd_notice"] = {"version": rv, "old": VERSION, "notes": notes}; store(d)
        return dict(UPD)

def restart(keep_port=False):
    """Replace this process with the freshly installed file. keep_port reuses the port so an open browser window can just reload."""
    env = dict(os.environ)
    if keep_port: env["SOBER_HUB_PORT"] = str(PORT[0])
    sys.stdout.flush()
    os.execve(sys.executable, [sys.executable, os.path.abspath(__file__)] + ARGV[1:], env)
PORT = [0]

def upd_status(_):
    n = load().get("upd_notice")
    if n and not n.get("notes") and n.get("version") == VERSION: n["notes"] = WHATS_NEW   # an older build wrote the notice, but this running file knows its own notes
    return {**{k: v for k, v in UPD.items()}, "cur": VERSION, "notice": n}
def upd_check(_): return {**update_check(), "cur": VERSION}
def upd_ack(_):
    UPD["shown"] = True
    with lock: d = load(); d["upd_notice"] = None; store(d)
    return {"ok": True}
def upd_restart(_):
    threading.Timer(0.6, lambda: restart(True)).start(); return {"ok": True}
def _upd_loop():
    while True:
        time.sleep(6 * 3600)
        try:
            if load()["opts"].get("autoupd", "1") != "0": update_check()
        except Exception: pass

def disconnect(a):
    """Forget the stored cookie and user."""
    with lock: d = load(); d.pop("cookie", None); d["user"] = None; store(d)
    return {"ok": True}

def all_friends(uid):
    """Every friend: the plain list first, then the paged 'find' endpoint fills in anything missing (accounts with hundreds of friends)."""
    seen, out = set(), []
    def add(x):
        i = x.get("id") or x.get("userId")
        if i and i not in seen: seen.add(i); out.append({"id": i, "name": x.get("name") or "", "displayName": x.get("displayName") or ""})
    try:
        for x in req(f"https://friends.roblox.com/v1/users/{uid}/friends")["data"]: add(x)
    except Exception: pass
    try: total = req(f"https://friends.roblox.com/v1/users/{uid}/friends/count")["count"]
    except Exception: total = 0
    if len(out) < total or not out:
        cur = ""
        for _ in range(60):
            try: r = req(f"https://friends.roblox.com/v1/users/{uid}/friends/find?limit=50&userSort=1" + (f"&cursor={urllib.parse.quote(cur)}" if cur else ""))
            except Exception: break
            for x in r.get("PageItems") or r.get("pageItems") or r.get("data") or []: add(x)
            cur = r.get("NextCursor") or r.get("nextCursor") or r.get("nextPageCursor")
            if not cur: break
    if not out: raise Exception("Could not load your friends list")
    return out


# ---- last online cache: remembers when each friend was last seen online, and the game they were in (only learns while the hub is open)
LS = {}; LS_EV = []; LS_META = {}; LSF = D + "/lastseen.json"; LSL = threading.Lock(); LS_SAVED = [0.0]
def _lsload():
    try:
        j = json.load(open(LSF))
        for k, v in (j.get("friends") or {}).items(): LS[int(k)] = v
        LS_EV.extend((j.get("events") or [])[-100:]); LS_META.update(j.get("meta") or {})
    except Exception: pass
_lsload()
def _lssave(force=False):
    """Write lastseen.json (owner-only). Plain refreshes are throttled to once every 90 s; real changes are written at once."""
    if not force and time.time() - LS_SAVED[0] < 90: return
    try:
        os.makedirs(D, mode=0o700, exist_ok=True); tmp = LSF + ".tmp"
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump({"friends": {str(k): v for k, v in LS.items()}, "events": LS_EV[-100:], "meta": LS_META}, f)
        os.replace(tmp, LSF); LS_SAVED[0] = time.time()
    except Exception: pass
def _iso(s):
    try: return int(datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except Exception: return 0

def lastseen_update(fl, pr, nm):
    """Record presence for every friend whose presence really arrived (a failed batch teaches nothing). Activity events are skipped after a long gap (hub was closed) so they never carry a wrong time."""
    now = int(time.time()); fresh = now - LS_META.get("poll", 0) <= 180; changed = False
    with LSL:
        for f in fl:
            i = f["id"]; p = pr.get(i)
            if p is None: continue
            s = p.get("userPresenceType", 0) or 0; r = LS.get(i); first = r is None
            if first: r = LS[i] = {"t": 0, "s": 0, "ing": False}
            old, oldp, place = r.get("s", 0), r.get("p"), p.get("rootPlaceId")
            game = (p.get("lastLocation") or "") if s == 2 else ""
            kind = None
            if fresh and not first:
                if s == 2 and (old != 2 or (place and place != oldp)): kind = "play"
                elif s in (1, 3) and old == 0: kind = "online"
                elif s == 0 and old > 0: kind = "offline"
            if kind:
                who = (nm.get(i) or ("", ""))[1] or (nm.get(i) or ("", ""))[0] or "A friend"
                g = game if kind == "play" else ((r.get("g") or "") if (kind == "offline" and r.get("ing")) else "")
                LS_EV.append({"id": i, "n": who, "k": kind, "g": g, "t": now}); changed = True
            if s > 0:
                r["t"] = now; r["ing"] = (s == 2); r["src"] = "hub"
                if s == 2:
                    if game: r["g"] = game
                    if place: r["p"] = place
                    r["gt"] = now
            elif not r.get("t") or r.get("src") == "roblox":   # never seen online by the hub: borrow Roblox's own last-online if it gives one
                lo = _iso(p.get("lastOnline"))
                if lo and lo > r.get("t", 0): r["t"] = lo; r["src"] = "roblox"
            if r.get("s") != s: changed = True
            r["s"] = s
        LS_META["poll"] = now
        if len(LS_EV) > 100: del LS_EV[:-100]
        _lssave(changed)

def lastseen(a): return {"rec": LS.get(int(a["id"]))}
def lsevents(_a): return list(reversed(LS_EV[-60:]))
def ls_clear(_a):
    with LSL: LS.clear(); LS_EV.clear(); LS_META.clear(); _lssave(True)
    return {"ok": True}

def friends(a):
    """EVERY friend (no cap). Presence is requested 50 at a time and avatars 100 at a time, in parallel."""
    u = load()["user"]
    if not u: raise Exception("Not connected")
    fl = all_friends(u["id"])
    ids = [f["id"] for f in fl]
    def pres(c):
        try: return req("https://presence.roblox.com/v1/presence/users", {"userIds": c})["userPresences"]
        except Exception: return []
    def ava(c):
        try: return thumbs("users/avatar-headshot", c)
        except Exception: return {}
    with ThreadPoolExecutor(4) as ex:
        pr = {p["userId"]: p for ps in ex.map(pres, chunks(ids, 50)) for p in ps}
        av = {k: v for m in ex.map(ava, chunks(ids, 100)) for k, v in m.items()}
    pin = set(load().get("pinned") or [])
    prio = [f for f in fl if pr.get(f["id"], {}).get("userPresenceType", 0) in (1, 2, 3) or f["id"] in pin]
    nm = names(prio, 300)   # online, in-game and pinned friends first: full retries + single lookups, so they never show as "User 123"
    nm.update(names([f for f in fl if f["id"] not in nm]))
    lastseen_update(fl, pr, nm)
    out = []
    for f in fl:
        p = pr.get(f["id"], {}); r = LS.get(f["id"]) or {}
        out.append({"id": f["id"], "name": nm[f["id"]][0], "display": nm[f["id"]][1], "avatar": av.get(f["id"]),
                    "status": p.get("userPresenceType", 0), "place": p.get("rootPlaceId"), "job": p.get("gameId"), "where": p.get("lastLocation"),
                    "seen": r.get("t") or 0, "sing": bool(r.get("ing")), "sgame": r.get("g") or "", "splace": r.get("p"), "gt": r.get("gt") or 0})
    nk = load().get("nicks") or {}
    for o in out:   # a nickname replaces the display name everywhere in the hub; the real one is kept as "real"
        n = nk.get(str(o["id"]))
        if n: o["real"], o["display"], o["nick"] = o["display"], n, True
    out.sort(key=lambda x: {2: 0, 1: 1, 3: 2, 0: 3}.get(x["status"], 3))
    return out

def resolve(ids, singles=300):
    """id -> (username, display name). Cached, batched 100 at a time with retries; single lookups as a last resort."""
    need = [i for i in dict.fromkeys(ids) if i and i not in NAMES]
    for c in chunks(need, 100):
        for attempt in range(3):
            try:
                for x in req("https://users.roblox.com/v1/users", {"userIds": c, "excludeBannedUsers": False})["data"]:
                    if x.get("name"): NAMES[x["id"]] = (x["name"], x.get("displayName") or x["name"])
                break
            except Exception: time.sleep(0.7 * (attempt + 1))
    for i in [i for i in need if i not in NAMES][:singles]:
        for attempt in range(2):
            try:
                u = req(f"https://users.roblox.com/v1/users/{i}")
                if u.get("name"): NAMES[i] = (u["name"], u.get("displayName") or u["name"])
                break
            except Exception: time.sleep(0.5)
    if need: _nsave()
    return {i: NAMES.get(i) for i in ids}

def frequests(a):
    """EVERY pending friend request (follows the pagination cursor until the end)."""
    out, cur, lim = [], "", 100
    for _ in range(300):
        url = f"https://friends.roblox.com/v1/my/friends/requests?sortOrder=Desc&limit={lim}" + (f"&cursor={urllib.parse.quote(cur)}" if cur else "")
        try: r = req(url)
        except Exception:
            if lim == 100 and not out: lim = 25; continue   # some accounts only accept the smaller page size
            raise
        out += r.get("data", []); cur = r.get("nextPageCursor")
        if not cur: break
    ids = [(x.get("friendRequest") or {}).get("senderId") or x.get("id") or x.get("userId") for x in out]
    nm = resolve([i for i in ids if i])
    av = {}
    for c in chunks([i for i in ids if i], 100):
        for attempt in range(2):
            try: av.update(thumbs("users/avatar-headshot", c)); break
            except Exception: time.sleep(0.5)
    res = []
    for i, x in zip(ids, out):
        n, d = nm.get(i) or ("", "")
        n = n or x.get("name") or "User " + str(i); d = d or x.get("displayName") or n
        res.append({"id": i, "name": n, "display": d, "avatar": av.get(i), "mutual": len(x.get("mutualFriendsList") or [])})
    return res

def col_enter():
    """Turn on Sober's close_on_leave while a hub-launched game runs, so leaving the game lands back in the hub, not on Sober's home screen."""
    if load()["opts"].get("backhub", "1") != "1": return False
    with CLK:
        try:
            cfg, head = cfg_read()
            if COL["n"] == 0:
                with lock:
                    d = load()
                    if "col_orig" not in d: d["col_orig"] = {"v": cfg.get("close_on_leave")}; store(d)
            COL["n"] += 1
            if cfg.get("close_on_leave") is not True: cfg["close_on_leave"] = True; cfg_write(cfg, head)
            return True
        except Exception: return False

def col_leave():
    """Put the user's own close_on_leave value back once the last hub-launched game has ended."""
    with CLK:
        COL["n"] = max(0, COL["n"] - 1)
        if COL["n"]: return
        try:
            with lock:
                d = load(); o = d.pop("col_orig", None); store(d)
            if not o: return
            cfg, head = cfg_read()
            if o.get("v") is None: cfg.pop("close_on_leave", None)
            else: cfg["close_on_leave"] = o["v"]
            cfg_write(cfg, head)
        except Exception: pass

def col_recover():
    """If the hub was closed mid-game last time, restore the setting it changed."""
    try:
        if load().get("col_orig") and APP not in _run(["flatpak", "ps", "--columns=application"]).stdout: COL["n"] = 1; col_leave()
    except Exception: pass

def focus_hub():
    """Best effort: bring the hub window to the front (pywebview, wmctrl or xdotool)."""
    ok = False
    try:
        w = WIN[0]
        if w: w.restore(); w.show(); ok = True
    except Exception: pass
    for cmd in (["wmctrl", "-x", "-a", WMCLASS], ["xdotool", "search", "--class", WMCLASS, "windowactivate"]):
        try:
            if shutil.which(cmd[0]) and _run(cmd, 5).returncode == 0: ok = True; break
        except Exception: pass
    return ok

def icon_file(u):
    """Download a Roblox CDN image once for use as a desktop-notification icon."""
    try:
        h = urllib.parse.urlparse(u or "")
        if h.scheme != "https" or not (h.hostname or "").endswith(".rbxcdn.com"): return None
        os.makedirs(D + "/ntf", exist_ok=True); fp = D + "/ntf/" + uuid.uuid5(uuid.NAMESPACE_URL, u).hex + ".png"
        if not os.path.exists(fp):
            data = OP.open(urllib.request.Request(u, headers={"User-Agent": "SoberHub"}), timeout=5).read(2000000)
            open(fp, "wb").write(data)
        return fp
    except Exception: return None

def dnotify(a):
    """Native desktop notification through notify-send (works while the hub window is in the background)."""
    if not shutil.which("notify-send"): return {"ok": False}
    cmd = ["notify-send", "-a", "Sober Hub", "-t", "9000"]
    ic = icon_file(a.get("icon"))
    if ic: cmd += ["-i", ic]
    r = _run(cmd + [str(a.get("title") or "Sober Hub")[:100], str(a.get("body") or "")[:200]], 5)
    return {"ok": r.returncode == 0}

def track(place, t0, p, name, col=False):
    """Thread body: wait for the launched Sober process to exit, then add the session to playtime stats (per game and per day, 400 days kept)."""
    p.wait(); s = time.time() - t0
    ACTIVE.pop(str(place), None)
    if col: col_leave(); focus_hub()
    with lock:
        d = load(); e = d["play"].setdefault(str(place), {"secs": 0, "launches": 0, "last": 0, "longest": 0})
        e["secs"] += s; e["longest"] = max(e.get("longest", 0), s)
        k = time.strftime("%Y-%m-%d"); d["daily"][k] = d["daily"].get(k, 0) + s
        for old in sorted(d["daily"])[:-400]: d["daily"].pop(old, None)
        store(d)

def capture_server(place, ts, p):
    """After a plain Play, ask Roblox which server we ended up in (own presence 'gameId') and store it on that history entry, so 'Rejoin last server' works."""
    u = load().get("user")
    if not u or not load().get("cookie"): return
    got, n = None, 0
    while p.poll() is None and n < 600:
        time.sleep(6 if n < 20 else 20); n += 1
        try: x = (req("https://presence.roblox.com/v1/presence/users", {"userIds": [u["id"]]}).get("userPresences") or [{}])[0]
        except Exception: continue
        job = x.get("gameId")
        if x.get("userPresenceType") == 2 and job and str(place) in (str(x.get("rootPlaceId")), str(x.get("placeId"))) and job != got:
            got = job
            with lock:
                d = load()
                for h in d["history"]:
                    if abs(h.get("ts", 0) - ts) < 1e-6: h["job"] = job; break
                store(d)

def launch(a):
    """Start Sober, optionally straight into a place / server / private-server link code, and record it in history and playtime stats."""
    place, job, code = a.get("place"), a.get("job"), a.get("code")
    cmd = shlex.split(load()["opts"]["cmd"])
    if place:
        u = f"roblox://experiences/start?placeId={int(place)}"
        if job: u += "&gameInstanceId=" + urllib.parse.quote(str(job))
        if code: u += "&linkCode=" + urllib.parse.quote(str(code))
        cmd.append(u)
    col = col_enter() if place else False
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    if place:
        now = time.time(); name, icon = a.get("name") or "", a.get("icon") or ""
        with lock:
            d = load()
            d["history"] = ([{"place": int(place), "name": name, "icon": icon, "job": job, "code": code, "ts": now}] + d["history"])[:500]
            e = d["play"].setdefault(str(place), {"secs": 0, "launches": 0, "last": 0, "longest": 0})
            e.update(launches=e["launches"] + 1, last=now)
            if name: e["name"] = name
            if icon: e["icon"] = icon
            store(d)
        ACTIVE[str(place)] = {"name": name or str(place), "t0": now}
        threading.Thread(target=track, args=(place, now, p, name, col), daemon=True).start()
        if not job and not code: threading.Thread(target=capture_server, args=(place, now, p), daemon=True).start()
        if not (name and icon):
            def fixup():
                try: heal([place])
                except Exception: pass
            threading.Thread(target=fixup, daemon=True).start()
    return {"ok": True}

def active(_):
    """Games launched from the hub that are still running, with elapsed seconds."""
    now = time.time()
    return sorted([{"place": k, "name": v["name"], "secs": round(now - v["t0"])} for k, v in ACTIVE.items()], key=lambda x: x["secs"])

def hist_del(a):
    with lock:
        d = load(); d["history"] = [h for h in d["history"] if abs(h.get("ts", 0) - a["ts"]) > 1e-6]; store(d)
    return {"ok": True}

def _run(cmd, t=15):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=t)

def _jsonc(t):
    """Sober's config.json starts with // comment lines, which plain json.load rejects. Strip comments (outside strings)."""
    out, i, n, s, e = [], 0, len(t), False, False
    while i < n:
        c = t[i]
        if s:
            out.append(c)
            if e: e = False
            elif c == "": e = True
            elif c == '"': s = False
        elif c == '"': s = True; out.append(c)
        elif c == "/" and t[i + 1:i + 2] == "/":
            while i < n and t[i] != "\n": i += 1
            continue
        elif c == "/" and t[i + 1:i + 2] == "*":
            j = t.find("*/", i + 2); i = n if j < 0 else j + 2
            continue
        else: out.append(c)
        i += 1
    return "".join(out)

def cfg_read():
    if not os.path.exists(SC): return {}, ""
    t = open(SC, encoding="utf-8").read(); head = []
    for ln in t.splitlines():
        if ln.strip() == "" or ln.lstrip().startswith("//"): head.append(ln)
        else: break
    try: return json.loads(_jsonc(t) or "{}"), "\n".join(head).rstrip()
    except ValueError as ex:
        raise Exception("Sober's config.json can't be parsed (" + str(ex) + "). Delete it and start Sober once to recreate it.")

def cfg_write(cfg, head):
    os.makedirs(os.path.dirname(SC), exist_ok=True)
    if os.path.exists(SC) and not os.path.exists(SC + ".hub.bak"): shutil.copy(SC, SC + ".hub.bak")
    tmp = SC + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: f.write((head + "\n" if head else "") + json.dumps(cfg, indent=4) + "\n")
    os.replace(tmp, SC)

def sober(_):
    """Sober's install state, version, whether it is running, and its parsed config."""
    inst = bool(shutil.which("flatpak")) and _run(["flatpak", "info", APP]).returncode == 0
    ver, running = "", False
    if inst:
        for line in _run(["flatpak", "info", APP]).stdout.splitlines():
            if line.strip().startswith("Version:"): ver = line.split(":", 1)[1].strip()
        try: running = APP in _run(["flatpak", "ps", "--columns=application"]).stdout
        except Exception: pass
    return {"installed": inst, "version": ver, "running": running, "cfg": cfg_read()[0], "path": SC}

def doctor(_a):
    """Hub health check -> [{name, state: ok|warn|bad, detail}]. Network checks run in parallel; the cookie only ever goes to roblox.com."""
    def row(name, state, detail): return {"name": name, "state": state, "detail": detail}
    def hub():
        try: import webview; win = "native window (pywebview)"
        except Exception: win = "browser app window (pywebview is optional)"
        st = "ok" if sys.version_info >= (3, 8) else "warn"
        return row("Sober Hub", st, f"v{VERSION} · Python {sys.version.split()[0]} · {win}")
    def data():
        try:
            base = D if os.path.isdir(D) else os.path.dirname(D)
            if not os.access(base, os.W_OK): return row("Hub data", "bad", f"{D} is not writable, so nothing can be saved")
            if os.path.exists(F) and os.stat(F).st_mode & 0o077: return row("Hub data", "warn", f"{F} is readable by other users. Fix: chmod 600 {F}")
            return row("Hub data", "ok", F)
        except Exception as e: return row("Hub data", "bad", str(e))
    def sob():
        if not shutil.which("flatpak"): return [row("Sober", "bad", "flatpak was not found. Install flatpak, then: flatpak install flathub " + APP)]
        if _run(["flatpak", "info", APP]).returncode != 0: return [row("Sober", "bad", "Sober is not installed. Run: flatpak install flathub " + APP)]
        try: running = APP in _run(["flatpak", "ps", "--columns=application"]).stdout
        except Exception: running = False
        out = [row("Sober", "ok", "installed" + (" and running" if running else ""))]
        try:
            n = len((cfg_read()[0] or {}).get("fflags") or {})
            out.append(row("Sober config", "ok" if os.path.exists(SC) else "warn", f"{SC} ({n} Fast Flags)" if os.path.exists(SC) else "Not created yet. Start Sober once"))
        except Exception as e: out.append(row("Sober config", "bad", str(e)))
        return out
    def tools():
        return [row("ffmpeg", "ok" if shutil.which("ffmpeg") else "warn", "found" if shutil.which("ffmpeg") else "Optional: needed to convert MP3 files for the custom death sound"),
                row("Desktop alerts", "ok" if shutil.which("notify-send") else "warn", "notify-send found" if shutil.which("notify-send") else "Optional: install libnotify-bin for desktop alerts (break reminders, friend pop-ups)")]
    def net(name, url):
        try:
            t0 = time.time(); urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "SoberHub"}), timeout=8).read(512)
            return row(name, "ok", f"reachable ({round((time.time() - t0) * 1000)} ms)")
        except Exception as e: return row(name, "bad", f"cannot reach it: {e}")
    def acct():
        if not load().get("cookie"): return row("Roblox account", "warn", "Not connected. Friends, Chats and Avatar need Hub Login")
        try: return row("Roblox account", "ok", "Session is valid, logged in as " + str(req("https://users.roblox.com/v1/users/authenticated").get("name", "?")))
        except Exception as e: return row("Roblox account", "bad", f"{e}. Reconnect in Hub Login")
    with ThreadPoolExecutor(4) as ex:
        fr, fs = ex.submit(net, "Roblox connection", "https://users.roblox.com/v1/users/1"), ex.submit(net, "GitHub (updates)", REPO_RAW)
        fa = ex.submit(acct)
        rows = [hub(), data(), *sob(), *tools(), fr.result(), fa.result(), fs.result()]
    return rows

def sober_set(a):
    """Merge settings into Sober's config.json: `fflags` is merged, `fflag_del` removes flags, any other key is set directly. Sober reads the file at startup, so it must be restarted."""
    cfg, head = cfg_read()
    for k, v in a.items():
        if k == "fflags": cfg.setdefault("fflags", {}).update(v)
        elif k == "fflag_del":
            for x in v: cfg.get("fflags", {}).pop(x, None)
        else: cfg[k] = v
    cfg_write(cfg, head)
    try:   # read it back: confirms the change really reached disk
        chk = cfg_read()[0]; ff = a.get("fflags") or {}
        verified = all(chk.get("fflags", {}).get(k) == v for k, v in ff.items()) and not any(k in chk.get("fflags", {}) for k in a.get("fflag_del") or [])
    except Exception: verified = False
    running = False
    try: running = APP in _run(["flatpak", "ps", "--columns=application"]).stdout
    except Exception: pass
    return {"ok": True, "running": running, "verified": verified}

def sober_update(_):
    p = _run(["flatpak", "update", "-y", APP], 900)
    return {"ok": p.returncode == 0, "out": (p.stdout + p.stderr)[-300:]}

def sober_kill(_):
    _run(["flatpak", "kill", APP]); return {"ok": True}

def sober_dir(_):
    subprocess.Popen(["xdg-open", os.path.dirname(SC)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); return {"ok": True}

def save(a):
    """Persist UI settings. theme/opts/notes are merged key by key, everything else is replaced; the cookie cannot be set through here."""
    with lock:
        d = load()
        for k, v in a.items():
            if k == "cookie": continue
            if k in ("theme", "opts", "notes"): d[k].update(v)
            else: d[k] = v
        if "opts" in a or "capi" in a: BR["t"] = 0; CR["t"] = 0
        store(d)
    return {"ok": True}

def clear(a):
    with lock:
        d = load()
        for k in a["what"]: d[k] = type(DEF[k])()
        store(d)
    return {"ok": True}

def imp(a):
    """Restore a backup. The cookie is never imported and the current custom-API headers (which may hold a key) are kept."""
    for k in ("cookie", "upd_notice", "version", "connected", "changelog"): a["data"].pop(k, None)
    if isinstance(a["data"].get("capi"), dict): a["data"]["capi"]["headers"] = (load().get("capi") or {}).get("headers", "")
    with lock: store({**load(), **a["data"]})
    return {"ok": True}

def state(_):
    """Everything the UI needs, minus the cookie itself (only a `connected` flag is exposed)."""
    d = load(); c = bool(d.pop("cookie", None)); d["connected"] = c; d["version"] = VERSION
    d["changelog"] = [{"v": VERSION, "notes": WHATS_NEW}, {"v": "5.5.0", "notes": CHANGELOG_OLD}]; return d

def rbx(a):
    """Generic proxy to Roblox APIs for the UI. Only https://*.roblox.com is allowed."""
    url = a["url"]
    if not url.startswith("https://") or not (urllib.parse.urlparse(url).hostname or "").endswith(".roblox.com"): raise Exception("blocked")
    m = (a.get("method") or "GET").upper(); b = a.get("body")
    if m != "GET" and b is None: b = {}
    return req(url, b, m)

def me(a):
    u = load()["user"]; return fill_user(u["id"], u["name"], u["display"])

def lastplayed(a):
    """Roblox account 'Continue' list (not guaranteed to be sorted by recency - the hub's own history is used for the top card by default)."""
    ids, dbg = [], []
    def pick(sorts):
        for s in sorts:
            nm = " ".join(str(s.get(k, "")) for k in ("topic", "topicId", "treatmentType", "sortId", "sortDisplayName", "sortName")).lower()
            dbg.append(nm.strip()[:40])
            if "continue" in nm or "recent" in nm:
                l = [x["contentId"] for x in s.get("recommendationList", []) if x.get("contentType") == "Game"] or [g["universeId"] for g in s.get("games", []) if "universeId" in g]
                if l: return l
        return []
    for body in ({"pageType": "Home", "sessionId": str(uuid.uuid4())},
                 {"pageType": "Home", "sessionId": str(uuid.uuid4()), "supportedTreatmentTypes": ["Carousel"]}):
        try: ids = pick(req("https://apis.roblox.com/discovery-api/omni-recommendation", body).get("sorts", []))
        except Exception as e: dbg.append("omni: " + str(e))
        if ids: break
    if not ids:
        base, sid = "https://apis.roblox.com/explore-api/v1/", str(uuid.uuid4())
        try:
            for s in req(base + f"get-sorts?sessionId={sid}&device=computer&country=all").get("sorts", []):
                nm = (str(s.get("sortDisplayName", "")) + " " + str(s.get("sortId", ""))).lower(); dbg.append(nm[:40])
                if "continue" in nm or "recent" in nm:
                    ids = [g["universeId"] for g in req(base + f"get-sort-content?sessionId={sid}&sortId={s['sortId']}&device=computer&country=all").get("games", [])]
                    if ids: break
        except Exception as e: dbg.append("explore: " + str(e))
    if not ids: return {"games": [], "debug": "; ".join(dbg)[:300]}
    ids = ids[:12]
    gs = req("https://games.roblox.com/v1/games?universeIds=" + ",".join(map(str, ids)))["data"]
    order = {u: i for i, u in enumerate(ids)}
    gs.sort(key=lambda g: order.get(g["id"], 99))
    ic = thumbs("games/icons", ids)
    return {"games": [{"place": g["rootPlaceId"], "name": g["name"], "icon": ic.get(g["id"]), "playing": g["playing"]} for g in gs]}

def open_url(a):
    u = a["url"]; h = urllib.parse.urlparse(u).hostname or ""
    if not u.startswith("https://") or not (h == "roblox.com" or h.endswith(".roblox.com")): raise Exception("blocked")
    webbrowser.open(u); return {"ok": True}


# Sober's asset overlay: files placed here mirror Roblox's assets folder (e.g. content/sounds/ouch.ogg) and replace the originals.
OVL = os.path.expanduser("~/.var/app/org.vinegarhq.Sober/data/sober/asset_overlay")

def _ovl(rel):
    """Resolve a mod path inside the asset-overlay folder; anything escaping it (.., absolute paths, symlinks) raises."""
    rel = os.path.normpath(str(rel).replace("\\", "/").lstrip("/"))
    if not rel or rel == "." or rel.startswith("..") or ".." in rel.split(os.sep): raise Exception("Bad path")
    full = os.path.realpath(os.path.join(OVL, rel))
    if not full.startswith(os.path.realpath(OVL) + os.sep): raise Exception("Bad path")
    return full

MRAW = "https://raw.githubusercontent.com/bloxstraplabs/bloxstrap/main/Bloxstrap/Resources/Mods/"   # mod preset files
EMJ = "https://github.com/bloxstraplabs/rbxcustom-fontemojis/releases/download/my-phone-is-78-percent/"   # emoji font files
_CUR = "content/textures/Cursors/KeyboardMouse/"
_SND = "content/sounds/"
MODP = {   # preset id -> {path inside the overlay: download url}, downloaded from GitHub
    "avatar_bg": {"ExtraContent/places/Mobile.rbxl": MRAW + "OldAvatarBackground.rbxl"},
    "old_sounds": {_SND + "action_footsteps_plastic.mp3": MRAW + "Sounds/OldWalk.mp3", _SND + "action_jump.mp3": MRAW + "Sounds/OldJump.mp3",
                   _SND + "action_get_up.mp3": MRAW + "Sounds/OldGetUp.mp3", _SND + "action_falling.mp3": MRAW + "Sounds/Empty.mp3",
                   _SND + "action_jump_land.mp3": MRAW + "Sounds/Empty.mp3", _SND + "action_swim.mp3": MRAW + "Sounds/Empty.mp3", _SND + "impact_water.mp3": MRAW + "Sounds/Empty.mp3"},
    "cursor_2013": {_CUR + "ArrowCursor.png": MRAW + "Cursor/From2013/ArrowCursor.png", _CUR + "ArrowFarCursor.png": MRAW + "Cursor/From2013/ArrowFarCursor.png"},
    "cursor_2006": {_CUR + "ArrowCursor.png": MRAW + "Cursor/From2006/ArrowCursor.png", _CUR + "ArrowFarCursor.png": MRAW + "Cursor/From2006/ArrowFarCursor.png"},
    "emoji_catmoji": {"content/fonts/TwemojiMozilla.ttf": EMJ + "Catmoji.ttf"},
    "emoji_win11": {"content/fonts/TwemojiMozilla.ttf": EMJ + "Win1122H2SegoeUIEmoji.ttf"},
    "emoji_win10": {"content/fonts/TwemojiMozilla.ttf": EMJ + "Win10April2018SegoeUIEmoji.ttf"},
    "emoji_win8": {"content/fonts/TwemojiMozilla.ttf": EMJ + "Win8.1SegoeUIEmoji.ttf"}}
MODGROUP = [["cursor_2013", "cursor_2006"], ["emoji_catmoji", "emoji_win11", "emoji_win10", "emoji_win8"]]   # presets that share files: turning one on turns the others off
MM = D + "/mods.json"   # which presets the hub installed
FONT_REL = "content/fonts/CustomFont.ttf"

def _mman():
    try: return json.load(open(MM))
    except Exception: return {}

def _msave(m):
    os.makedirs(D, mode=0o700, exist_ok=True)
    with open(MM, "w") as f: json.dump(m, f)

def _prune(p):
    """Remove folders left empty after deleting a mod file (never the overlay root)."""
    d = os.path.dirname(p)
    while os.path.realpath(d) != os.path.realpath(OVL) and os.path.isdir(d) and not os.listdir(d):
        os.rmdir(d); d = os.path.dirname(d)

def _rmfile(rel):
    p = _ovl(rel)
    if os.path.isfile(p): os.remove(p); _prune(p)

def _dl(url, limit=25 * 1024 * 1024):
    """Download a mod file. Only GitHub hosts, no cookie is ever sent, redirects (GitHub release assets) are followed."""
    host = urllib.parse.urlparse(url).hostname or ""
    if not url.startswith("https://") or host not in ("raw.githubusercontent.com", "github.com"): raise Exception("blocked")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "SoberHub"}), timeout=60) as f: data = f.read(limit + 1)
    except Exception as e: raise Exception("Could not download the mod from GitHub (" + str(e) + ")")
    if not data or len(data) > limit: raise Exception("Bad download from GitHub")
    return data

def mods_list(_a):
    out = []
    for root, _dirs, files in os.walk(OVL):
        for f in files:
            p = os.path.join(root, f); out.append({"path": os.path.relpath(p, OVL), "size": os.path.getsize(p)})
    m = _mman()
    return {"dir": OVL, "files": sorted(out, key=lambda x: x["path"]),
            "presets": {k: bool(m.get(k)) and all(os.path.isfile(_ovl(r)) for r in v) for k, v in MODP.items()},
            "font": os.path.isfile(_ovl(FONT_REL))}

def mods_preset(a):
    """Install or remove one mod preset. Everything is downloaded first, so a failed download changes nothing."""
    pid, on = a["id"], bool(a.get("on", True))
    if pid not in MODP: raise Exception("Unknown preset")
    m = _mman()
    if on:
        cache = {}
        blobs = {r: cache.setdefault(u, _dl(u)) for r, u in MODP[pid].items()}
        for g in MODGROUP:
            if pid in g:
                for o in g:
                    if o != pid:
                        for r in MODP[o]: _rmfile(r)
                        m.pop(o, None)
        for r, data in blobs.items():
            p = _ovl(r); os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f: f.write(data)
        m[pid] = 1
    else:
        for r in MODP[pid]: _rmfile(r)
        m.pop(pid, None)
    _msave(m); return {"ok": True}

def _font_families():
    """Roblox's font family files, if Sober has them on disk (rewritten so every font uses CustomFont.ttf)."""
    base, ovl = os.path.dirname(OVL), os.path.realpath(OVL)
    for root, dirs, _f in os.walk(base):
        if os.path.realpath(root).startswith(ovl) or root[len(base):].count(os.sep) > 9: dirs[:] = []; continue
        if root.replace("\\", "/").endswith("content/fonts/families"): return root
    return None

def mods_font(a):
    """Custom font mod: installs content/fonts/CustomFont.ttf and points every font family at it."""
    m = _mman()
    if a.get("remove"):
        _rmfile(FONT_REL)
        for r in m.pop("font_families", []): _rmfile(r)
        _msave(m); return {"ok": True}
    data = base64.b64decode(a["b64"])
    if len(data) > 25 * 1024 * 1024: raise Exception("File too big (25 MB max)")
    if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"ttcf"): raise Exception("That is not a valid font file (.ttf, .otf or .ttc)")
    p = _ovl(FONT_REL); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f: f.write(data)
    made, fam = [], _font_families()
    if fam:
        for fn in sorted(os.listdir(fam)):
            if not fn.endswith(".json"): continue
            rel = "content/fonts/families/" + fn; dst = _ovl(rel)
            if os.path.exists(dst): continue
            try:
                j = json.load(open(os.path.join(fam, fn), encoding="utf-8"))
                for face in j.get("faces", []): face["assetId"] = "rbxasset://fonts/CustomFont.ttf"
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "w", encoding="utf-8") as f: json.dump(j, f)
                made.append(rel)
            except Exception: pass
    m["font_families"] = (m.get("font_families") or []) + made; _msave(m)
    return {"ok": True, "families": len(made)}

def _audio_kind(data):
    """What the bytes really are: 'ogg' (Vorbis), 'opus' (Ogg Opus - Roblox cannot play it), 'mp3', 'wav', or '' for anything else."""
    if data[:4] == b"OggS": return "opus" if b"OpusHead" in data[:300] else "ogg"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0): return "mp3"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE": return "wav"
    return ""

def _to_ogg(data):
    """MP3 -> Ogg Vorbis through ffmpeg when it is installed; None if it is not or the conversion fails."""
    ff = shutil.which("ffmpeg")
    if not ff: return None
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        a, b = td + "/in", td + "/out.ogg"
        with open(a, "wb") as f: f.write(data)
        try: subprocess.run([ff, "-y", "-loglevel", "error", "-i", a, "-c:a", "libvorbis", "-q:a", "5", b], capture_output=True, timeout=90)
        except Exception: return None
        try: out = open(b, "rb").read()
        except Exception: return None
    return out if out[:4] == b"OggS" else None

def mods_put(a):
    """Install one uploaded file into the overlay. `path` may list several targets separated by | (the death sound uses ouch.ogg and uuhhh.mp3).
    Audio is written only under a name that matches its real format (Roblox decodes by file type, so an MP3 saved as .ogg is silent); an MP3 is converted to Ogg when ffmpeg is available."""
    data = base64.b64decode(a["b64"])
    if len(data) > 25 * 1024 * 1024: raise Exception("File too big (25 MB max)")
    if not data: raise Exception("That file is empty")
    targets = [x.strip() for x in str(a["path"]).split("|") if x.strip()]
    writes, converted = {}, False
    audio = [x for x in targets if x.lower().endswith((".ogg", ".mp3"))]
    if audio:
        kind = _audio_kind(data)
        if kind == "opus": raise Exception("This .ogg uses the Opus codec, which Roblox cannot play. Export it again as Ogg Vorbis (Audacity: File > Export > Export as OGG).")
        if kind == "wav": raise Exception("WAV files cannot be used. Export it as Ogg Vorbis (.ogg) first (Audacity: File > Export > Export as OGG).")
        if kind not in ("ogg", "mp3"): raise Exception("That is not an .ogg or .mp3 audio file")
        for x in audio:
            if x.lower().endswith("." + kind): writes[x] = data
            elif kind == "mp3" and x.lower().endswith(".ogg"):
                o = _to_ogg(data)
                if o: writes[x] = o; converted = True
        if not writes or (kind == "mp3" and any(x.lower().endswith(".ogg") and x not in writes for x in audio)):
            raise Exception("This is an MP3, but the game reads the death sound from an Ogg Vorbis file and ffmpeg is not installed to convert it. Convert it to .ogg (Audacity or any online converter) and install it again.")
    for x in targets:
        if x not in audio: writes[x] = data
    for x, blob in writes.items():
        p = _ovl(x); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f: f.write(blob)
        if os.path.getsize(p) != len(blob): raise Exception("Could not write " + x)
    return {"ok": True, "files": len(writes), "converted": converted, "skipped": [x for x in targets if x not in writes]}

def mods_del(a):
    if a.get("all"): shutil.rmtree(OVL, ignore_errors=True); _msave({}); return {"ok": True}
    _rmfile(a["path"]); return {"ok": True}

def mods_dir(_a):
    os.makedirs(OVL, exist_ok=True)
    subprocess.Popen(["xdg-open", OVL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); return {"ok": True}

R = {"state": state, "lastplayed": lastplayed, "open": open_url, "rbx": rbx, "me": me, "game": lambda a: ginfo(a["place"]), "search": search, "servers": servers,
     "user": user, "connect": connect, "ql_start": ql_start, "ql_poll": ql_poll, "ql_cancel": ql_cancel,
     "upd_status": upd_status, "upd_check": upd_check, "upd_ack": upd_ack, "upd_restart": upd_restart, "disconnect": disconnect, "friends": friends, "frequests": frequests, "discover": discover, "capi_test": capi_test, "launch": launch,
     "active": active, "dnotify": dnotify, "focus": lambda a: {"ok": focus_hub()}, "heal": lambda a: heal(a["places"]), "hist_del": hist_del, "sober": sober, "sober_set": sober_set, "sober_update": sober_update,
     "sober_kill": sober_kill, "doctor": doctor, "sober_dir": sober_dir, "save": save, "clear": clear, "import": imp,
     "mods_list": mods_list, "mods_preset": mods_preset, "mods_font": mods_font, "mods_put": mods_put, "mods_del": mods_del, "mods_dir": mods_dir,
     "lastseen": lastseen, "lsevents": lsevents, "ls_clear": ls_clear}

class H(BaseHTTPRequestHandler):
    """Serves the UI at /, local images at /local and JSON routes at /api/<name> (GET: query string, POST: JSON body)."""
    def log_message(s, *a): pass
    def ok(s):
        """Accept only requests addressed to localhost; blocks DNS rebinding and cross-site calls."""
        o = s.headers.get("Origin")
        h = s.headers.get("Host", "").split(":")[0]
        return (h in ("127.0.0.1", "localhost") or h.endswith(".localhost")) and (not o or "127.0.0.1" in o or "localhost" in o)
    def out(s, b, t="application/json"):
        b = b if isinstance(b, bytes) else json.dumps(b).encode()
        s.send_response(200); s.send_header("Content-Type", t); s.send_header("Content-Length", str(len(b))); s.end_headers(); s.wfile.write(b)
    def do_GET(s):
        if not s.ok(): return s.send_error(403)
        p = urllib.parse.urlparse(s.path)
        if p.path == "/": return s.out(HTML.encode(), "text/html; charset=utf-8")
        if p.path in ("/favicon.svg", "/favicon.ico"): return s.out(ICON.encode(), "image/svg+xml")
        if p.path == "/local":   # custom CSS cannot load file:// from an http page, so cssFix() rewrites those URLs to here; image files in your home folder only (no dotfolders)
            f = os.path.realpath(os.path.expanduser(dict(urllib.parse.parse_qsl(p.query)).get("p", "")))
            home = os.path.realpath(os.path.expanduser("~")); rel = os.path.relpath(f, home) if f.startswith(home + os.sep) else ""
            mt = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml", ".bmp": "image/bmp"}.get(os.path.splitext(f)[1].lower())
            if not mt or not rel or any(x.startswith(".") for x in rel.split(os.sep)) or not os.path.isfile(f) or os.path.getsize(f) > 40e6: return s.send_error(404)
            return s.out(open(f, "rb").read(), mt)
        s.route(p.path, dict(urllib.parse.parse_qsl(p.query)))
    def do_POST(s):
        if not s.ok(): return s.send_error(403)
        s.route(s.path, json.loads(s.rfile.read(int(s.headers.get("Content-Length", 0))) or "{}"))
    def route(s, p, a):
        try: s.out(R[p[5:]](a))
        except Exception as e: s.out({"error": str(e)})

HTML = r"""<!doctype html><html><head><meta charset=utf-8><title>Sober Hub</title><link rel=icon type="image/svg+xml" href="/favicon.svg">
<style>
:root{--bg:#1a1210;--panel:#271a16;--accent:#ff8a3d;--green:#ff8a3d;--text:#fff0e6;--muted:#9a9ca1;--radius:8px;--font:system-ui;--tile:150px}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px var(--font),sans-serif}
header{position:fixed;top:0;left:0;right:0;height:48px;background:var(--panel);display:flex;align-items:center;gap:14px;padding:0 16px;z-index:5;border-bottom:1px solid #ffffff10}
header .logo{font-weight:800;font-size:19px;letter-spacing:-.5px;white-space:nowrap}header .sp{flex:1}
header input{width:min(380px,40vw);background:#0005;border-radius:20px;padding:7px 14px}
.pill{background:#ffffff12;padding:5px 12px;border-radius:20px;font-weight:600;white-space:nowrap}
nav{position:fixed;top:48px;bottom:0;left:0;width:210px;background:var(--panel);padding:12px 8px;overflow:auto;display:flex;flex-direction:column}
nav a{display:block;padding:10px 14px;border-radius:var(--radius);cursor:pointer;color:var(--muted);font-weight:600;flex:none}
nav a:hover{background:#ffffff0d;color:var(--text)}nav a.on{background:#ffffff14;color:var(--text)}
nav a.so{margin-top:12px;background:var(--green);color:#fff;text-align:center}nav a.so:hover{filter:brightness(1.15)}
.badge{background:#e5484d;color:#fff;border-radius:10px;padding:1px 7px;font-size:12px;margin-left:6px}
#main{margin:48px 0 0 210px;padding:24px 32px}
h2{margin:0 0 14px}h3{margin:22px 0 10px}
.card{background:var(--panel);border-radius:var(--radius);padding:16px;margin-bottom:14px;border:1px solid #ffffff0d}
.rs{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px}
.tile{width:var(--tile);flex:none;cursor:pointer}.tile img{width:var(--tile);height:var(--tile);border-radius:var(--radius);background:#ffffff0d;display:block;transition:.15s}
.tile:hover img{filter:brightness(1.15);transform:scale(1.02)}.tile b{display:block;margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
small{color:var(--muted);display:block;margin-top:3px}.grid{display:grid;grid-template-columns:repeat(auto-fill,var(--tile));gap:14px}
.fr{width:84px;flex:none;text-align:center;cursor:pointer}.fr .ava{position:relative;width:72px;margin:auto}.fr img{width:72px;height:72px;border-radius:50%;background:#ffffff0d}
.fr b{font-size:13px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.fr small{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dot{position:absolute;right:0;bottom:2px;width:17px;height:17px;border-radius:50%;border:3px solid var(--bg)}.s0{display:none}.s1{background:#3b8cff}.s2{background:var(--green)}.s3{background:#f68802}
.btn{background:var(--accent);color:#fff;border:0;padding:9px 18px;border-radius:var(--radius);cursor:pointer;font:600 14px var(--font);margin:3px 4px 3px 0}
.btn:hover{filter:brightness(1.15)}.btn.alt{background:#ffffff14;color:var(--text)}.btn.play{background:var(--green);padding:11px 34px;font-size:16px}.btn.sm{padding:5px 12px;font-size:13px}
input,textarea,select{background:#0005;color:var(--text);border:1px solid #ffffff1f;border-radius:var(--radius);padding:9px;font:inherit}
select{-webkit-appearance:none;appearance:none;background-color:var(--panel);color:var(--text);color-scheme:dark;cursor:pointer;padding-right:30px;background-repeat:no-repeat;background-position:right 11px center;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6'><path d='M0 0l5 6 5-6z' fill='%239a9ca1'/></svg>")}
select:hover{border-color:#ffffff40}select:focus{outline:none;border-color:var(--accent)}
select option{background:#232527;color:#f7f7f8}
textarea{width:100%;font-family:monospace}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.stat{display:inline-block;margin-right:26px}.stat b{font-size:20px;display:block}
.bar{height:8px;border-radius:8px;background:var(--green);margin-top:6px}
table{width:100%;border-collapse:collapse}td{padding:7px;border-bottom:1px solid #ffffff10}
#dlg{position:fixed;inset:0;background:#000b;z-index:60;display:flex;align-items:center;justify-content:center;animation:dlgin .12s ease-out}
#dlg .box{background:var(--panel);border:1px solid #ffffff1a;border-radius:12px;padding:22px 24px 16px;width:min(420px,92vw);box-shadow:0 20px 60px #000a}
#dlg h3{margin:0 0 8px;font-size:17px}#dlg p{margin:0 0 14px;color:var(--muted);white-space:pre-line;line-height:1.45;max-height:55vh;overflow:auto}
#dlg input{width:100%;margin-bottom:14px}#dlg .acts{display:flex;justify-content:flex-end;gap:6px}#dlg .btn{margin:0}
.btn.danger{background:#e5484d}@keyframes dlgin{from{opacity:0;transform:scale(.97)}to{opacity:1;transform:none}}
#toast{position:fixed;bottom:20px;right:20px;background:var(--accent);padding:10px 18px;border-radius:var(--radius);display:none;z-index:99;max-width:380px}
#nts{position:fixed;top:16px;right:16px;display:flex;flex-direction:column;gap:10px;z-index:120;width:min(370px,92vw);pointer-events:none}
.nt{pointer-events:auto;position:relative;overflow:hidden;display:flex;align-items:center;gap:12px;padding:12px 12px 15px;background:linear-gradient(120deg,var(--panel),color-mix(in srgb,var(--accent) 24%,var(--panel)));border:1px solid color-mix(in srgb,var(--accent) 45%,transparent);border-radius:14px;box-shadow:0 14px 40px #000a,0 0 24px color-mix(in srgb,var(--accent) 30%,transparent);cursor:pointer;animation:ntin .45s cubic-bezier(.2,1.2,.3,1)}
.nt.out{animation:ntout .3s ease-in forwards}@keyframes ntin{from{opacity:0;transform:translateX(70px) scale(.92)}to{opacity:1;transform:none}}@keyframes ntout{to{opacity:0;transform:translateX(70px) scale(.95)}}
.ntav{position:relative;flex:none;width:54px}.ntav img{width:54px;height:54px;border-radius:50%;background:#0004;display:block}.nt.play .ntav img{box-shadow:0 0 0 2px var(--green),0 0 16px var(--green);animation:ntp 1.6s ease-in-out 3}@keyframes ntp{50%{box-shadow:0 0 0 3px var(--green),0 0 28px var(--green)}}
.ntx{flex:1;min-width:0;display:flex;flex-direction:column}.ntx b{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ntx span{font-size:13px}.ntx small{display:block;color:var(--muted);font-size:11px;margin-top:2px}
.ntg{width:44px;height:44px;border-radius:10px;flex:none;object-fit:cover}.nta{display:flex;flex-direction:column;gap:4px}
.ntbar{position:absolute;left:0;bottom:0;height:3px;width:100%;background:var(--green);transform-origin:left;animation:ntb var(--d,9s) linear forwards}.nt:hover .ntbar{animation-play-state:paused}@keyframes ntb{to{transform:scaleX(0)}}
.avx{display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap}.avl{width:300px;flex:none;text-align:center;position:sticky;top:0}.avr{flex:1;min-width:340px}
#bwrap{position:relative;border-radius:var(--radius);background:radial-gradient(circle at 50% 35%,color-mix(in srgb,var(--accent) 22%,transparent),transparent 70%);overflow:hidden}#bust{width:100%;min-height:280px;display:block;transition:opacity .25s}
#bwrap.busy #bust{opacity:.35}#bwrap.busy::after{content:"";position:absolute;left:50%;top:50%;width:34px;height:34px;margin:-17px;border:4px solid #fff3;border-top-color:var(--green);border-radius:50%;animation:avs .8s linear infinite}@keyframes avs{to{transform:rotate(360deg)}}
.chip{padding:6px 14px;border-radius:99px;background:#ffffff12;cursor:pointer;font-size:13px;user-select:none}.chip:hover{background:#ffffff24}.chip.on{background:var(--accent);color:#fff}.btn.sel{outline:2px solid var(--green)}
.tile{position:relative;transition:transform .12s}.tile:hover{transform:translateY(-2px)}.tile.on{outline:2px solid var(--green)}.tile.on::after{content:"✓";position:absolute;top:6px;right:6px;width:22px;height:22px;border-radius:50%;background:var(--green);color:#fff;font-size:13px;line-height:22px;text-align:center}
.avw{flex-wrap:nowrap;overflow-x:auto}.avw .tile{width:96px;flex:none}
.av{width:72px;height:72px;border-radius:50%}
.hero{background:linear-gradient(90deg,var(--panel),color-mix(in srgb,var(--accent) 22%,transparent))}.hero img{width:96px;height:96px;border-radius:var(--radius)}details{padding:6px 0;border-bottom:1px solid #ffffff10}summary{cursor:pointer}
.chart{display:flex;align-items:flex-end;gap:6px;height:130px}.chart div{flex:1;text-align:center}.chart i{display:block;background:var(--green);border-radius:4px 4px 0 0;min-height:2px}.chart small{font-size:10px}
.cmd{display:block;padding:8px 12px;border-radius:var(--radius);cursor:pointer}.cmd:hover{background:#ffffff14}#ctx{animation:cx .09s ease-out}#ctx .cmd{padding:7px 12px;font-size:14px}@keyframes cx{from{opacity:0;transform:scale(.97)}}
body.compact #main{padding:14px 18px}body.compact .card{padding:10px;margin-bottom:8px}body.compact nav a{padding:6px 12px}body.compact td{padding:3px 6px}body.compact h3{margin:12px 0 6px}
*{scrollbar-width:thin;scrollbar-color:#ffffff26 transparent}.rs{scrollbar-color:transparent transparent}.rs:hover{scrollbar-color:#ffffff30 transparent}*::-webkit-scrollbar{height:6px;width:8px}*::-webkit-scrollbar-thumb{background:#ffffff26;border-radius:6px}*::-webkit-scrollbar-track,*::-webkit-scrollbar-corner{background:transparent}.rs:not(:hover)::-webkit-scrollbar-thumb{background:transparent}</style><style id=theme></style><style id=user></style></head><body>
<header></header><nav></nav><div id=main></div><div id=toast></div>
<script>
const $=s=>document.querySelector(s);
let S={},tab='home',PT='games',cur=null,FR=[],pendingQ='',PU='',LP=null,RC=[],DISC=[],ACT=[],PREV=null,FQ='',FSORT='status',HQ='',SV=[],SVC=null,SVS='Asc',MT='Inbox',MP=0,SOB={};
let CV=[],CVC=null,CID=null,CVT=null,CLAST='',CUN=-1;
let GM={},PVL=[],MYP=[],PVM={},CTXI=[];
const HEALED=new Set(),ORD={2:0,1:1,3:2,0:3};
// api(route, body?) -> JSON from this hub's backend (POST when a body is given).
const api=(p,b)=>fetch('/api/'+p,b?{method:'POST',body:JSON.stringify(b)}:{}).then(r=>r.json());
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=s=>{s=Math.round(s);const h=s/3600|0,m=s%3600/60|0;return h?h+'h '+m+'m':m+'m '+s%60+'s'};
const ago=t=>{if(!t)return'never';const s=Date.now()/1e3-t;return s<60?'just now':s<3600?(s/60|0)+'m ago':s<86400?(s/3600|0)+'h ago':(s/86400|0)+'d ago'};
const lk=d=>d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
const todaySecs=()=>(S.daily||{})[lk(new Date())]||0;
const toast=(t,ms)=>{const e=$('#toast');e.textContent=t;e.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>e.style.display='none',ms||2500)};
const copy=t=>navigator.clipboard.writeText(t).then(()=>toast('Copied'),()=>toast('Copy failed'));
const pv=v=>{try{return JSON.parse(v)}catch(e){return v}};
// Promise-based modal behind ask() (confirm) and askText() (prompt); Enter confirms, Escape cancels.
function dlg(o){return new Promise(res=>{
 if(dlg.cur)dlg.cur(o.input?null:false);
 const m=document.createElement('div');m.id='dlg';
 m.innerHTML=`<div class=box><h3>${esc(o.title||(o.input?'Sober Hub':'Are you sure?'))}</h3><p>${esc(o.msg)}</p>${o.input?'<input id=dlgi autocomplete=off>':''}<div class=acts>${o.info?'':'<button class="btn alt" id=dlgn>Cancel</button>'}<button class="btn${o.danger?' danger':''}" id=dlgy>${esc(o.ok||'OK')}</button></div></div>`;
 const done=v=>{document.removeEventListener('keydown',kd,true);dlg.cur=null;m.remove();res(v)};
 const yes=()=>done(o.input?$('#dlgi').value:true),no=()=>done(o.input?null:false);
 const kd=e=>{if(e.key=='Escape'){e.preventDefault();e.stopPropagation();no()}else if(e.key=='Enter'&&document.activeElement.id!='dlgn'){e.preventDefault();e.stopPropagation();yes()}};
 document.addEventListener('keydown',kd,true);dlg.cur=done;
 m.onclick=e=>{if(e.target==m)no()};document.body.appendChild(m);
 $('#dlgy').onclick=yes;$('#dlgn')&&($('#dlgn').onclick=no);
 if(o.input){const i=$('#dlgi');i.value=o.def??'';i.focus();i.select()}else $('#dlgy').focus()})}
const ask=(msg,o)=>dlg({msg,...o});
const askText=(msg,def,o)=>dlg({msg,input:1,def,ok:'Create',...o});
const dl=(n,t,m)=>{const a=document.createElement('a');a.href='data:'+(m||'text/plain')+';charset=utf-8,'+encodeURIComponent(t);a.download=n;a.click()};
const greet=()=>{const h=new Date().getHours();return h<5?'Night owl mode':h<12?'Good morning':h<18?'Good afternoon':'Good evening'};
const hist=()=>{const m=new Map();S.history.forEach(x=>{if(!m.has(x.place))m.set(x.place,x)});return [...m.values()]};
const lastSrv=p=>S.history.find(x=>x.place==p&&(x.job||x.code));
const nameOf=p=>{const x=S.play[p]||S.favs.find(f=>f.place==p)||S.history.find(f=>f.place==p)||{};return x.name||String(p)};
function parseLink(s){s=String(s||'').trim();if(!s)return null;const m=s.match(/\/games\/(\d+)/)||s.match(/placeId=(\d+)/i)||s.match(/^(\d{5,})$/);if(!m)return null;
 const c=s.match(/privateServerLinkCode=([\w-]+)/i)||s.match(/linkCode=([\w-]+)/i),j=s.match(/gameInstanceId=([\w-]+)/i);return{place:m[1],code:c&&c[1],job:j&&j[1]}}
const TABS={home:'🏠 Home',games:'🎮 Discover',library:'📚 Library',privs:'🔒 Private Servers',friends:'👥 Friends',chat:'💬 Chats',avatar:'🧍 Avatar',shop:'🛒 Catalog ↗',inbox:'✉️ Messages',groups:'🏘 Communities',profile:'🙂 Profile',history:'🕘 History',stats:'📊 Time Played',rset:'🔧 Roblox Settings',sober:'⚙️ Sober Settings',tweaks:'🧩 Tweaks & Mods',hub:'🧰 Hub Settings',style:'🎨 Style & CSS',data:'💾 Data',account:'🔗 Hub Login'};
const PRESETS={'Roblox Dark':['#18191d','#232527','#335fff','#f7f7f8','#00b06f'],'Roblox Light':['#e4e6ea','#ffffff','#335fff','#1b1e2b','#00b06f'],Amoled:['#000000','#0c0c0c','#00e5a0','#ffffff','#00e5a0'],Rose:['#1a1016','#26171f','#ff6b9d','#ffe8f0','#ff6b9d'],Sunset:['#1a1210','#271a16','#ff8a3d','#fff0e6','#ff8a3d'],Ocean:['#0b1620','#12212f','#2f9bff','#e6f3ff','#19c3a6'],Forest:['#101712','#17221a','#4caf50','#e8f5e9','#8bc34a'],Purple:['#150f1f','#20172e','#9b59ff','#f1e8ff','#c77dff'],Mono:['#111111','#1b1b1b','#888888','#eeeeee','#aaaaaa']};
const TEMPLATE=`/* ===== Sober Hub CSS template — edit & press Apply ===== */
:root{
  --bg:#1a1210;      /* page background */
  --panel:#271a16;   /* header, sidebar, cards */
  --accent:#ff8a3d;  /* buttons */
  --green:#ff8a3d;   /* Play button, bars, online dot */
  --text:#fff0e6;
  --muted:#9a9ca1;
  --radius:8px;
}
/* Selectors: header, nav, nav a.on, nav a.so, #main, h2, h3, .card, .tile, .tile img, .fr img, .dot, .btn, .btn.play, .pill, input, table, .bar */
body{ /* background-image:url(file:///home/you/wall.jpg); background-size:cover; */ }
.card{ /* backdrop-filter:blur(10px); box-shadow:0 8px 30px #0006; */ }
.tile img{ /* border-radius:50%; */ }
.btn.play{ /* text-transform:uppercase; letter-spacing:.08em; */ }
nav{ /* width:250px; */ }
`;
// Custom CSS can't load file:// images from an http page; route them through the backend's /local endpoint.
const cssFix=s=>String(s||'').replace(/url\(\s*(['"]?)file:\/\/([^)'"]+)\1\s*\)/gi,(m,q,p)=>{try{p=decodeURIComponent(p)}catch(e){}return 'url("/local?p='+encodeURIComponent(p)+'")'});
// Theme variables go in their own <style id=theme> placed BEFORE <style id=user>, so custom CSS can override them (inline styles could not be).
function apply(){const t=S.theme;let c=':root{';for(const k in t)c+='--'+k+':'+t[k]+(k=='radius'||k=='tile'?'px':'')+';';$('#theme').textContent=c+'}';document.body.classList.toggle('compact',t.compact=='1');$('#user').textContent=cssFix(S.css)}
const actHTML=()=>ACT.length?`<span class=pill id=act style="background:var(--green);cursor:pointer" onclick="openGame(${ACT[0].place})">▶ ${esc(ACT[0].name)} · ${fmt(ACT[0].secs)}</span>`:'<span id=act></span>';
async function load(){S=await api('state');apply();
 $('header').innerHTML=`<span class=logo>🧊 Sober Hub</span><input id=ts placeholder="Search games, paste a link or ID… (Ctrl+K)" onkeydown="if(event.key=='Enter'){const l=parseLink(this.value);if(l)openGame(l.place);else{pendingQ=this.value;go('games')}}"><span class=sp></span>${actHTML()}${S.user&&S.user.robux!=null?`<span class=pill>R$ ${S.user.robux.toLocaleString()}</span>`:''}${S.user?`<img src="${esc(S.user.avatar)}" style="width:32px;height:32px;border-radius:50%;cursor:pointer" onclick="go('account')">`:`<span class=pill style="cursor:pointer" onclick="go('account')">Connect</span>`}`;
 $('nav').innerHTML=Object.entries(TABS).map(([k,v])=>`<a data-t=${k} class="${k==tab?'on':''}" onclick="go('${k}')">${v}</a>`).join('')+`<a class=so onclick="openSober()">🚀 Open Sober</a>`;
 badge();
 if(tab!='game'){$('#main').innerHTML=await PAGES[tab]();AFTER[tab]&&AFTER[tab]()}}
const go=t=>{if(t=='shop'){open_('https://www.roblox.com/catalog');return}tab=t;load()};
const openSober=()=>api('launch',{}).then(r=>toast(r.error||'Opening Sober…'));
const save=async(p,q)=>{await api('save',p);S=await api('state');apply();if(!q)toast('Saved')};
async function badge(){if(!S.connected)return;const r=await rb('https://friends.roblox.com/v1/user/friend-requests/count'),n=r&&r.count,a=document.querySelector('nav a[data-t=friends]');if(a&&n)a.innerHTML=TABS.friends+` <span class=badge>${n}</span>`;
 const m=await rb(CHAT+'get-conversation-metadata'),u=m&&m.global_unread_count,c=document.querySelector('nav a[data-t=chat]');if(c&&u)c.innerHTML=TABS.chat+` <span class=badge>${u}</span>`}
async function chatWatch(){if(!S.connected)return;const m=await rb(CHAT+'get-conversation-metadata'),n=m&&m.global_unread_message_count;if(n==null)return;if(CUN>=0&&n>CUN&&tab!='chat')notify('New Roblox message',n-CUN+' unread — open Chats');CUN=n}
function notify(t,b){toast(b?t+' — '+b:t,6000);try{if('Notification' in window&&Notification.permission=='granted')new Notification(t,{body:b||''})}catch(e){}}
let NG={};
async function gIcon(p){if(!p)return'';if(NG[p]!==undefined)return NG[p];NG[p]='';try{const r=await rb(`https://thumbnails.roblox.com/v1/places/gameicons?placeIds=${p}&returnPolicy=PlaceHolder&size=150x150&format=Png`);NG[p]=(r.data&&r.data[0]&&r.data[0].imageUrl)||''}catch(e){}return NG[p]}
function chime(up){try{const A=chime.c||(chime.c=new(window.AudioContext||window.webkitAudioContext)());A.resume&&A.resume();const t=A.currentTime;[[660,0],[880,.11],[up?1175:990,.22]].forEach(([f,d])=>{const o=A.createOscillator(),g=A.createGain();o.type='sine';o.frequency.value=f;g.gain.setValueAtTime(0,t+d);g.gain.linearRampToValueAtTime(.12,t+d+.02);g.gain.exponentialRampToValueAtTime(.0001,t+d+.35);o.connect(g);g.connect(A.destination);o.start(t+d);o.stop(t+d+.4)})}catch(e){}}
// ---- last online / activity helpers
const agoS=t=>{const s=Math.max(0,Date.now()/1000-t);if(s<60)return'just now';const m=Math.floor(s/60);if(m<60)return m+' min ago';const h=Math.floor(m/60);if(h<24)return h+(h==1?' hour ago':' hours ago');const d=Math.floor(h/24);if(d<30)return d+(d==1?' day ago':' days ago');const mo=Math.floor(d/30);return mo<12?mo+(mo==1?' month ago':' months ago'):Math.floor(d/365)+' yr ago'};
const seenTxt=f=>f.seen?'Last online '+agoS(f.seen)+(f.sing&&f.sgame?' - was playing '+f.sgame:''):'Last online: not seen yet';
const inQuiet=()=>{const o=S.opts||{};if(o.qon!='1')return false;const m=t=>{const [h,mi]=String(t||'').split(':').map(Number);return h*60+(mi||0)},n=new Date(),c=n.getHours()*60+n.getMinutes(),a=m(o.qs),b=m(o.qe);return a==b?false:a<b?(c>=a&&c<b):(c>=a||c<b)};
const EVK={play:'started playing',online:'came online',offline:'went offline'};
async function evDraw(){const el=$('#fev');if(!el)return;const r=await api('lsevents');if(!$('#fev'))return;const l=Array.isArray(r)?r:[];
 el.innerHTML=`<h3>Recent activity <button class="btn alt sm" onclick="evClear()">Clear</button></h3><div class=card>${l.length?`<table>${l.slice(0,40).map(e=>`<tr><td><b>${esc(e.n)}</b> ${EVK[e.k]||esc(e.k)}${e.k=='play'&&e.g?' <i>'+esc(e.g)+'</i>':e.k=='offline'&&e.g?' (was playing <i>'+esc(e.g)+'</i>)':''}</td><td style="text-align:right;white-space:nowrap"><small style="margin:0" title="${esc(new Date(e.t*1000).toLocaleString())}">${agoS(e.t)}</small></td></tr>`).join('')}</table>`:'<small>Nothing yet. Sober Hub logs friend activity while it is open.</small>'}</div>`}
async function evClear(){if(!await ask('Clear the activity log and every saved last-online time? They build up again while Sober Hub is open.',{ok:'Clear',danger:1,title:'Clear friend activity'}))return;await api('ls_clear',{});toast('Cleared');go('friends')}
// Immersive friend notification: animated card (avatar, game icon, Join), a chime, and a real desktop notification when the hub is not in front.
async function fnote(f,kind){
 const o=S.opts||{},play=kind=='play',gi=play?await gIcon(f.place):'',gname=f.where||'a game';
 if(o.nsound!='0')chime(play);
 let host=$('#nts');if(!host){host=document.createElement('div');host.id='nts';document.body.appendChild(host)}
 while(host.children.length>=4)host.firstChild.remove();
 const el=document.createElement('div');el.className='nt '+(play?'play':'online');
 el.innerHTML=`<div class=ntav><img src="${esc(f.avatar||'')}"><span class="dot s${f.status}"></span></div><div class=ntx><b>${esc(f.display)}</b><span>${play?'is playing <i>'+esc(gname)+'</i>':'just came online'}</span><small>${play?'Click to view · Join to hop in':'Click to view profile'}</small></div>${gi?`<img class=ntg src="${esc(gi)}">`:''}<div class=nta>${play&&f.place?'<button class="btn sm" data-j>▶ Join</button>':''}<button class="btn alt sm" data-x>✕</button></div><i class=ntbar></i>`;
 const close=()=>{el.classList.add('out');setTimeout(()=>el.remove(),320)};
 el.onclick=e=>{if(e.target.closest('[data-x]'))return close();if(e.target.closest('[data-j]')){joinFriend(f.id);return close()}showFriend(f.id);close()};
 host.appendChild(el);setTimeout(close,9000);
 if(o.ndesk!='0'&&(document.hidden||!document.hasFocus())){
  const title=play?f.display+' is playing':f.display+' came online',body=play?gname:'',r=await api('dnotify',{title,body,icon:f.avatar||''});
  if(!r.ok){try{if('Notification' in window&&Notification.permission=='granted')new Notification(title,{body,icon:f.avatar||undefined})}catch(e){}}}}
const ntest=()=>{const u=S.user||{};fnote({id:u.id||0,display:'Test Friend',avatar:u.avatar||'',status:2,place:null,where:'Notifications work!'},'play')};
const setOpt=(k,v)=>save({opts:{[k]:v}},1);
let HADACT=false;
async function pollActive(){const r=await api('active');ACT=Array.isArray(r)?r:[];breakCheck();const e=$('#act');if(e)e.outerHTML=actHTML();
 if(HADACT&&!ACT.length){try{window.focus()}catch(x){}api('focus',{});toast('Welcome back to Sober Hub',4000);load()}HADACT=ACT.length>0}
function setTitle(l){const n=l.filter(f=>f.status==2).length;document.title=(n?`(${n}) `:'')+'Sober Hub'}
async function watch(){if(!S.connected||!S.opts)return;const l=await api('friends');if(!Array.isArray(l)||!l.length)return;setTitle(l);if(S.opts.notify=='off'){PREV=null;return}
 const pn=new Set(S.pinned||[]),mode=S.opts.notify,np={};l.forEach(f=>np[f.id]={s:f.status,p:f.place});
 // presence sometimes fails for a whole batch and everybody looks offline: ignore that poll instead of announcing a flood of "came online"
 if(PREV){const was=Object.values(PREV).filter(x=>x.s>0).length,now=l.filter(f=>f.status>0).length;if(was>3&&now==0)return}
 if(PREV&&!inQuiet())for(const f of l){const o=PREV[f.id];if(!o)continue;if(mode=='pinned'&&!pn.has(f.id))continue;
  if(f.status==2&&(o.s!=2||o.p!=f.place))await fnote(f,'play');
  else if(f.status==1&&o.s==0&&S.opts.nonline!='0')await fnote(f,'online')}
 PREV=np}
const tile=(g,sub)=>(GM[g.place]=g,`<div class=tile onclick="openGame(${g.place})" oncontextmenu="gameMenu(event,${g.place})"><img loading=lazy src="${esc(g.icon||'')}" onerror="this.style.opacity=.2"><b>${esc(g.name||g.place)}</b><small>${sub||''}</small></div>`);
const dtile=g=>tile(g,`${g.like!=null?'👍 '+g.like+'% · ':''}👤 ${(g.playing||0).toLocaleString()}`);
function setFriends(l){const pn=new Set(S.pinned||[]);l.sort((a,b)=>(pn.has(b.id)-pn.has(a.id))||((ORD[a.status]??3)-(ORD[b.status]??3)));FR=l;setTitle(l);return l}
const frs=l=>l.map(f=>`<div class=fr onclick="showFriend(${f.id})" oncontextmenu="friendMenu(event,${f.id})"><div class=ava><img src="${esc(f.avatar||'')}"><span class="dot s${f.status}"></span></div><b>${(S.pinned||[]).includes(f.id)?'⭐ ':''}${esc(f.display)}</b><small>${f.status==2?esc(f.where||'In game'):f.status==1?'Online':f.status==0&&f.seen?esc(agoS(f.seen)):''}</small></div>`).join('')||'<small>No friends found</small>';
async function play(place,name,icon,job,code){const r=await api('launch',{place,name,icon,job,code});if(!r.error&&(S.later||[]).some(x=>x.place==place))save({later:S.later.filter(x=>x.place!=place)},1);toast(r.error?r.error:'Launching in Sober…');setTimeout(load,900);setTimeout(pollActive,2500)}
function pl(place,job,code){const g=cur&&cur.place==place?cur:(S.favs.find(f=>f.place==place)||S.history.find(f=>f.place==place)||{});play(place,g.name||'',g.icon||'',job||null,code||null)}
function joinFriend(id){const f=FR.find(x=>x.id==id);if(!f)return;if(f.status==2&&f.place){play(f.place,f.where||'',null,f.job)}else toast(f.status==2?'Game not joinable':'Friend is not in a game')}
function heroHTML(g,label){LP=g;const l=lastSrv(g.place),p2=S.play[g.place];
 return `<div class="card hero"><div class=row style="gap:16px"><img src="${esc(g.icon||'')}" onerror="this.style.opacity=.2"><div style="flex:1;min-width:200px"><small>${esc(label)}</small><h2 style="margin:0">${esc(g.name||g.place)}</h2><small>${g.playing!=null?'👤 '+g.playing.toLocaleString()+' playing now · ':''}${p2?fmt(p2.secs)+' played via Sober Hub':'Not played via Sober Hub yet'}</small></div><button class="btn play" onclick="play(LP.place,LP.name,LP.icon)">▶ Play in Sober</button>${l?`<button class="btn alt" onclick="pl(${g.place},'${esc(l.job||'')}','${esc(l.code||'')}')">↩ Last server</button>`:''}<button class="btn alt" onclick="openGame(${g.place})">Details</button></div></div>`}
const contHTML=()=>{const h=hist(),seen=new Set(h.map(x=>String(x.place))),ex=RC.filter(g=>!seen.has(String(g.place))&&!hidSet().has(String(g.place)));
 const all=[...h.slice(0,20).map(g=>tile(g,ago(g.ts))),...ex.map(g=>tile(g,'On your Roblox account'))];return all.length?`<h3>Continue</h3><div class=rs>${all.join('')}</div>`:''};
function refreshHome(){const hd=$('#hero');if(!hd)return;const h=hist(),useR=RC[0]&&(S.opts.hero=='roblox'||!h.length);
 hd.innerHTML=useR?heroHTML(RC[0],'Jump back in · first in your Roblox Continue list'):h[0]?heroHTML(h[0],'Jump back in · last launched from Sober Hub · '+ago(h[0].ts)):'';const c=$('#cont');c&&(c.innerHTML=contHTML())}
async function healMissing(){const need=[...S.history.slice(0,60),...S.favs].filter(x=>(!x.icon||!x.name||x.name==String(x.place))&&!HEALED.has(x.place)).map(x=>x.place);if(!need.length)return;
 need.forEach(p=>HEALED.add(p));await api('heal',{places:[...new Set(need)].slice(0,100)});S=await api('state');if(tab=='home'){refreshHome();const f=$('#fav');f&&(f.innerHTML=S.favs.map(g=>tile(g)).join(''))}}
// Page registry: PAGES[tab]() returns the page's HTML, then AFTER[tab]() (below) fills in anything that loads asynchronously.
const PAGES={
home(){const h=hist(),lj=h[0]&&lastSrv(h[0].place),pl0=Object.values(S.play),tot=pl0.reduce((a,b)=>a+b.secs,0);
 return `<h2>${greet()}${S.user?', '+esc(S.user.display):''}</h2><div id=hero></div>
 <div class=card><span class=stat><b>${fmt(todaySecs())}</b>today</span><span class=stat><b>${fmt(tot)}</b>total</span><span class=stat><b>${pl0.reduce((a,b)=>a+b.launches,0)}</b>launches</span><span class=stat><b>${S.favs.length}</b>favorites</span><span class=stat><b id=fo>${S.connected?'…':'—'}</b>friends in game</span></div>
 ${S.connected?'<h3 style="margin-top:0">Friends</h3><div id=fr class=rs>Loading…</div>':`<div class=card>🔗 <b>Connect your Roblox account</b> to see friends, who's in-game, join them, and your Robux. <button class=btn onclick="go('home');go('account')">Connect</button></div>`}
  <div class=row>${lj?`<button class="btn alt" onclick="pl(${lj.place},'${esc(lj.job||'')}','${esc(lj.code||'')}')">↩ Rejoin last server</button><button class="btn alt" onclick="hop(${lj.place})">🔀 Hop to a new server</button>`:''}${S.favs.length?`<button class="btn alt" onclick="rnd()">🎲 Random favorite</button>`:''}<button class="btn alt" onclick="cmdk()">⌨ Quick jump (Ctrl+K)</button></div>
 <div id=cont></div>${(S.later||[]).length?`<h3>🕒 Play later</h3><div class=rs>${S.later.map(g=>tile(g)).join('')}</div>`:''}${S.favs.length?`<h3>Favorites</h3><div id=fav class=rs>${S.favs.map(g=>tile(g)).join('')}</div>`:''}<div id=disc><small>Loading recommendations…</small></div>`},
friends(){return S.connected?`<h2>Friends <small id=fsum style="display:inline"></small></h2>
 <small style="margin:0 0 10px">You can only add trusted friends from <a style="cursor:pointer;color:var(--accent)" onclick="open_('https://www.roblox.com/users/friends#!/friend-requests')">https://www.roblox.com/users/friends#!/friend-requests</a></small>
 <div class=row><input id=fq value="${esc(FQ)}" placeholder="Filter friends or games…" style="flex:1;min-width:200px" oninput="FQ=this.value;renderFriends()"><select onchange="FSORT=this.value;renderFriends()"><option value=status ${FSORT=='status'?'selected':''}>Sort: status</option><option value=name ${FSORT=='name'?'selected':''}>Sort: name</option></select><button class="btn alt" onclick="go('friends')">↻ Refresh</button></div>
 <div class=row style="margin-top:8px"><input id=addfr placeholder="Add friend by username" onkeydown="if(event.key=='Enter')addFriend()"><button class=btn onclick="addFriend()">Send request</button></div>
 <details style="margin-top:8px"><summary>Add several people at once</summary><textarea id=addmany rows=3 placeholder="Usernames separated by spaces, commas or new lines"></textarea><button class=btn onclick="addMany()">Send requests</button></details>
<div id=rq></div><div id=fl>Loading…</div><div id=fev></div>`:`<div class=card>Connect your account first. <button class=btn onclick="go('account')">Connect</button></div>`},
games(){return `<h2>Discover</h2><div class=card><b>Quick launch</b><small>Paste a roblox.com/games link (private-server links work), a roblox:// link or a place ID</small><div class=row style="margin-top:8px"><input id=ql placeholder="https://www.roblox.com/games/…" style="flex:1;min-width:240px" onkeydown="if(event.key=='Enter')quick(1)"><button class="btn play" style="padding:9px 22px;font-size:14px" onclick="quick(1)">▶ Launch</button><button class="btn alt" onclick="quick(0)">Details</button><button class="btn alt" onclick="surprise()">🎲 Surprise me</button></div></div>
 <div class=row><input id=gs value="${esc(pendingQ)}" placeholder="Search Roblox games…" style="flex:1" onkeydown="if(event.key=='Enter')gsearch()"><button class=btn onclick="gsearch()">Search</button></div><div id=res class=grid style="margin-top:14px"></div>`},
library(){const C=S.collections||{},CK=Object.keys(C),nt=Object.entries(S.notes||{}).filter(([,v])=>v&&v.trim());
 const lt=(g,b)=>(GM[g.place]=g,`<div class=tile oncontextmenu="gameMenu(event,${g.place})"><img loading=lazy src="${esc(g.icon||'')}" onclick="openGame(${g.place})" onerror="this.style.opacity=.2"><b>${esc(g.name||g.place)}</b><div>${b}</div></div>`);
 return `<h2>Library</h2><h3 style="margin-top:0">⭐ Favorites (${S.favs.length})</h3><div class=grid>${S.favs.map((g,i)=>lt(g,`<button class="btn sm" onclick="pl(${g.place})">Play</button><button class="btn alt sm" title="Move to front" onclick="favFirst(${i})">↑</button><button class="btn alt sm" onclick="favDel(${g.place})">✕</button>`)).join('')||'<small>Open a game and press ☆ Favorite to add it here.</small>'}</div>
 ${(S.later||[]).length?`<h3>🕒 Play later (${S.later.length})</h3><div class=grid>${S.later.map(g=>lt(g,`<button class="btn sm" onclick="pl(${g.place})">Play</button><button class="btn alt sm" onclick="laterDel(${g.place})">✕</button>`)).join('')}</div>`:''}
 ${CK.map((n,ci)=>`<h3>📁 ${esc(n)} (${C[n].length}) <button class="btn alt sm" onclick="delColl(${ci})">Delete collection</button></h3><div class=grid>${C[n].map((g,gi)=>lt(g,`<button class="btn sm" onclick="pl(${g.place})">Play</button><button class="btn alt sm" onclick="rmColl(${ci},${gi})">✕</button>`)).join('')||'<small>Empty</small>'}</div>`).join('')}
 <div class=row style="margin-top:16px"><button class="btn alt" onclick="newColl()">＋ New collection</button></div>
 ${nt.length?`<h3>📝 Notes</h3><div class=card>${nt.map(([p,v])=>`<details><summary onclick="openGame(${p})" style="cursor:pointer"><b>${esc(nameOf(p))}</b></summary><small>${esc(v)}</small></details>`).join('')}</div>`:''}`},
chat(){return needConn()||`<h2>Chats ${Object.keys(S.hidden_chat||{}).length?`<button class="btn alt sm" onclick="go('hub')">🙈 ${Object.keys(S.hidden_chat||{}).length} hidden · manage</button>`:''}</h2><div style="display:flex;gap:14px;align-items:flex-start"><div class=card id=cvl style="width:300px;flex:none;height:72vh;overflow:auto;padding:8px">Loading…</div><div class=card id=cvp style="flex:1;min-width:0;height:72vh;display:flex;flex-direction:column"><small>Pick a chat on the left, or press 💬 next to a friend.</small></div></div>`},
history(){return `<h2>History</h2><div class=row><input id=hq value="${esc(HQ)}" placeholder="Filter history…" style="flex:1" oninput="HQ=this.value;rHist()"><button class="btn alt" onclick="ask('Clear all history?',{ok:'Clear',danger:1}).then(y=>y&&api('clear',{what:['history']}).then(load))">Clear history</button></div><div class=card id=hl style="margin-top:12px"></div>`},
stats(){const e=Object.entries(S.play).sort((a,b)=>b[1].secs-a[1].secs),mx=e[0]?e[0][1].secs||1:1,tot=e.reduce((a,b)=>a+b[1].secs,0),D=S.daily||{};
 const days=[];for(let i=13;i>=0;i--){const d=new Date(Date.now()-i*864e5);days.push([d,D[lk(d)]||0])}
 const mxd=Math.max(...days.map(x=>x[1]),1),wk=days.slice(7).reduce((a,b)=>a+b[1],0);
 let st=0;for(let i=0;i<400;i++){const v=D[lk(new Date(Date.now()-i*864e5))]||0;if(v>0)st++;else if(i>0)break}
 return `<h2>Time played</h2><div class=card><div class=stat><b>${fmt(tot)}</b>total</div><div class=stat><b>${fmt(wk)}</b>last 7 days</div><div class=stat><b>${fmt(todaySecs())}</b>today</div><div class=stat><b>${st} day${st==1?'':'s'}</b>play streak</div><div class=stat><b>${e.reduce((a,b)=>a+b[1].launches,0)}</b>launches</div>${e[0]?`<div class=stat><b>${esc(e[0][1].name||e[0][0])}</b>most played</div>`:''}</div>
 <div class=card><b>Last 14 days</b><div class=chart style="margin-top:10px">${days.map(([d,v])=>`<div title="${lk(d)} · ${fmt(v)}"><i style="height:${Math.round(v/mxd*100)}px"></i><small>${['S','M','T','W','T','F','S'][d.getDay()]}${d.getDate()}</small></div>`).join('')}</div></div>
 <div class=row style="margin-bottom:10px"><button class="btn alt" onclick="expCSV()">Export playtime CSV</button></div>
 ${e.map(([p,v])=>`<div class=card><div class=row><b style="flex:1">${esc(v.name||p)}</b><button class="btn sm" onclick="pl(${p})">Play</button></div><small>${fmt(v.secs)} · ${v.launches} launches · longest ${fmt(v.longest||0)} · last ${ago(v.last)}</small><div class=bar style="width:${v.secs/mx*100}%"></div></div>`).join('')}<small>Counts while the Sober process launched from the hub is running.</small>`},
async sober(){const s=await api('sober'),c=s.cfg||{},ff=c.fflags||{};SOB=ff;
 const tg=(k,l)=>`<label style="display:block;margin:6px 0"><input type=checkbox ${c[k]?'checked':''} onchange="api('sober_set',{${k}:this.checked}).then(()=>toast('Saved'))"> ${l}</label>`;
 return `<h2>Sober settings</h2><div class=card>Status: <b>${s.installed?'✅ Sober installed':'❌ Not found'}</b>${s.version?' · v'+esc(s.version):''}${s.running?' · 🟢 running':''}${s.installed?'':'<small>sudo apt install flatpak, then: flatpak install flathub org.vinegarhq.Sober</small>'}<small>${esc(s.path)}</small>
 <div class=row style="margin-top:8px"><button class=btn onclick="soberUpd(this)">⬆ Update Sober</button><button class="btn alt" onclick="api('sober_kill',{}).then(()=>{toast('Sober stopped');go('sober')})">⏹ Stop Sober</button><button class="btn alt" onclick="api('sober_dir',{})">📂 Config folder</button></div></div>
 <div class=card>${tg('discord_rpc_enabled','Discord Rich Presence')}${tg('use_opengl','Use OpenGL renderer')}${tg('bring_back_oof','Bring back old death sound')}${tg('close_on_leave','Close Sober when leaving a game')}${tg('allow_gamepad_permission','Allow gamepad access')}${tg('enable_hidpi','HiDPI scaling')}${tg('touch_mode','Touch mode')}${tg('server_location_indicator_enabled','Server location indicator')}
 <div class=row style="margin-top:10px">FPS cap <input id=fps type=number value="${ff.DFIntTaskSchedulerTargetFps||''}" placeholder=60 style="width:90px"><button class=btn onclick="api('sober_set',{fflags:{DFIntTaskSchedulerTargetFps:+$('#fps').value}}).then(()=>toast('Saved'))">Set</button></div></div>
 <div class=card><b>Fast Flags</b><small>Advanced — wrong flags can break Sober. Values: true / false, numbers or text.</small><table>${Object.entries(ff).map(([k,v],i)=>`<tr><td><code>${esc(k)}</code></td><td>${esc(JSON.stringify(v))}</td><td style="text-align:right"><button class="btn alt sm" onclick="ffDel(${i})">Remove</button></td></tr>`).join('')||'<tr><td><small>None set</small></td></tr>'}</table>
 <div class=row style="margin-top:8px"><input id=ffk placeholder="Flag name" style="flex:1"><input id=ffv placeholder=Value style="width:140px"><button class=btn onclick="ffAdd()">Add / update</button></div></div>
 <div class=card><b>Launch command</b><div class=row><input id=cmd value="${esc(S.opts.cmd)}" style="flex:1"><button class=btn onclick="save({opts:{cmd:$('#cmd').value}})">Save</button></div>
 <div class=row style="margin-top:6px"><small style="margin:0">Presets:</small>${[['Default','flatpak run org.vinegarhq.Sober'],['GameMode','gamemoderun flatpak run org.vinegarhq.Sober'],['Dedicated GPU','env DRI_PRIME=1 flatpak run org.vinegarhq.Sober']].map(([n,c2])=>`<button class="btn alt sm" onclick="$('#cmd').value='${c2}'">${n}</button>`).join('')}</div></div>`},
hub(){const o=S.opts;return `<h2>Hub settings</h2>
 <div class=card><b>Recommendations</b><small>Better = trending + hidden gems + picks matched to your play history, built from the community-made Rolimons game list (your history never leaves this computer). Roblox = Roblox's own Discover sorts. Falls back to Roblox if the community API is down.</small><select onchange="save({opts:{recs:this.value}});DISC=[]"><option value=better ${o.recs!='roblox'?'selected':''}>Better recommendations (community API)</option><option value=custom ${o.recs=='custom'?'selected':''}>My own API (set up below)</option><option value=roblox ${o.recs=='roblox'?'selected':''}>Normal Roblox recommendations</option></select>
  <details ${o.recs=='custom'?'open':''} style="margin-top:10px"><summary><b>My own API</b></summary>
  <small>Your API must return JSON: a list of games, <code>{"games":[...]}</code>, or <code>{"sections":[{"name":"...","games":[...]}]}</code>. Each game needs a place ID (<code>placeId</code>, <code>place</code>, <code>rootPlaceId</code> or <code>id</code>); name, icon, <code>playing</code> and <code>like</code> are optional (missing names and icons are looked up). Placeholders you can use in the URL or body: <code>{userId} {username} {history} {favs}</code> - nothing about you is sent unless you use them. Your Roblox login is never sent. If it fails, the hub falls back to the built-in better recommendations.</small>
  <div class=row style="margin-top:8px"><select id=cam><option ${(S.capi||{}).method!='POST'?'selected':''}>GET</option><option ${(S.capi||{}).method=='POST'?'selected':''}>POST</option></select><input id=cau placeholder="https://example.com/recs?user={userId}" value="${esc((S.capi||{}).url||'')}" style="flex:1;min-width:260px"></div>
  <textarea id=cah rows=2 placeholder="Headers, one per line:&#10;Authorization: Bearer abc123" style="margin-top:8px">${esc((S.capi||{}).headers||'')}</textarea>
  <textarea id=cab rows=3 placeholder='POST body, e.g. {"user": "{username}", "recent": "{history}"}' style="margin-top:8px">${esc((S.capi||{}).body||'')}</textarea>
  <div class=row style="margin-top:8px"><input id=cap placeholder="Path to list (optional), e.g. data.games" value="${esc((S.capi||{}).path||'')}" style="flex:1;min-width:220px"><button class=btn onclick="capiSave()">Save</button><button class="btn alt" onclick="capiTest()">Save &amp; test</button></div>
  <small id=capir></small></details></div>
 <div class=card><b>Home hero (top game)</b><small>Which game is shown at the top of Home.</small><select onchange="save({opts:{hero:this.value}})"><option value=hub ${o.hero!='roblox'?'selected':''}>Last game I launched from Sober Hub</option><option value=roblox ${o.hero=='roblox'?'selected':''}>First game in my Roblox Continue list</option></select></div>
 <div class=card><b>Friend notifications</b><small>Animated pop-up cards when friends start playing or come online (checked every 30 seconds while the hub is open). Pinned = only friends you starred on the Friends page.</small><div class=row style="margin-top:8px"><select onchange="setNotify(this.value)">${[['off','Off'],['pinned','Pinned friends only'],['all','All friends']].map(([v,l])=>`<option value=${v} ${o.notify==v?'selected':''}>${l}</option>`).join('')}</select><button class="btn alt sm" onclick="ntest()">🔔 Test notification</button></div>
  ${[['nonline','Also notify when they come online'],['nsound','Play a chime'],['ndesk','Desktop notification when the hub is in the background']].map(([k,l])=>`<label style="display:block;margin:6px 0"><input type=checkbox ${o[k]!='0'?'checked':''} onchange="setOpt('${k}',this.checked?'1':'0')"> ${l}</label>`).join('')}</div>
 <div class=card><b>Quiet hours</b><small>No friend pop-ups, chimes or desktop alerts during these hours. Last-online tracking keeps running.</small><label style="display:block;margin:8px 0"><input type=checkbox ${o.qon=='1'?'checked':''} onchange="setOpt('qon',this.checked?'1':'0')"> Enable quiet hours</label><div class=row><input type=time value="${esc(o.qs||'23:00')}" onchange="setOpt('qs',this.value)"><span>to</span><input type=time value="${esc(o.qe||'07:00')}" onchange="setOpt('qe',this.value)"></div></div>
 <div class=card><b>Games</b><label style="display:block;margin:6px 0"><input type=checkbox ${o.backhub!='0'?'checked':''} onchange="setOpt('backhub',this.checked?'1':'0')"> Return to Sober Hub when I leave a game <small style="display:inline">- Sober closes instead of showing its own home screen (only for games started from the hub; your own Sober setting is restored afterwards)</small></label></div>
 <div class=card><b>Updates</b><small>Sober Hub v${esc(S.version||'')} · checks GitHub on start and every 6 hours. A newer sober_hub.py in the repo is installed automatically (the old file is kept as sober_hub.py.bak).</small><label class=row style="margin:8px 0"><input type=checkbox ${o.autoupd!='0'?'checked':''} onchange="save({opts:{autoupd:this.checked?'1':'0'}})"> Update automatically</label><button class=btn onclick="updNow(this)">Check for updates now</button><details open style="margin-top:12px"><summary><b>Update log</b></summary>${(S.changelog||[]).map(c=>`<b style="display:block;margin-top:8px">v${esc(c.v)}</b><ul style="margin:4px 0 0 18px">${c.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`).join('')}</details></div>
 <div class=card><b>Healthy play</b><small>Gentle reminders while a game launched from the hub is running. They show in the hub and as a desktop alert.</small><label style="display:block;margin:8px 0"><input type=checkbox ${o.brk=='1'?'checked':''} onchange="setOpt('brk',this.checked?'1':'0')"> Remind me to take a break</label>
  <div class=row>Every <select onchange="setOpt('brkmin',this.value)">${[30,45,60,90,120].map(m=>`<option value=${m} ${(+o.brkmin||60)==m?'selected':''}>${m} min</option>`).join('')}</select> of playing · Daily limit <select onchange="setOpt('lim',this.value)">${[[0,'Off'],[60,'1 hour'],[120,'2 hours'],[180,'3 hours'],[240,'4 hours']].map(([m,l])=>`<option value=${m} ${(+o.lim||0)==m?'selected':''}>${l}</option>`).join('')}</select><button class="btn alt sm" onclick="remind('Time for a break','This is what a reminder looks like.')">Test</button></div></div>
 <div class=card><b>🩺 Health check</b><small>Tests Sober, your Roblox session, the connection and the optional tools some features rely on.</small><button class=btn onclick="doctor(this)">Run health check</button><div id=docr style="margin-top:10px"></div></div>
 ${hiddenCard()}<div class=card><b>Keyboard shortcuts</b><small><code>Ctrl+K</code> quick jump to tabs/games/links · <code>/</code> focus search · <code>Esc</code> close pop-ups</small></div>`},
style(){return `<h2>Style & CSS</h2><div class=card><b>Presets</b><div class=row>${Object.keys(PRESETS).map(k=>`<button class="btn alt" onclick="preset('${k}')">${k}</button>`).join('')}</div>
 <div class=row style="margin-top:10px">${['bg','panel','accent','green','text'].map(k=>`${k} <input type=color value="${S.theme[k]}" onchange="save({theme:{${k}:this.value}},1)">`).join(' ')} radius <input type=range min=0 max=30 value=${S.theme.radius} oninput="save({theme:{radius:this.value}},1)"> font <select onchange="save({theme:{font:this.value}},1)">${['system-ui','monospace','Georgia','Verdana'].map(f=>`<option ${S.theme.font==f?'selected':''}>${f}</option>`).join('')}</select></div>
 <div class=row style="margin-top:10px">Tile size <input type=range min=100 max=240 value=${S.theme.tile} oninput="save({theme:{tile:this.value}},1)"> <label><input type=checkbox ${S.theme.compact=='1'?'checked':''} onchange="save({theme:{compact:this.checked?'1':'0'}},1)"> Compact layout</label></div></div>
 <div class=card><b>Custom CSS</b><small>Applies live. Overrides everything above.</small><textarea id=css rows=16 oninput="$('#user').textContent=cssFix(this.value)">${esc(S.css)}</textarea>
 <button class=btn onclick="save({css:$('#css').value})">Apply & save</button><button class="btn alt" onclick="$('#css').value=TEMPLATE;$('#css').oninput()">Insert template</button><button class="btn alt" onclick="$('#css').value='';$('#css').oninput()">Clear</button></div>`},
data(){return `<h2>Data</h2><div class=card><button class=btn onclick="exp()">Export backup</button><label class="btn alt">Import<input type=file hidden onchange="imp(this.files[0])"></label><button class="btn alt" onclick="expCSV()">Playtime CSV</button>${S.connected?'<button class="btn alt" onclick="expFriends()">Friends CSV</button>':''}<button class="btn alt" onclick="ask('Reset all playtime?',{ok:'Reset',danger:1}).then(y=>y&&api('clear',{what:['play','daily']}).then(load))">Reset playtime</button></div><small>Stored in ~/.config/sober-hub/data.json (permissions 600). Backups never include your Roblox cookie.</small>`},
account(){const u=S.user;return `<h2>Account</h2>${u?`<div class=card><div class=row><img class=av src="${esc(u.avatar)}"><div><b>${esc(u.display)}</b> @${esc(u.name)}<small>${S.connected?'✅ Connected':'Public profile only'} · ${u.friends} friends · joined ${u.created}${u.robux!=null?' · R$ '+u.robux.toLocaleString():''}</small></div></div><button class="btn alt" onclick="api('disconnect',{}).then(load)">${S.connected?'Disconnect & delete cookie':'Sign out'}</button></div>`:''}
 ${S.connected?'':`<div class=card><b>Log in with Quick Login</b><small>No cookie to copy. The hub shows a code; you approve it on roblox.com while you're logged in there (the same flow Roblox uses to sign in on a TV). Your password is never typed into the hub.</small>
 <div id=qlbox><button class=btn onclick="qlStart()">Get a login code</button></div></div>
 <div class=card><b>Log in with a cookie</b><small>Paste your <code>.ROBLOSECURITY</code> cookie (browser devtools → Storage/Application → Cookies → roblox.com).</small>
 <p style="color:#f68802">⚠ This cookie is a login key. Anyone who has it controls your account. It is stored only on this PC (file mode 600) and only ever sent to *.roblox.com. Never paste it anywhere else, and log out on roblox.com to revoke it.</p>
 <input id=ck type=password placeholder=".ROBLOSECURITY value" style="width:100%"><button class=btn onclick="connect()">Connect</button></div>
 <div class=card><b>Or just look up a profile (no login)</b><div class=row><input id=un placeholder=Username><button class="btn alt" onclick="signin()">Look up</button></div></div>`}
 <small>Playing still uses Sober's own login — open Sober once and sign in there.</small>`}};
const AFTER={
async home(){healMissing();refreshHome();
 if(S.connected){api('lastplayed').then(r=>{RC=(r&&r.games)||[];refreshHome()});
  api('friends').then(l=>{const fr=$('#fr');if(l.error){fr&&(fr.innerHTML=esc(l.error));return}setFriends(l);fr&&(fr.innerHTML=frs(FR));const fo=$('#fo');fo&&(fo.textContent=FR.filter(f=>f.status==2).length+' / '+FR.length)})}
 const d=await api('discover');DISC=d.error?[]:d;drawDisc()},
async friends(){if(!S.connected)return;reqs();evDraw();const l=await api('friends');if(l.error)return $('#fl').innerHTML=esc(l.error);setFriends(l);renderFriends()},
games(){pendingQ&&gsearch();pendingQ=''},
history(){rHist()},
async chat(){if(!S.connected)return;clearInterval(CVT);CVT=setInterval(()=>{if(tab!='chat'){clearInterval(CVT);return}loadConvs(true,1);CID&&openConv(CID,1)},8000);await loadConvs(true);CID&&openConv(CID)}};
const CHAT='https://apis.roblox.com/platform-chat-api/v1/';
const cvOther=c=>(c.participant_user_ids||[]).find(i=>i!=S.user.id);
const cvName=c=>{if(c.type=='one_to_one'){const o=cvOther(c),u=(c.user_data||{})[o];return u?(u.combined_name||u.display_name||u.name):(c.name||'Chat')}return c.name||'Group chat'};
async function loadConvs(reset,quiet){const r=await rb(CHAT+'get-user-conversations?include_user_data=true&pageSize=30'+(reset||!CVC?'':'&cursor='+encodeURIComponent(CVC)));const el=$('#cvl');if(!el)return;
 if(r.error){quiet||(el.innerHTML=esc(r.error));return}
 const hc=hcSet(),l=(r.conversations||[]).filter(c=>!(c.type=='one_to_one'&&hc.has(String(cvOther(c)))));if(reset)CV=l;else CV=CV.concat(l);CVC=r.next_cursor||null;
 const ids=CV.filter(c=>c.type=='one_to_one').map(cvOther).filter(Boolean),av=ids.length?await th('users/avatar-headshot','userIds',[...new Set(ids)]):{};
 el.innerHTML=CV.map((c,i)=>{const p=c.preview_message||(c.messages||[])[0]||{};return `<div class="row" style="flex-wrap:nowrap;padding:8px;border-radius:var(--radius);cursor:pointer;${c.id==CID?'background:#ffffff14':''}" onclick="openConv('${esc(c.id)}')" oncontextmenu="convMenu(event,${i})"><img src="${esc(c.type=='one_to_one'?av[cvOther(c)]||'':'')}" style="width:38px;height:38px;border-radius:50%;background:#ffffff0d;flex:none"><div style="min-width:0;flex:1"><b style="display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(cvName(c))}</b><small style="margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(hc.has(String(p.sender_user_id))?'🙈 hidden':(p.content||''))}</small></div>${c.unread_message_count?`<span class=badge>${c.unread_message_count}</span>`:''}</div>`}).join('')+(CVC?'<button class="btn alt" style="width:100%" onclick="loadConvs()">Load more chats</button>':'')||'<small>No chats yet</small>'}
async function openConv(id,quiet){CID=id;const c=CV.find(x=>x.id==id)||{},r=await rb(CHAT+'get-conversation-messages?conversation_id='+encodeURIComponent(id)),p=$('#cvp');if(!p)return;
 if(r.error){quiet||(p.innerHTML=esc(r.error));return}
 const hcs=hcSet(),m=(r.messages||[]).filter(x=>!hcs.has(String(x.sender_user_id))).slice().sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at))),last=m.length?m[m.length-1].id:'';
 if(quiet&&$('#cvm')&&last==CLAST)return;CLAST=last;const U=c.user_data||{},keep=$('#cvi')?$('#cvi').value:'';
 p.innerHTML=`<div class=row style="justify-content:space-between"><b>${esc(cvName(c))}</b>${c.type=='one_to_one'&&cvOther(c)?`<span><button class="btn alt sm" onclick="PU='${esc((U[cvOther(c)]||{}).name||'')}';go('profile')">Profile</button><button class="btn alt sm" onclick="hideChat(${cvOther(c)})">🙈 Hide chats</button></span>`:''}</div><div id=cvm style="flex:1;overflow:auto;padding:10px 0">${m.map(x=>{const me=x.sender_user_id==S.user.id,u=U[x.sender_user_id]||{};return `<div style="display:flex;justify-content:${me?'flex-end':'flex-start'};margin:4px 0"><div ${me?'':`oncontextmenu="msgMenu(event,${x.sender_user_id})"`} style="max-width:75%;padding:7px 12px;border-radius:14px;background:${me?'var(--accent)':'#ffffff14'}">${!me&&c.type!='one_to_one'?`<small style="margin:0 0 2px">${esc(u.display_name||u.name||'')}</small>`:''}${x.is_deleted?'<i>message deleted</i>':esc(x.content)}<small style="margin:3px 0 0;font-size:10px;color:${me?'#ffffffb0':'var(--muted)'}">${esc(String(x.created_at||'').slice(11,16))}</small></div></div>`}).join('')||'<small>No messages yet — say hi 👋</small>'}</div><div class=row style="flex-wrap:nowrap"><input id=cvi value="${esc(keep)}" placeholder="Message…" maxlength=500 style="flex:1" onkeydown="if(event.key=='Enter')sendMsg()"><button class=btn onclick="sendMsg()">Send</button></div>`;
 const box=$('#cvm');box&&(box.scrollTop=box.scrollHeight);
 if(c.unread_message_count){c.unread_message_count=0;rb(CHAT+'mark-conversations',{conversation_ids:[id]});loadConvs(true,1);badge()}}
async function sendMsg(){const i=$('#cvi'),t=i&&i.value.trim();if(!t||!CID)return;i.value='';const r=await rb(CHAT+'send-messages',{conversation_id:CID,messages:[{content:t}]});if(r.error){toast(r.error);i.value=t;return}openConv(CID);loadConvs(true,1)}
async function chatWith(uid){if(!S.connected)return toast('Connect your account first');if(hcSet().has(String(uid))){await unhideChat(uid);toast('Chats from this player are visible again')}toast('Opening chat…');
 let found=null,cursor=null;for(let n=0;n<10&&!found;n++){const r=await rb(CHAT+'get-user-conversations?include_user_data=true&pageSize=30'+(cursor?'&cursor='+encodeURIComponent(cursor):''));if(r.error)break;
  found=(r.conversations||[]).find(c=>c.type=='one_to_one'&&(c.participant_user_ids||[]).includes(uid));cursor=r.next_cursor;if(!cursor)break}
 if(!found){const r=await rb(CHAT+'create-conversations',{conversations:[{name:'',type:'one_to_one',participant_user_ids:[uid]}],include_user_data:true});if(r.error)return toast("Couldn't start chat: "+r.error,6000);found=(r.conversations||[])[0]}
 if(!found||!found.id)return toast("Roblox didn't return a chat — try starting it once on roblox.com");CID=found.id;CLAST='';go('chat')}
const frow=([f,i])=>`<tr oncontextmenu="friendMenu(event,${f.id})"><td style="width:48px"><img src="${esc(f.avatar||'')}" style="width:36px;height:36px;border-radius:50%"></td><td><b>${esc(f.display)}</b> <small style="display:inline">@${esc(f.name)}</small></td><td>${f.status==0?`<small style="margin:0" title="${esc(f.seen?new Date(f.seen*1000).toLocaleString():'')}">${esc(seenTxt(f))}</small>`:esc(f.where||'')}</td><td style="text-align:right;white-space:nowrap">${f.status==2&&f.place?`<button class="btn sm" onclick="joinFriend(${f.id})">Join</button>`:''}<button class="btn alt sm" title="Message" onclick="chatWith(${f.id})">💬</button><button class="btn alt sm" title="Pin / unpin" onclick="pin(${f.id})">${(S.pinned||[]).includes(f.id)?'★':'☆'}</button><button class="btn alt sm" onclick="PU='${esc(f.name)}';go('profile')">Profile</button><button class="btn alt sm" onclick="unfriend(${f.id})">Unfriend</button></td></tr>`;
function renderFriends(){const el=$('#fl');if(!el)return;const q=FQ.toLowerCase(),pn=new Set(S.pinned||[]);
 let m=FR.map((f,i)=>[f,i]).filter(([f])=>!q||(f.name+' '+f.display+' '+(f.where||'')).toLowerCase().includes(q));
 if(FSORT=='name')m.sort((a,b)=>a[0].display.localeCompare(b[0].display));
 el.innerHTML=[['⭐ Pinned',null],['In game',2],['Online',1],['In Studio',3],['Offline',0]].map(([n,s])=>{const g=m.filter(([f])=>s===null?pn.has(f.id):(!pn.has(f.id)&&f.status==s));if(s===0&&FSORT!='name')g.sort((a,b)=>(b[0].seen||0)-(a[0].seen||0));return g.length?`<h3>${n} (${g.length})</h3><div class=card><table>${g.map(frow).join('')}</table></div>`:''}).join('')||'<small>No friends match</small>';
 const c=k=>FR.filter(f=>f.status==k).length,e=$('#fsum');e&&(e.textContent=`${FR.length} friends · ${c(2)} in game · ${c(1)} online`)}
async function pin(id){const p=new Set(S.pinned||[]);p.has(id)?p.delete(id):p.add(id);await save({pinned:[...p]},1);setFriends(FR);renderFriends()}
async function addFriend(){const n=$('#addfr').value.trim();if(!n)return;const r=await rb('https://users.roblox.com/v1/usernames/users',{usernames:[n],excludeBannedUsers:true}),u=r.data&&r.data[0];if(!u)return toast(r.error||'User not found');
 guard(rb(`https://friends.roblox.com/v1/users/${u.id}/request-friend`,{}),'Request sent to '+u.name)}
async function reqs(){const el=$('#rq');if(!el)return;const r=await api('frequests');if(!$('#rq'))return;if(r.error||!r.length){el.innerHTML=r.error?`<small>${esc(r.error)}</small>`:'<small style="margin-top:10px">No pending friend requests</small>';return}
 RQ=r;if(S.opts&&S.opts.hidereq){el.innerHTML=`<small style="margin-top:10px">Friend requests hidden (${r.length}) <button class="btn alt sm" onclick="toggleReq()">Show</button></small>`;return}
 el.innerHTML=`<h3>Friend requests (${r.length}) <button class="btn sm" onclick="bulkReq('accept-friend-request')">Accept all</button><button class="btn alt sm" onclick="bulkReq('decline-friend-request')">Decline all</button><button class="btn alt sm" onclick="toggleReq()">Hide</button></h3><div class=card><table>${r.map(x=>`<tr><td style="width:50px"><img src="${esc(x.avatar||'')}" style="width:40px;height:40px;border-radius:50%"></td><td><b>${esc(x.display)}</b> <small style="display:inline">@${esc(x.name)}${x.mutual?' · '+x.mutual+' mutual':''}</small></td><td style="text-align:right"><button class=btn onclick="fr(${x.id},'accept-friend-request')">Accept</button><button class="btn alt" onclick="fr(${x.id},'decline-friend-request')">Decline</button></td></tr>`).join('')}</table></div>`;RQ=r}
let RQ=[];
async function bulkReq(a){if(!RQ.length||!await ask((a[0]=='a'?'Accept':'Decline')+' all '+RQ.length+' requests?',{ok:a[0]=='a'?'Accept all':'Decline all',danger:a[0]!='a'}))return;let ok=0;for(const x of RQ){const r=await rb(`https://friends.roblox.com/v1/users/${x.id}/${a}`,{});if(!r.error)ok++;else if(/too many|flood|limit/i.test(r.error)){toast(r.error);break}await new Promise(r=>setTimeout(r,350))}toast(ok+' done');go('friends')}
const fr=(id,a)=>guard(rb(`https://friends.roblox.com/v1/users/${id}/${a}`,{}),'Done').then(()=>load().then(()=>tab=='friends'&&AFTER.friends()));
const unfriend=id=>ask('Remove this friend?',{ok:'Remove',danger:1}).then(y=>y&&fr(id,'unfriend'));
let QLT=null;
function qlStop(){clearInterval(QLT);QLT=null}
async function qlStart(){const b=$('#qlbox');b.innerHTML='Asking Roblox for a code…';const r=await api('ql_start',{});if(r.error){b.innerHTML=`<small style="color:#e5484d">${esc(r.error)}</small><button class=btn onclick="qlStart()">Try again</button>`;return}
 b.innerHTML=`<div style="font:800 34px monospace;letter-spacing:.2em;margin:10px 0">${esc(r.code)}</div><small>1. On a device where you're logged in, open <a href="#" onclick="open_('https://www.roblox.com/crossdevicelogin/ConfirmCode');return false">roblox.com/crossdevicelogin/ConfirmCode</a><br>2. Enter this code and confirm.<br>The code expires in about 5 minutes.</small><div class=row style="margin-top:10px"><button class=btn onclick="open_('https://www.roblox.com/crossdevicelogin/ConfirmCode')">Open that page</button><button class="btn alt" onclick="qlCancel()">Cancel</button><span id=qls style="color:var(--muted)">Waiting for you to confirm…</span></div>`;
 qlStop();QLT=setInterval(async()=>{if(!$('#qlbox'))return qlStop();const p=await api('ql_poll',{});if(!$('#qls')&&p.state!='done')return;
  if(p.error){qlStop();$('#qlbox').innerHTML=`<small style="color:#e5484d">${esc(p.error)}. You can use the cookie method below instead.</small><button class=btn onclick="qlStart()">Try again</button>`;return}
  if(p.state=='linked')$('#qls').textContent='Code entered - approve it on your other device…';
  else if(p.state=='done'){qlStop();toast('Logged in!');go('home')}
  else if(p.state=='expired'||p.state=='cancelled'||p.state=='none'){qlStop();$('#qlbox').innerHTML=`<small>${p.state=='expired'?'That code expired.':'Login cancelled.'}</small><button class=btn onclick="qlStart()">Get a new code</button>`}},2000)}
async function qlCancel(){qlStop();await api('ql_cancel',{});if($('#qlbox'))$('#qlbox').innerHTML='<button class=btn onclick="qlStart()">Get a login code</button>'}
async function connect(){const r=await api('connect',{cookie:$('#ck').value});r.error?toast(r.error):(toast('Connected!'),go('home'))}
// ---- auto update popups
async function updRestart(){await api('upd_restart',{});toast('Restarting Sober Hub…',20000);setTimeout(()=>{const t=setInterval(async()=>{try{const r=await fetch('/api/upd_status');if(r.ok){clearInterval(t);location.reload()}}catch(e){}},700)},1800)}
const nt=a=>a&&a.length?'\n\nWhat\'s new:\n'+a.map(x=>'• '+x).join('\n'):'';
async function updShow(u,manual){
 if(u.state=='installed'){await api('upd_ack',{});const y=await ask('Version '+u.version+' is installed. Restart Sober Hub to start using it.'+nt(u.notes),{title:'Sober Hub updated automatically!',ok:'Restart now',danger:0});if(y)updRestart()}
 else if(u.state=='failed'){await api('upd_ack',{});await ask('Version '+u.version+' is available on GitHub, but it could not be installed: '+u.error+'.\n\nYou are still on version '+u.cur+' and your current file was not changed.',{title:'Update found but failed to install',ok:'OK',info:1})}
 else if(manual){u.state=='current'?toast('You are on the latest version (v'+u.cur+')',4000):toast(u.error||'Could not check for updates',6000)}}
async function updNow(b){b.disabled=true;toast('Checking GitHub…',15000);const u=await api('upd_check',{});b.disabled=false;$('#toast').style.display='none';updShow(u,true)}
async function updPoll(){const u=await api('upd_status',{});if(u.error&&!u.state)return;
 if(u.notice){await api('upd_ack',{});await ask('Version '+u.notice.version+' is now installed (you were on '+u.notice.old+').'+nt(u.notice.notes),{title:'Sober Hub updated automatically!',ok:'Nice',info:1})}
 else if((u.state=='installed'||u.state=='failed')&&!u.shown)updShow(u)}
async function signin(){const r=await api('user',{name:$('#un').value});r.error?toast(r.error):load()}
function rnd(){const g=S.favs[Math.random()*S.favs.length|0];play(g.place,g.name,g.icon)}
async function surprise(){const d=DISC.length?DISC:await api('discover'),all=(d.error?[]:d).flatMap(s=>s.games).filter(g=>!hidSet().has(String(g.place)));if(!all.length)return toast('Nothing to pick from');openGame(all[Math.random()*all.length|0].place)}
function quick(run){const l=parseLink($('#ql').value);if(!l)return toast('Paste a roblox.com/games link or a place ID');run?play(l.place,'','',l.job,l.code):openGame(l.place)}
async function gsearch(){const r=await api('search',{q:$('#gs').value});$('#res').innerHTML=r.error?esc(r.error):r.map(g=>tile(g,'👤 '+g.playing)).join('')||'<small>No results</small>'}
async function fav(g,on){const f=S.favs.filter(x=>x.place!=g.place);if(on)f.push({place:g.place,name:g.name,icon:g.icon});await save({favs:f},1);S.favs=f}
async function favFirst(i){const f=[...S.favs],[x]=f.splice(i,1);f.unshift(x);await save({favs:f},1);go('library')}
async function favDel(p){await fav({place:p},false);go('library')}
function rHist(){const el=$('#hl');if(!el)return;const q=HQ.toLowerCase(),l=S.history.filter(h=>!q||String(h.name||h.place).toLowerCase().includes(q));
 el.innerHTML=`<table>${l.map(h=>`<tr><td><b>${esc(h.name||h.place)}</b></td><td>${ago(h.ts)}</td><td>${h.job?'🖥 '+esc(String(h.job).slice(0,8))+'…':h.code?'🔒 private':'public'}</td><td style="text-align:right;white-space:nowrap"><button class="btn sm" onclick="pl(${h.place})">Play</button>${h.job||h.code?`<button class="btn alt sm" onclick="pl(${h.place},'${esc(h.job||'')}','${esc(h.code||'')}')">Same server</button>`:''}<button class="btn alt sm" onclick="histDel(${h.ts})">✕</button></td></tr>`).join('')||'<tr><td>Nothing yet</td></tr>'}</table>`}
async function histDel(ts){await api('hist_del',{ts});S=await api('state');rHist()}
async function newColl(){const n=(await askText('Collection name','',{title:'New collection'})||'').trim();if(!n)return;const C=JSON.parse(JSON.stringify(S.collections||{}));C[n]=C[n]||[];await save({collections:C},1);go('library')}
async function delColl(ci){const C=JSON.parse(JSON.stringify(S.collections||{})),n=Object.keys(C)[ci];if(!await ask('Delete collection "'+n+'"?',{ok:'Delete',danger:1}))return;delete C[n];await save({collections:C},1);go('library')}
async function rmColl(ci,gi){const C=JSON.parse(JSON.stringify(S.collections||{})),n=Object.keys(C)[ci];C[n].splice(gi,1);await save({collections:C},1);go('library')}
async function addToColl(v){if(v==='')return;const C=JSON.parse(JSON.stringify(S.collections||{}));let n;if(v=='new'){n=(await askText('Collection name','',{title:'New collection'})||'').trim();if(!n)return}else n=Object.keys(C)[+v];
 C[n]=C[n]||[];if(!C[n].some(x=>x.place==cur.place))C[n].push({place:cur.place,name:cur.name,icon:cur.icon});await save({collections:C});openGame(cur.place)}
async function openGame(p,focus){if(tab!='game')PT=tab;tab='game';document.querySelectorAll('nav a.on').forEach(a=>a.classList.remove('on'));$('#main').innerHTML='Loading…';const g=await api('game',{place:p});if(g.error){$('#main').innerHTML=esc(g.error);return}cur=g;
 const pl2=S.play[p]||{},h=lastSrv(p),isF=S.favs.some(f=>f.place==p),C=S.collections||{},fp=FR.filter(f=>f.status==2&&String(f.place)==String(p));
 $('#main').innerHTML=`<a class="btn alt" onclick="go(PT||'games')">← Back</a><div class=card><div class=row style="align-items:flex-start;gap:20px"><img src="${esc(g.icon)}" style="width:200px;border-radius:var(--radius)"><div style="flex:1;min-width:260px"><h2 style="margin:0">${esc(g.name)}</h2><small>By ${esc(g.creator)} · updated ${g.updated} · max ${g.maxp} players</small>
 <p><span class=stat><b>${g.like!=null?'👍 '+g.like+'%':'—'}</b>rating</span><span class=stat><b>${g.playing.toLocaleString()}</b>playing</span><span class=stat><b>${g.visits.toLocaleString()}</b>visits</span><span class=stat><b>${g.favs.toLocaleString()}</b>favorites</span></p>
 <button class="btn play" onclick="pl(${p})">▶ Play</button>${h?`<button class=btn onclick="pl(${p},'${esc(h.job||'')}','${esc(h.code||'')}')">↩ Last server</button>`:''}<button class="btn alt" onclick="fav(cur,${!isF}).then(()=>openGame(${p}))">${isF?'★ Favorited':'☆ Favorite'}</button><button class="btn alt" onclick="laterToggle(cur).then(()=>openGame(${p}))">${(S.later||[]).some(x=>x.place==p)?'🕒 In Play later':'🕒 Play later'}</button>${S.connected?'<button class="btn alt" onclick="vote(true)">👍</button><button class="btn alt" onclick="vote(false)">👎</button><button class="btn alt" onclick="rfav()">❤ Favorite on Roblox</button>':''}
 <div class=row style="margin-top:6px"><select onchange="addToColl(this.value)"><option value="">＋ Add to collection…</option>${Object.keys(C).map((n,i)=>`<option value=${i}>${esc(n)}</option>`).join('')}<option value=new>New collection…</option></select><button class="btn alt sm" onclick="copy('https://www.roblox.com/games/${p}')">Copy link</button><button class="btn alt sm" onclick="copy('roblox://experiences/start?placeId=${p}')">Copy launch link</button><button class="btn alt sm" onclick="open_('https://www.roblox.com/games/${p}')">Open on roblox.com</button></div></div></div>
 <p>${esc(g.desc)}</p><span class=stat><b>${fmt(pl2.secs||0)}</b>your time</span><span class=stat><b>${pl2.launches||0}</b>launches</span><span class=stat><b>${ago(pl2.last)}</b>last played</span></div>
 ${fp.length?`<div class=card><b>Friends playing this (${fp.length})</b><div class=rs style="margin-top:8px">${fp.map(f=>`<div class=fr onclick="joinFriend(${f.id})"><div class=ava><img src="${esc(f.avatar||'')}"><span class="dot s2"></span></div><b>${esc(f.display)}</b><small>Click to join</small></div>`).join('')}</div></div>`:''}
 <div class=card><b>Notes</b><textarea rows=3 onchange="save({notes:{${p}:this.value}})">${esc(S.notes[p]||'')}</textarea></div>${privCard(p)}<div class=card><b>Public servers</b><div class=row><button class="btn alt" onclick="srv(${p},'Asc')">Emptiest</button><button class="btn alt" onclick="srv(${p},'Desc')">Fullest</button><button class="btn alt" onclick="renderSV(${p},'ping')">Best ping (loaded)</button><button class="btn alt" onclick="randSrv(${p})">🎲 Random server</button><button class="btn alt" onclick="hop(${p})">🔀 Server hop</button></div><div id=sv></div></div>`;srv(p,'Asc');pvSaved(p);if(focus=='priv')setTimeout(()=>{const e=$('#privcard');e&&e.scrollIntoView({behavior:'smooth'})},400)}
async function srv(p,sort,more){const el=$('#sv');if(!more){SV=[];SVC=null;el.innerHTML='Loading…'}const r=await api('servers',{place:p,sort,cursor:more?SVC:''});if(r.error){el.innerHTML=esc(r.error);return}SV=SV.concat(r.list);SVC=r.next;SVS=sort;renderSV(p)}
function renderSV(p,by){const el=$('#sv');if(!el)return;const l=[...SV];if(by=='ping')l.sort((a,b)=>(a.ping??9999)-(b.ping??9999));
 el.innerHTML='<table>'+l.map(s=>`<tr><td>${s.playing}/${s.max}</td><td>${s.ping??'?'} ms</td><td>${s.fps} fps</td><td>${s.id.slice(0,8)}…</td><td style="text-align:right"><button class="btn sm" onclick="pl(${p},'${s.id}')">Join</button></td></tr>`).join('')+'</table>'+(SVC?`<button class="btn alt" onclick="srv(${p},SVS,1)">Load more servers</button>`:'')||'<small>No servers</small>'}
function preset(k){const [bg,panel,accent,text,green]=PRESETS[k],t={bg,panel,accent,text};if(green)t.green=green;save({theme:t}).then(load)}
const capiGet=()=>({capi:{url:$('#cau').value.trim(),method:$('#cam').value,headers:$('#cah').value,body:$('#cab').value,path:$('#cap').value.trim()}});
async function capiSave(){await save(capiGet());DISC=[]}
async function capiTest(){await save(capiGet(),1);DISC=[];const el=$('#capir');el.textContent='Testing...';const r=await api('capi_test');
 el.textContent=r.error?'Failed: '+r.error:`Works: ${r.games} games in ${r.sections} section(s), first is "${r.first}".`}
function exp(){dl('sober-hub-backup.json',JSON.stringify({...S,capi:{...S.capi,headers:''}}),'application/json')}
function expCSV(){const q=v=>'"'+String(v??'').replace(/"/g,'""')+'"';dl('sober-hub-playtime.csv',['game,place,seconds,launches,longest_seconds,last_played'].concat(Object.entries(S.play).map(([p,v])=>[q(v.name||p),p,Math.round(v.secs),v.launches,Math.round(v.longest||0),v.last?new Date(v.last*1e3).toISOString():''].join(','))).join('\n'),'text/csv')}
async function expFriends(){const l=FR.length?FR:await api('friends'),q=v=>'"'+String(v??'').replace(/"/g,'""')+'"';dl('sober-hub-friends.csv',['id,username,display_name,last_online,last_game'].concat(l.map(f=>[f.id,q(f.name),q(f.display),q(f.status>0?'online now':f.seen?new Date(f.seen*1000).toISOString():''),q(f.sgame)].join(','))).join('\n'),'text/csv')}
async function imp(f){await api('import',{data:JSON.parse(await f.text())});load()}
// rb(url, body?, method?) -> any roblox.com API through the backend, which adds the cookie and CSRF token.
const rb=(url,body,method)=>api('rbx',{url,body,method:method||(body?'POST':'GET')});
const guard=async(p,ok)=>{const r=await p;r.error?toast(r.error):ok&&toast(ok);return r};
// th(kind, idParam, ids) -> {id: thumbnailUrl}, batched 100 per request.
const th=async(kind,key,ids)=>{const o={};for(let i=0;i<ids.length;i+=100){const r=await rb(`https://thumbnails.roblox.com/v1/${kind}?${key}=${ids.slice(i,i+100).join(',')}&size=150x150&format=Png`);(r.data||[]).forEach(t=>o[t.targetId]=t.imageUrl)}return o};
const needConn=()=>S.connected?'':`<div class=card>Connect your Roblox account first. <button class=btn onclick="go('account')">Connect</button></div>`;
const open_=u=>api('open',{url:u}).then(r=>r.error&&toast(r.error));
async function vote(v){guard(rb(`https://games.roblox.com/v1/games/${cur.uid}/user-votes`,{vote:v}),'Vote sent')}
async function rfav(){guard(rb(`https://games.roblox.com/v1/games/${cur.uid}/favorites`,{isFavorited:true}),'Favorited on Roblox')}
async function randSrv(p){const r=await api('servers',{place:p});if(r.error||!r.list.length)return toast(r.error||'No servers');pl(p,r.list[Math.random()*r.list.length|0].id)}
async function soberUpd(b){b.disabled=true;toast('Updating Sober…',8000);const r=await api('sober_update',{});b.disabled=false;toast(r.error||(r.ok?'Sober is up to date':'Update failed: '+r.out),6000)}
const ffDel=i=>api('sober_set',{fflag_del:[Object.keys(SOB)[i]]}).then(()=>go('sober'));
function ffAdd(){const k=$('#ffk').value.trim();if(!k)return toast('Enter a flag name');api('sober_set',{fflags:{[k]:pv($('#ffv').value.trim())}}).then(()=>go('sober'))}
async function setNotify(v){await save({opts:{notify:v}});PREV=null;if(v!='off'){watch();if('Notification' in window&&Notification.permission=='default')Notification.requestPermission()}}
// ---- avatar
const SKIN=[[18,'#cc8e69'],[125,'#eab892'],[217,'#7c5c46'],[1,'#f2f3f3'],[226,'#fdea8d'],[194,'#a3a2a5'],[23,'#0d69ac'],[21,'#c4281c'],[26,'#1b2a35'],[24,'#f5cd30'],[105,'#e29b40'],[104,'#6b327c'],[119,'#a4bd47'],[1001,'#f8f8f8'],[1003,'#111111']];
const WT=['Hat','HairAccessory','FaceAccessory','NeckAccessory','ShoulderAccessory','FrontAccessory','BackAccessory','WaistAccessory','TShirtAccessory','ShirtAccessory','PantsAccessory','JacketAccessory','SweaterAccessory','ShortsAccessory','LeftShoeAccessory','RightShoeAccessory','DressSkirtAccessory','EyebrowAccessory','EyelashAccessory','Shirt','Pants','TShirt','Face','Head','DynamicHead','Torso','LeftArm','RightArm','LeftLeg','RightLeg'];
const EXCL=new Set(['IdleAnimation','WalkAnimation','RunAnimation','JumpAnimation','FallAnimation','ClimbAnimation','SwimAnimation','Shirt','Pants','TShirt','Face','Head','DynamicHead','Torso','LeftArm','RightArm','LeftLeg','RightLeg']);
let AV={},CAT=[],WC='',WL=[],WN=new Set(),OL=[],AVV='avatar',AVCAT='Accessories',AVT='Hat',UNDO=null;
const AVC={Clothing:['Shirt','Pants','TShirt','ShirtAccessory','PantsAccessory','JacketAccessory','SweaterAccessory','ShortsAccessory','TShirtAccessory','DressSkirtAccessory','LeftShoeAccessory','RightShoeAccessory'],Accessories:['Hat','HairAccessory','FaceAccessory','NeckAccessory','ShoulderAccessory','FrontAccessory','BackAccessory','WaistAccessory','EyebrowAccessory','EyelashAccessory'],Body:['Face','Head','DynamicHead','Torso','LeftArm','RightArm','LeftLeg','RightLeg'],Animations:['IdleAnimation','WalkAnimation','RunAnimation','JumpAnimation','FallAnimation','ClimbAnimation','SwimAnimation'],Outfits:[],Bundles:[]};
const tname=t=>t.replace(/Accessory$/,'').replace(/([a-z])([A-Z])/g,'$1 $2');
const avBusy=on=>{const w=$('#bwrap');if(w){w.classList.toggle('busy',!!on);clearTimeout(avBusy.t);if(on)avBusy.t=setTimeout(()=>avBusy(0),14000)}};
const isAcc=t=>t=='Hat'||/Accessory$/.test(t||'');
const atype=a=>(a.assetType&&a.assetType.name)||'';
const avErr=e=>esc(e)+(/author|denied|token|login/i.test(e)?' - reconnect your account in Hub Login':'');
const AVU='https://avatar.roblox.com/v1/';
async function bust(n=0){const im=$('#bust');if(!im||!S.user)return;
 const r=await rb(`https://thumbnails.roblox.com/v1/users/${AVV}?userIds=${S.user.id}&size=420x420&format=Png`),d=r.data&&r.data[0];
 if(d&&d.imageUrl&&(d.state=='Completed'||n>=6)){im.onload=im.onerror=()=>avBusy(0);im.src=d.imageUrl}else if(n<8)setTimeout(()=>bust(n+1),1500);else avBusy(0)}
const avView=v=>{AVV=v;document.querySelectorAll('[data-v]').forEach(e=>e.classList.toggle('sel',e.dataset.v==v));avBusy(1);bust()};
function avResult(r,ok){
 if(r.error){avBusy(0);toast(r.error,5000);return false}
 if(r.success===false||(r.invalidAssetIds||[]).length){avBusy(0);toast('Roblox rejected that: '+((r.invalidAssetIds||[]).join(', ')||'invalid combination'),5000);avLoad(1);return false}
 toast(ok,2500);rb(AVU+'redraw-thumbnail',{});avLoad(1);setTimeout(bust,2500);return true}
// Replace the worn assets. Tries the v2 endpoint (supports layered-clothing meta), then falls back to v1 asset ids.
async function avSave(list,ok,isUndo){
 avBusy(1);if(!isUndo)UNDO=(AV.assets||[]).map(a=>a.meta?{id:a.id,meta:a.meta}:{id:a.id});
 let r=await rb('https://avatar.roblox.com/v2/avatar/set-wearing-assets',{assets:list.map(a=>a.meta?{id:a.id,meta:a.meta}:{id:a.id})});
 if(r.error||r.success===false){const r1=await rb(AVU+'avatar/set-wearing-assets',{assetIds:list.map(a=>a.id)});if(!(r1.error||r1.success===false)||!r.error)r=r1}
 return avResult(r,ok)}
const avUndo=()=>UNDO?avSave(UNDO,'Reverted',true):toast('Nothing to undo yet');
const avMark=()=>document.querySelectorAll('#ward .tile[data-id]').forEach(e=>{const on=WN.has(+e.dataset.id);e.classList.toggle('on',on);const s=e.querySelector('small');if(s)s.textContent=on?'✓ Worn':'Wear'});
function avChips(){const t=$('#avtabs');if(!t)return;t.innerHTML=Object.keys(AVC).map(k=>`<span class="chip${k==AVCAT?' on':''}" onclick="avCat('${k}')">${k}</span>`).join('');
 const s=$('#avsub');if(s)s.innerHTML=(AVC[AVCAT]||[]).map(k=>`<span class="chip${k==AVT?' on':''}" onclick="avSub('${k}')">${tname(k)}</span>`).join('')}
function avCat(c){AVCAT=c;AVT=AVC[c][0]||c;avChips();avTab()}
function avSub(t){AVT=t;avChips();avTab()}
function avTab(){const w=$('#ward'),m=$('#wmore');if(m)m.style.display='none';if($('#wq'))$('#wq').value='';
 if(AVT=='Outfits'){w.innerHTML='<small>Loading…</small>';outfits()}else{w.innerHTML='<small>Loading…</small>';wardrobe(true)}}
async function avLoad(keep){if(!keep)avBusy(1);bust();
 AV=await rb(AVU+'avatar');
 if(AV.error){$('#worn')&&($('#worn').innerHTML=avErr(AV.error));return}
 const A=AV.assets||[];WN=new Set(A.map(a=>a.id));const t=await th('assets','assetIds',A.map(a=>a.id));
 $('#worn')&&($('#worn').innerHTML=A.map(a=>`<div class=tile onclick="unwear(${a.id})" title="Click to take off"><img src="${esc(t[a.id]||'')}"><b>${esc(a.name)}</b><small>${esc(atype(a))} - click to remove</small></div>`).join('')||'<small>Nothing worn</small>');
 $('#avmeta')&&($('#avmeta').innerHTML=`<span class=pill>${esc(AV.playerAvatarType||'')}</span><span class=pill>${A.length} items worn</span>`);
 if(keep){avMark();return}
 drawAvOpts();avChips();avTab()}
async function drawAvOpts(){const rules=await rb(AVU+'avatar-rules'),sc=rules.scales||{},cur=AV.scales||{},el=$('#avopt');if(!el)return;
 const defs={height:{min:.9,max:1.05,increment:.01},width:{min:.7,max:1,increment:.01},head:{min:.95,max:1,increment:.01},depth:{min:.7,max:1,increment:.01},proportion:{min:0,max:1,increment:.01},bodyType:{min:0,max:1,increment:.01}};
 el.innerHTML=`<div class=row>Avatar type <button class="btn${AV.playerAvatarType=='R6'?'':' alt'} sm" onclick="avType('R6')">R6</button><button class="btn${AV.playerAvatarType=='R15'?'':' alt'} sm" onclick="avType('R15')">R15</button></div><div style="margin-top:8px">${Object.keys(defs).map(k=>{const d={...defs[k],...(sc[k]||{})},v=cur[k]??d.max;return `<label style="display:flex;gap:8px;align-items:center;margin:4px 0"><span style="width:84px">${k}</span><input type=range data-s=${k} min=${d.min} max=${d.max} step=${d.increment} value="${v}" oninput="this.nextElementSibling.textContent=this.value" style="flex:1;padding:0"><span style="width:38px">${v}</span></label>`}).join('')}<button class=btn onclick="applyScales()">Apply scales</button></div>`}
async function applyScales(){
 if(AV.playerAvatarType=='R6')return toast('Body scales only work on R15 - switch to R15 first',4500);
 const rules=(await rb(AVU+'avatar-rules')).scales||{},b={};
 document.querySelectorAll('#avopt [data-s]').forEach(e=>{const k=e.dataset.s,r=rules[k]||{},st=+r.increment||.01;let v=+e.value;
  if(r.min!=null)v=Math.max(+r.min,v);if(r.max!=null)v=Math.min(+r.max,v);b[k]=+(Math.round(v/st)*st).toFixed(2)});
 avResult(await rb(AVU+'avatar/set-scales',b),'Body scale set')}
const EXT=new Set([8,41,42,43,44,45,46,47,64,65,66,67,68,69,70,71,72,76,77]);   // accessory asset-type ids (these stack; everything else replaces its own type)
async function wearBundle(id,nm){
 if(!S.connected)return toast('Connect your account first');
 toast('Equipping bundle...',6000);
 const b=await rb(`https://catalog.roblox.com/v1/bundles/${id}/details`);if(b.error)return toast(b.error,5000);
 const items=b.items||[];
 if(items.some(i=>i.owned===false))return toast('You do not own this bundle yet - buy it first',5000);
 AV=await rb(AVU+'avatar');if(AV.error)return toast(AV.error,5000);
 let add=[],outfit=null;
 for(const it of items){
  if(it.type=='UserOutfit'){const o=await rb(AVU+`outfits/${it.id}/details`);if(o.error)return toast(o.error,5000);outfit=o;
   (o.assets||[]).forEach(a=>add.push({id:a.id,tid:a.assetType&&a.assetType.id,meta:a.meta}))}
  else if(it.type=='Asset')add.push({id:it.id})}
 add=add.filter((a,i)=>add.findIndex(x=>x.id==a.id)==i);
 await Promise.all(add.filter(a=>a.tid==null).map(async a=>{const d=await rb(`https://economy.roblox.com/v2/assets/${a.id}/details`);a.tid=d.AssetTypeId}));
 if(!add.length)return toast('This bundle has nothing wearable in it',4000);
 if(outfit&&outfit.playerAvatarType&&outfit.playerAvatarType!=AV.playerAvatarType){const r0=await rb(AVU+'avatar/set-player-avatar-type',{playerAvatarType:outfit.playerAvatarType});if(r0.error)return toast(r0.error,5000)}
 const nt=new Set(add.map(a=>a.tid)),head=add.some(a=>a.tid==17||a.tid==79);
 const keep=(AV.assets||[]).filter(a=>{const t=a.assetType&&a.assetType.id;if(add.some(x=>x.id==a.id))return false;if(head&&(t==17||t==79))return false;return EXT.has(t)||!nt.has(t)});
 return avSave([...keep,...add.map(a=>a.meta?{id:a.id,meta:a.meta}:{id:a.id})],(nm||b.name||'Bundle')+' equipped')}
async function wardBundles(reset){
 let r=await rb(`https://catalog.roblox.com/v1/users/${S.user.id}/bundles?limit=100&sortOrder=Desc${WC?'&cursor='+encodeURIComponent(WC):''}`);
 if(r.error&&!WC)r=await rb(`https://catalog.roblox.com/v1/users/${S.user.id}/bundles/1?limit=100&sortOrder=Desc`);
 if(r.error){$('#ward').innerHTML=avErr(r.error);return}
 const l=r.data||[];WC=r.nextPageCursor||'';const im=await th('bundles/thumbnails','bundleIds',l.map(x=>x.id));
 const html=l.map(x=>`<div class=tile onclick="wearBundle(${x.id})"><img src="${esc(im[x.id]||'')}"><b>${esc(x.name)}</b><small>Bundle - click to wear</small></div>`).join('');
 if(reset)$('#ward').innerHTML=html||'<small>No bundles owned</small>';else $('#ward').insertAdjacentHTML('beforeend',html);
 const m=$('#wmore');m&&(m.style.display=WC?'inline-block':'none');wfilter()}
async function avType(t){avResult(await rb(AVU+'avatar/set-player-avatar-type',{playerAvatarType:t}),'Switched to '+t)}
async function wardrobe(reset=true){if(reset){WC='';WL=[]}const t=AVT;if(!t)return;if(t=='Bundles')return wardBundles(reset);if(t=='Outfits')return;
 const r=await rb(`https://inventory.roblox.com/v2/users/${S.user.id}/inventory?assetTypes=${t}&limit=50&sortOrder=Desc${WC?'&cursor='+encodeURIComponent(WC):''}`);
 if(r.error){$('#ward').innerHTML=avErr(r.error);return}
 const l=r.data||[],base=WL.length;WC=r.nextPageCursor||'';WL=WL.concat(l.map(x=>({id:x.assetId,name:x.assetName,type:t})));
 const im=await th('assets','assetIds',l.map(x=>x.assetId));
 const html=l.map((x,i)=>{const on=WN.has(x.assetId);return `<div class="tile${on?' on':''}" data-id="${x.assetId}" onclick="wearI(${base+i})"><img src="${esc(im[x.assetId]||'')}"><b>${esc(x.assetName)}</b><small>${on?'✓ Worn':'Wear'}</small></div>`}).join('');
 if(reset)$('#ward').innerHTML=html||'<small>You do not own anything in this category</small>';else $('#ward').insertAdjacentHTML('beforeend',html);
 const m=$('#wmore');m&&(m.style.display=WC?'inline-block':'none');wfilter()}
const wfilter=()=>{const q=($('#wq')&&$('#wq').value||'').toLowerCase();document.querySelectorAll('#ward .tile').forEach(e=>e.style.display=e.textContent.toLowerCase().includes(q)?'':'none')};
const wearI=i=>{const x=WL[i];x&&wear(x.id,x.type,x.name)};
async function wear(id,type,nm){if(WN.has(id))return unwear(id);
 let keep=(AV.assets||[]).filter(a=>a.id!=id);
 if(type=='Head'||type=='DynamicHead')keep=keep.filter(a=>atype(a)!='Head'&&atype(a)!='DynamicHead');
 else if(EXCL.has(type))keep=keep.filter(a=>atype(a)!=type);
 if(isAcc(type)&&keep.filter(a=>isAcc(atype(a))).length>=10)return toast('Max 10 accessories - take one off first',4000);
 return avSave([...keep,{id}],(nm||'Item')+' worn')}
const unwear=id=>avSave((AV.assets||[]).filter(a=>a.id!=id),'Taken off');
const unwearAcc=()=>avSave((AV.assets||[]).filter(a=>!isAcc(atype(a))),'Accessories removed');
async function outfits(){const o=await rb(AVU+`users/${S.user.id}/outfits?itemsPerPage=50&isEditable=true`);if(AVT!='Outfits'||!$('#ward'))return;
 if(o.error){$('#ward').innerHTML=avErr(o.error);return}
 OL=o.data||[];const ot=await th('users/outfits','userOutfitIds',OL.map(x=>x.id));
 $('#ward').innerHTML=OL.map((x,i)=>`<div class=tile><img src="${esc(ot[x.id]||'')}" onclick="wearOutfit(${x.id})"><b>${esc(x.name)}</b><div class=row style="gap:4px;justify-content:center"><button class="btn sm" onclick="wearOutfit(${x.id})">Wear</button><button class="btn alt sm" title=Rename onclick="renOutfit(${i})">&#9998;</button><button class="btn alt sm" title=Delete onclick="delOutfit(${i})">&#128465;</button></div></div>`).join('')||'<small>No outfits yet</small>'}
// Wear a saved outfit. Roblox's direct "wear" call can succeed without changing anything (or reject the whole outfit over one
// unavailable item), so the result is verified against the live avatar and, if needed, the outfit is rebuilt item by item.
async function wearOutfit(id){
 try{
  toast('Wearing outfit...',8000);
  const o=await rb(AVU+`outfits/${id}/details`);if(o.error)return toast('Could not read the outfit: '+o.error,5000);
  const wornIds=async()=>{const a=await rb(AVU+'avatar');return a.error?null:new Set((a.assets||[]).map(x=>x.id))};
  const has=async ids=>{const w=await wornIds();return !!w&&ids.every(i=>w.has(i))};
  const r=await rb(AVU+`outfits/${id}/wear`,{});
  if(!r.error&&r.success!==false&&!(r.invalidAssetIds||[]).length&&await has((o.assets||[]).map(a=>a.id)))return avResult({},'Outfit worn');
  const why=r.error||((r.invalidAssetIds||[]).length?'some items are unavailable':'it had no effect');
  AV=await rb(AVU+'avatar');if(AV.error)return toast(AV.error,5000);
  if(o.playerAvatarType&&o.playerAvatarType!=AV.playerAvatarType){const t=await rb(AVU+'avatar/set-player-avatar-type',{playerAvatarType:o.playerAvatarType});if(t.error)return toast(t.error,5000)}
  let list=(o.assets||[]).slice(),skipped=0,s={};
  const full=l=>l.map(a=>({id:a.id,name:a.name,assetType:a.assetType,currentVersionId:a.currentVersionId,meta:a.meta}));
  const slim=l=>l.map(a=>a.meta?{id:a.id,meta:a.meta}:{id:a.id});
  for(let n=0;n<4;n++){   // each round drops the items Roblox says it cannot wear
   for(const body of [full(list),slim(list)]){
    s=await rb('https://avatar.roblox.com/v2/avatar/set-wearing-assets',{assets:body});
    if(!s.error&&s.success!==false)break;if((s.invalidAssetIds||[]).length)break}
   if((s.error||s.success===false)&&!(s.invalidAssetIds||[]).length){const s1=await rb(AVU+'avatar/set-wearing-assets',{assetIds:list.map(a=>a.id)});if(!(s1.error||s1.success===false))s=s1}
   const bad=s.invalidAssetIds||[];
   if(bad.length&&list.length){skipped+=bad.length;list=list.filter(a=>!bad.includes(a.id));continue}
   break}
  if(s.error||s.success===false)return toast('Roblox rejected this outfit: '+(s.error||'invalid combination')+' (direct wear: '+why+')',7000);
  if(o.bodyColors)await rb(AVU+'avatar/set-body-colors',o.bodyColors);
  if(o.scale&&(o.playerAvatarType||AV.playerAvatarType)!='R6')await rb(AVU+'avatar/set-scales',o.scale);
  if(!await has(list.map(a=>a.id)))return toast('Roblox accepted the request but the avatar did not change ('+why+')',7000);
  avResult({},'Outfit worn'+(skipped?` (${skipped} unavailable item${skipped>1?'s':''} skipped)`:''))
 }catch(e){toast('Could not wear the outfit: '+e.message,6000)}}
async function saveOutfit(){const n=await askText('Name for the new outfit','',{title:'Save current avatar as outfit'});if(!n)return;
 const r=await rb(AVU+'outfits/create',{name:n,bodyColors:AV.bodyColors,assets:(AV.assets||[]).map(a=>a.meta?{id:a.id,meta:a.meta}:{id:a.id}),scale:AV.scales,playerAvatarType:AV.playerAvatarType});
 if(r.error)return toast(r.error,5000);toast('Outfit saved');if(AVT=='Outfits')outfits()}
async function renOutfit(i){const o=OL[i];if(!o)return;const n=await askText('New name','' +o.name,{title:'Rename outfit',ok:'Rename'});if(!n)return;
 const r=await rb(AVU+`outfits/${o.id}`,{name:n},'PATCH');if(r.error)return toast(r.error,5000);toast('Renamed');avTab()}
async function delOutfit(i){const o=OL[i];if(!o)return;if(!await ask(`Delete outfit "${o.name}"?`,{ok:'Delete',danger:1,title:'Delete outfit'}))return;
 const r=await rb(AVU+`outfits/${o.id}/delete`,{});if(r.error)return toast(r.error,5000);toast('Outfit deleted');avTab()}
async function skin(c){const b={};['head','torso','rightArm','leftArm','rightLeg','leftLeg'].forEach(x=>b[x+'ColorId']=c);avBusy(1);avResult(await rb(AVU+'avatar/set-body-colors',b),'Skin tone set')}
const redraw=async()=>{avBusy(1);await rb(AVU+'redraw-thumbnail',{});toast('Re-rendering...');setTimeout(bust,2500)};
// ---- tweaks & mods
// Roblox now only honours a short allow-list of Fast Flags, so only a few current presets are offered;
// the older flags are kept under "Legacy" and may be silently ignored.
const MSAA=[['Automatic',''],['1x',1],['2x',2],['4x',4]];
const TEXQ=[['Automatic',''],['Lowest (0)',0],['Low (1)',1],['Medium (2)',2],['High (3)',3]];
const FFP=[
 ['Disable shadows','Big FPS gain, flatter look',{FIntRenderShadowIntensity:0}],
 ['Disable post-processing','Turns off bloom, blur and sun rays',{FFlagDisablePostFx:true}],
 ['Remove grass','Less terrain clutter',{FIntFRMMinGrassDistance:0,FIntFRMMaxGrassDistance:0,FIntRenderGrassDetailStrands:0}]];
const LIGHT=[['Default',null],['Voxel (fast, old style)','DFFlagDebugRenderForceTechnologyVoxel'],['Shadow map','FFlagDebugForceFutureIsBrightPhase2'],['Future (best looking)','FFlagDebugForceFutureIsBrightPhase3']];
const MODSLOTS=[['Custom death sound (.ogg)','content/sounds/ouch.ogg|content/sounds/uuhhh.mp3'],['Arrow cursor (.png)','content/textures/Cursors/KeyboardMouse/ArrowCursor.png'],['Far cursor (.png)','content/textures/Cursors/KeyboardMouse/ArrowFarCursor.png']];
const CURS=['cursor_2013','cursor_2006'],EMOJ=['emoji_catmoji','emoji_win11','emoji_win10','emoji_win8'];
let MODF=[],MODP={},MODFONT=false,SOBK=[],SOBCFG={};
// Sober rewrites config.json when it closes, so a running Sober can undo the change: offer to close it first.
async function ffApply(del,set){const s0=await api('sober');if(s0&&s0.running){if(!await ask('Sober is running and can overwrite this change when it closes. Close Sober now and apply?',{ok:'Close Sober & apply',title:'Sober is running'}))return go('tweaks');await api('sober_kill',{});await new Promise(r=>setTimeout(r,1500))}
 const r=await api('sober_set',{fflag_del:del||[],fflags:set||{}});toast(r.error||(r.verified===false?'Saved, but Sober\'s config did not keep it - check the file permissions':'Saved - start Sober to apply'),5000);go('tweaks')}
async function soberFlag(k,on){const s0=await api('sober');if(s0&&s0.running){if(!await ask('Sober is running and can overwrite this change when it closes. Close Sober now and apply?',{ok:'Close Sober & apply',title:'Sober is running'}))return go('tweaks');await api('sober_kill',{});await new Promise(r=>setTimeout(r,1500))}
 const r=await api('sober_set',{[k]:on});toast(r.error||'Saved - start Sober to apply',4000);go('tweaks')}
const ffBool=(k,on,v)=>on?ffApply([],{[k]:v}):ffApply([k],{});
const ffTog=i=>{const f=FFP[i][2],on=Object.entries(f).every(([k,v])=>SOB[k]===v);on?ffApply(Object.keys(f),{}):ffApply([],f)};
const ffLight=v=>ffApply(LIGHT.map(x=>x[1]).filter(Boolean),v?{[v]:true}:{});
const ffFps=v=>v?ffApply([],{DFIntTaskSchedulerTargetFps:+v}):ffApply(['DFIntTaskSchedulerTargetFps'],{});
const ffTex=v=>v===''?ffApply(['DFFlagTextureQualityOverrideEnabled','DFIntTextureQualityOverride'],{}):ffApply([],{DFFlagTextureQualityOverrideEnabled:true,DFIntTextureQualityOverride:+v});
const ffMsaa=v=>v===''?ffApply(['FIntDebugForceMSAASamples'],{}):ffApply([],{FIntDebugForceMSAASamples:+v});
const ffGfx=v=>v===''?ffApply(['DFIntDebugFRMQualityLevelOverride'],{}):ffApply([],{DFIntDebugFRMQualityLevelOverride:+v});
const ffDel2=i=>SOBK[i]&&ffApply([SOBK[i]],{});
function ffAdd2(){const k=$('#ffk2').value.trim();if(!k)return toast('Enter a flag name');ffApply([],{[k]:pv($('#ffv2').value.trim())})}
async function ffImport(){let o;try{o=JSON.parse($('#ffj').value)}catch(e){return toast('That is not valid JSON')}
 if(!o||typeof o!='object'||Array.isArray(o))return toast('Expected a JSON object like {"FlagName": value}');
 const n={};for(const k in o){let v=o[k];if(typeof v=='string')v=pv(v);if(v!==null&&typeof v=='object')return toast('Flag '+k+' has an unsupported value');n[k]=v}
 ffApply([],n)}
const ffExport=()=>dl('sober-fflags.json',JSON.stringify(SOB,null,2),'application/json');
async function ffClear(){if(!await ask('Remove ALL Fast Flags from Sober? ',{ok:'Remove all',danger:1,title:'Reset Fast Flags'}))return;ffApply(Object.keys(SOB),{})}
async function modSet(id,on){toast(on?'Downloading from GitHub…':'Removing…',60000);const x=await api('mods_preset',{id,on});toast(x.error||(on?'Installed - restart Sober to apply':'Removed'),5000);go('tweaks')}
const modGroup=(ids,v)=>v?modSet(v,true):(ids.find(i=>MODP[i])?modSet(ids.find(i=>MODP[i]),false):go('tweaks'));
function modFont(){const inp=document.createElement('input');inp.type='file';inp.accept='.ttf,.otf,.ttc';
 inp.onchange=()=>{const f=inp.files[0];if(!f)return;if(f.size>25e6)return toast('File too big (25 MB max)');const rd=new FileReader();
  rd.onload=async()=>{toast('Installing font…',60000);const x=await api('mods_font',{b64:String(rd.result).split(',')[1]});
   toast(x.error||(x.families?'Font installed for '+x.families+' font families - restart Sober to apply':'Font file installed, but Sober\'s font family files were not found on disk, so Roblox may ignore it'),8000);go('tweaks')};rd.readAsDataURL(f)};inp.click()}
async function modFontDel(){const x=await api('mods_font',{remove:1});toast(x.error||'Custom font removed');go('tweaks')}
function modPick(i){const slot=i>=0?MODSLOTS[i][1]:null,inp=document.createElement('input');inp.type='file';
 inp.onchange=()=>{const f=inp.files[0];if(!f)return;if(f.size>25e6)return toast('File too big (25 MB max)');
  const rd=new FileReader();rd.onload=async()=>{const p=slot||await askText('Path inside Roblox content, e.g. content/sounds/ouch.ogg','content/',{title:'Custom mod path',ok:'Install'});if(!p)return;
   const x=await api('mods_put',{path:p,b64:String(rd.result).split(',')[1]});toast(x.error||(x.converted?'Converted to Ogg and installed':'Installed')+' - restart Sober to apply'+(/ouch\.ogg/.test(p)&&SOBCFG.bring_back_oof?'. Note: the "Old oof" switch is on and can conflict with your file - turn it off to hear your own sound':''),x.error||(/ouch\.ogg/.test(p)&&SOBCFG.bring_back_oof)?9000:4000);go('tweaks')};rd.readAsDataURL(f)};inp.click()}
async function modDel(i){const f=MODF[i];if(!f)return;const x=await api('mods_del',{path:f.path});toast(x.error||'Removed');go('tweaks')}
async function modAll(){if(!await ask('Remove every installed mod?',{ok:'Remove all',danger:1,title:'Remove mods'}))return;await api('mods_del',{all:1});toast('All mods removed');go('tweaks')}
Object.assign(PAGES,{async tweaks(){const [s,m]=await Promise.all([api('sober'),api('mods_list')]);const c=s.cfg||{};SOB=c.fflags||{};SOBCFG=c;SOBK=Object.keys(SOB);MODF=m.files||[];MODP=m.presets||{};MODFONT=!!m.font;
 const sel=(opts,cur,fn)=>`<select onchange="${fn}(this.value)">${opts.map(([l,v])=>`<option value="${v}" ${String(v)==String(cur)?'selected':''}>${l}</option>`).join('')}</select>`;
 const msel=(ids,opts)=>`<select onchange="modGroup(${esc(JSON.stringify(ids))},this.value)">${opts.map(([l,v])=>`<option value="${v}" ${(ids.find(i=>MODP[i])||'')==v?'selected':''}>${l}</option>`).join('')}</select>`;
 const mchk=(id,l,d)=>`<label style="display:block;margin:8px 0"><input type=checkbox ${MODP[id]?'checked':''} onchange="modSet('${id}',this.checked)"> ${l} <small style="display:inline">- ${d}</small></label>`;
 const lcur=(LIGHT.find(x=>x[1]&&SOB[x[1]]===true)||[0,''])[1]||'';
 return `<h2>Tweaks & Mods</h2><small>Fast Flags and mods for Sober. Changes apply the next time Sober starts.</small>
 ${s.running?'<div class=card style="border:1px solid var(--accent)"><b>Sober is running</b><small>Close it before changing anything: Sober can overwrite the config when it exits, and mods are only read at startup.</small><button class="btn alt sm" style="margin-top:8px" onclick="api(\'sober_kill\',{}).then(()=>{toast(\'Sober closed\');go(\'tweaks\')})">Close Sober</button></div>':''}
 <div class=card><b>Fast Flag tweaks</b><small>Current presets. Roblox ignores flags it does not allow-list, so keep to these for results you can rely on.</small>
  <div class=row style="margin-top:8px"><span style="width:220px">Anti-aliasing (MSAA)</span>${sel(MSAA,SOB.FIntDebugForceMSAASamples||'','ffMsaa')}</div>
  <div class=row style="margin-top:6px"><span style="width:220px">Texture quality</span>${sel(TEXQ,SOB.DFFlagTextureQualityOverrideEnabled?SOB.DFIntTextureQualityOverride:'','ffTex')}</div>
  <label style="display:block;margin:10px 0 4px"><input type=checkbox ${c.bring_back_oof?'checked':''} onchange="soberFlag('bring_back_oof',this.checked)"> Old "oof" death sound <small style="display:inline">- Sober's built-in switch, the most reliable way. Use the custom file below only if you want your own sound</small></label>
  <label style="display:block;margin:10px 0 4px"><input type=checkbox ${SOB.DFFlagDisableDPIScale===true?'checked':''} onchange="ffBool('DFFlagDisableDPIScale',this.checked,true)"> Fix display scaling <small style="display:inline">- turns off DPI scaling</small></label>
  <label style="display:block;margin:4px 0"><input type=checkbox ${SOB.FFlagHandleAltEnterFullscreenManually===false?'checked':''} onchange="ffBool('FFlagHandleAltEnterFullscreenManually',this.checked,false)"> Don't handle Alt+Enter fullscreen manually <small style="display:inline">- recommended default</small></label>
  <div class=row style="margin-top:10px"><button class="btn alt sm" onclick="ffClear()">Reset configuration (remove all flags)</button></div></div>
 <div class=card><b>Legacy flags</b><small>Popular in older versions. Current Roblox may ignore some or all of these.</small>
  <div class=row style="margin-top:8px"><span style="width:220px">Frame rate cap</span>${sel([['Default',''],['30',30],['60',60],['75',75],['120',120],['144',144],['165',165],['240',240],['Unlimited',9999]],SOB.DFIntTaskSchedulerTargetFps||'','ffFps')}</div>
  <div class=row style="margin-top:6px"><span style="width:220px">Lighting technology</span>${sel(LIGHT.map(x=>[x[0],x[1]||'']),lcur,'ffLight')}</div>
  <div class=row style="margin-top:6px"><span style="width:220px">Graphics level override</span>${sel([['Automatic',''],['1 (lowest)',1],['3',3],['5',5],['8',8],['10',10],['15',15],['21 (max)',21]],SOB.DFIntDebugFRMQualityLevelOverride||'','ffGfx')}</div>
  <div style="margin-top:10px">${FFP.map((p,i)=>`<label style="display:block;margin:6px 0"><input type=checkbox ${Object.entries(p[2]).every(([k,v])=>SOB[k]===v)?'checked':''} onchange="ffTog(${i})"> ${p[0]} <small style="display:inline">- ${p[1]}</small></label>`).join('')}</div></div>
 <div class=card><b>Fast Flag editor</b><small>${SOBK.length} flag(s) set right now. Add, change or remove single flags, or import / export a flag list.</small>
  <table style="margin-top:8px">${SOBK.map((k,i)=>`<tr><td><code>${esc(k)}</code></td><td><code>${esc(JSON.stringify(SOB[k]))}</code></td><td style="text-align:right"><button class="btn alt sm" onclick="ffDel2(${i})">Remove</button></td></tr>`).join('')||'<tr><td><small>No flags set</small></td></tr>'}</table>
  <div class=row style="margin-top:8px"><input id=ffk2 placeholder="FlagName" style="flex:1;min-width:200px"><input id=ffv2 placeholder="value (true, 60, text)" style="width:170px"><button class=btn onclick="ffAdd2()">Set flag</button></div>
  <textarea id=ffj rows=4 placeholder='{"DFIntTaskSchedulerTargetFps": 144}' style="width:100%;margin-top:10px"></textarea>
  <div class=row style="margin-top:8px"><button class=btn onclick="ffImport()">Import JSON</button><button class="btn alt" onclick="ffExport()">Download flags</button><button class="btn alt" onclick="copy(JSON.stringify(SOB,null,2))">Copy flags</button></div></div>
 <div class=card><b>Mods</b><small>Mod presets downloaded from GitHub and placed in Sober's asset overlay. Restart Sober to apply.</small>
  ${mchk('avatar_bg','Old avatar background','the classic avatar editor backdrop')}
  ${mchk('old_sounds','Old character sounds','classic walk, jump and get-up sounds; swim, fall, land and water sounds muted')}
  <div class=row style="margin-top:8px"><span style="width:220px">Cursor</span>${msel(CURS,[['Default',''],['2013','cursor_2013'],['2006','cursor_2006']])}</div>
  <div class=row style="margin-top:6px"><span style="width:220px">Emoji font</span>${msel(EMOJ,[['Default',''],['Catmoji','emoji_catmoji'],['Windows 11','emoji_win11'],['Windows 10','emoji_win10'],['Windows 8.1','emoji_win8']])}</div>
  <div class=row style="margin-top:6px"><span style="width:220px">Custom font</span>${MODFONT?'<span>✅ Installed</span><button class="btn alt sm" onclick="modFontDel()">Remove</button>':'<button class="btn alt sm" onclick="modFont()">Choose font (.ttf .otf .ttc)</button>'}</div></div>
 <div class=card><b>Custom files (asset overlay)</b><small>Replace any Roblox file inside Sober, like a modifications folder. Folder: ${esc(m.dir||'')}</small>
  <div class=row style="margin:8px 0">${MODSLOTS.map((x,i)=>`<button class="btn alt sm" onclick="modPick(${i})">${x[0]}</button>`).join('')}<button class="btn alt sm" onclick="modPick(-1)">Custom path...</button><button class="btn alt sm" onclick="api('mods_dir',{})">Open folder</button></div>
  <table>${MODF.map((f,i)=>`<tr><td><code>${esc(f.path)}</code></td><td>${Math.max(1,Math.round(f.size/1024))} KB</td><td style="text-align:right"><button class="btn alt sm" onclick="modDel(${i})">Remove</button></td></tr>`).join('')||'<tr><td><small>No mods installed</small></td></tr>'}</table>${MODF.length?'<button class="btn alt sm" style="margin-top:8px" onclick="modAll()">Remove all mods</button>':''}</div>
 <div class=card><b>Also in Sober Settings</b><small>Discord Rich Presence, server location indicator, close-on-leave, OpenGL, old death sound and launch command presets.</small><button class="btn alt sm" onclick="go('sober')">Open Sober Settings</button></div>`}});
// ---- catalog / buying
const buyable=x=>(x.itemType=='Asset'||x.itemType=='Bundle')&&x.price!=null&&!(x.itemRestrictions||[]).length;
// Roblox answers "invalid argument" when one query parameter is not accepted, so the search steps down to simpler requests.
async function catSearch(q,c,free){
 const kw=q?'&Keyword='+encodeURIComponent(q):'',pr=free?'&MinPrice=0&MaxPrice=0':'',B='https://catalog.roblox.com/v1/search/items/details?Limit=30'+kw,cg=c?'&Category='+c:'';
 let err='';
 for(const u of [B+cg+'&SortType=0'+pr,B+cg+pr,B+cg,B]){const r=await rb(u);if(!r.error)return(r.data||[]);err=r.error;if(!/invalid|argument|bad request|HTTP 400/i.test(err))throw new Error(err)}
 for(const u of ['https://catalog.roblox.com/v1/search/items?Limit=30'+kw+cg,'https://catalog.roblox.com/v1/search/items?Limit=30'+kw]){   // plain search + details lookup
  const s=await rb(u);if(s.error){err=s.error;continue}
  const items=(s.data||[]).map(x=>({itemType:x.itemType,id:x.id}));if(!items.length)return[];
  const d=await rb('https://catalog.roblox.com/v1/catalog/items/details',{items});if(d.error){err=d.error;continue}return d.data||[]}
 throw new Error(err||'Catalog search failed')}
async function cat(){const free=$('#cf').checked,q=$('#cq').value.trim();$('#cat').innerHTML='Loading…';
 let data;try{data=await catSearch(q,$('#cc').value,free)}catch(e){return $('#cat').innerHTML=`${esc(e.message)}<div class=row style="margin-top:8px"><button class="btn alt sm" onclick="open_('https://www.roblox.com/catalog?Keyword='+encodeURIComponent($('#cq').value))">Search on roblox.com instead</button></div>`}
 CAT=data.map(x=>{if(x.price==null&&x.priceStatus=='Free')x.price=0;return x}).filter(x=>!free||x.price===0);
 const ids=t=>CAT.filter(x=>x.itemType==t).map(x=>x.id);
 const [ta,tb]=await Promise.all([th('assets','assetIds',ids('Asset')),th('bundles/thumbnails','bundleIds',ids('Bundle'))]);
 $('#cat').innerHTML=CAT.map((x,i)=>{const bun=x.itemType=='Bundle',im=(bun?tb:ta)[x.id]||'',pr=x.price==null?(x.priceStatus=='NoResellers'?'No resellers':'Off sale'):x.price?'R$ '+x.price:'Free';
  return `<div class=tile><img src="${esc(im)}" onclick="open_('https://www.roblox.com/${bun?'bundles':'catalog'}/${x.id}')"><b>${esc(x.name)}</b><small>${pr} · ${esc(x.creatorName||'')}</small>${buyable(x)?`<button class="btn sm" onclick="buy(${i})">Buy</button>`:''}${bun?`<button class="btn alt sm" onclick="wearBundle(${x.id})">Wear</button>`:''}</div>`}).join('')||'<small>No results</small>'}
async function buy(i){const x=CAT[i];if(!x)return;
 const bun=x.itemType=='Bundle',sellers=[],addS=v=>{v=Number(v);if(v>0&&!sellers.includes(v))sellers.push(v)};
 let pid=x.productId,price=x.price,sale=true;
 try{   // re-read the authoritative product info instead of trusting search-result fields
  if(bun){const d=await rb(`https://catalog.roblox.com/v1/bundles/${x.id}/details`);
   if(!d.error&&d.product){pid=d.product.id||pid;if(d.product.priceInRobux!=null)price=d.product.priceInRobux;sale=d.product.isForSale!==false;addS(d.product.sellerId);if(d.creator)addS(d.creator.id)}}
  else{const d=await rb(`https://economy.roblox.com/v2/assets/${x.id}/details`);
   if(!d.error&&d.ProductId){pid=d.ProductId;if(d.PriceInRobux!=null)price=d.PriceInRobux;sale=d.IsForSale!==false;if(d.Creator){addS(d.Creator.CreatorTargetId);addS(d.Creator.Id)}}}
 }catch(e){}
 addS(x.creatorTargetId);
 price=Number(price)||0;
 if(!pid)return toast('Could not read purchase info for this item',4500);
 if(!sale)return toast('This item is not for sale right now',4500);
 if(!sellers.length)return toast('Could not read the seller for this item',4500);
 if(!await ask(`Buy "${x.name}" for ${price?'R$ '+price:'free'}?\nYour balance: R$ ${S.user.robux??'?'}`,{ok:'Buy',title:'Confirm purchase'}))return;
 let r={},msg='';
 const prices=[...new Set([price,Number(x.price)||0])];
 outer:for(const pc of prices)for(const sl of sellers){   // Roblox answers "invalid arguments" when the seller id or price is not the one it expects, so try each
  r=await rb(`https://economy.roblox.com/v1/purchases/products/${pid}`,{expectedCurrency:1,expectedPrice:pc,expectedSellerId:sl});
  msg=String(r.error||r.errorMsg||r.reason||r.title||'');
  if(r.purchased||!/invalid|argument|seller|mismatch|price/i.test(msg))break outer}
 if(!r.purchased&&/invalid|argument/i.test(msg)){
  if(await ask(`Roblox rejected the purchase (${msg}).\nOpen the item page to buy it there instead?`,{ok:'Open page',title:'Could not buy here'}))open_(`https://www.roblox.com/${bun?'bundles':'catalog'}/${x.id}`);return}
 if(r.error&&!r.purchased)return toast(r.error,5000);
 toast(r.purchased?(bun?'Purchased! Press Wear on it, or find it in Avatar > Wardrobe > Bundles':'Purchased! Find it in Avatar > Wardrobe'):(msg||'Not purchased'),5000);
 api('me').then(u=>{if(u.robux!=null){S.user=u;const e=$('header .pill:not(#act)');e&&(e.textContent='R$ '+u.robux.toLocaleString())}})}
// ---- profile lookup
let PCUR=null;
const hsx=(u,b)=>rb(u,b).catch(()=>({error:'failed'}));
// /friends stops at 200 people, so the paged /friends/find endpoint fills in the rest (the same approach the Friends tab uses)
async function allFr(id){const B='https://friends.roblox.com/v1/users/'+id,seen=new Set(),out=[],add=x=>{const i=x.id||x.userId;if(i&&!seen.has(i)){seen.add(i);out.push({id:i,name:x.name||'',displayName:x.displayName||''})}};
 const a=await hsx(B+'/friends');(a.data||[]).forEach(add);const c=await hsx(B+'/friends/count'),tot=c.count||0;
 if(out.length<tot||!out.length){let cur='';for(let i=0;i<80;i++){const r=await hsx(B+'/friends/find?limit=50&userSort=1'+(cur?'&cursor='+encodeURIComponent(cur):''));if(r.error)break;(r.PageItems||r.pageItems||r.data||[]).forEach(add);cur=r.NextCursor||r.nextCursor||r.nextPageCursor;if(!cur)break}}
 return out}
async function fillUsers(list){const need=list.filter(f=>!(f.name&&f.displayName)).map(f=>f.id),nm={};for(let i=0;i<need.length;i+=100){const u=await rb('https://users.roblox.com/v1/users',{userIds:need.slice(i,i+100),excludeBannedUsers:false});(u.data||[]).forEach(x=>nm[x.id]=x)}
 return list.map(f=>{const x=nm[f.id]||{};return{id:f.id,name:f.name||x.name||'',display:f.displayName||x.displayName||f.name||x.name||('User '+f.id)}})}
async function thumbMap(kind,ids,q){const m={};for(let i=0;i<ids.length;i+=100){const r=await hsx('https://thumbnails.roblox.com/v1/'+kind+'?'+q+'='+ids.slice(i,i+100).join(',')+'&returnPolicy=PlaceHolder&size=150x150&format=Png');(r.data||[]).forEach(x=>m[x.targetId]=x.imageUrl)}return m}
const psec=(t,h)=>h?`<div class=card><b>${t}</b><div style="margin-top:8px">${h}</div></div>`:'';
async function prof(pid){const box=$('#pf');if(!box)return;box.innerHTML='Looking up…';let id=pid;
 if(!id){const nm=($('#pu').value||'').trim();if(!nm)return box.innerHTML='Type a username first';
  const r=await rb('https://users.roblox.com/v1/usernames/users',{usernames:[nm],excludeBannedUsers:false}),u0=r.data&&r.data[0];if(!u0)return box.innerHTML='User not found';id=u0.id}
 PCUR=id;const me=S.user&&S.user.id==id,U='https://users.roblox.com/v1/users/'+id,F='https://friends.roblox.com/v1/users/'+id;
 const [i,fc,fo,fi,pr,av,hs,gr,fl,cg,fg,wr,rbd,sl,bd,st]=await Promise.all([
  hsx(U),hsx(F+'/friends/count'),hsx(F+'/followers/count'),hsx(F+'/followings/count'),
  S.connected?hsx('https://presence.roblox.com/v1/presence/users',{userIds:[id]}):{},
  hsx('https://thumbnails.roblox.com/v1/users/avatar?userIds='+id+'&size=420x420&format=Png'),
  hsx(U+'/username-history?limit=25&sortOrder=Desc'),
  hsx('https://groups.roblox.com/v2/users/'+id+'/groups/roles'),
  allFr(id),
  hsx('https://games.roblox.com/v2/users/'+id+'/games?accessFilter=Public&limit=12&sortOrder=Desc'),
  hsx('https://games.roblox.com/v2/users/'+id+'/favorite/games?accessFilter=2&limit=12&sortOrder=Desc'),
  hsx('https://avatar.roblox.com/v1/users/'+id+'/avatar'),
  hsx('https://accountinformation.roblox.com/v1/users/'+id+'/roblox-badges'),
  hsx('https://accountinformation.roblox.com/v1/users/'+id+'/promotion-channels'),
  hsx('https://badges.roblox.com/v1/users/'+id+'/badges?limit=12&sortOrder=Desc'),
  S.connected&&!me?hsx('https://friends.roblox.com/v1/users/'+S.user.id+'/friends/statuses?userIds='+id):{}]);
 if(i.error||!i.name)return box.innerHTML=esc(i.error||'User not found');
 const pu=$('#pu');if(pu)pu.value=i.name;
 const frl=await fillUsers(Array.isArray(fl)?fl:[]);
 const mine=S.connected&&!me?(FR.length?FR:await loadFriends()):[],mset=new Set(mine.map(f=>f.id)),mut=frl.filter(f=>mset.has(f.id));
 const exp=(cg.data||[]).filter(x=>x.rootPlace&&x.rootPlace.id),fav=(fg.data||[]).filter(x=>x.rootPlace&&x.rootPlace.id),wa=(wr.assets||[]);
 const [fth,gth,ath]=await Promise.all([thumbMap('users/avatar-headshot',frl.map(f=>f.id),'userIds'),thumbMap('games/icons',[...new Set([...exp,...fav].map(x=>x.id))],'universeIds'),thumbMap('assets',wa.map(a=>a.id),'assetIds')]);
 const p=((pr.userPresences||[])[0])||{},stt=((st.data||[])[0]||{}).status||'',isF=!!(S.connected&&!me&&(stt=='Friends'||FR.some(f=>f.id==id)));
 const lsr=(await api('lastseen',{id:id}))||{};
  const where=p.userPresenceType==2?'🟢 In game'+(p.lastLocation?': <b>'+esc(p.lastLocation)+'</b>':''):p.userPresenceType==1?'🔵 Online':p.userPresenceType==3?'🟠 In Studio':S.connected?'⚫ Offline'+(p.lastOnline?' · last online '+esc(new Date(p.lastOnline).toLocaleString()):''):'';
 const pasts=(hs.data||[]).map(x=>esc(x.name)).join(' · '),sls=Object.entries(sl&&!sl.error?sl:{}).filter(([k,v])=>typeof v=='string'&&v).map(([k,v])=>`<span class=stat style="margin-right:14px"><b style="font-size:13px">${esc(v)}</b>${esc(k)}</span>`).join('');
 const rbs=(Array.isArray(rbd)?rbd:[]).map(x=>`<img title="${esc(x.name)}" src="${esc(x.imageUrl||'')}" style="width:40px;height:40px;margin-right:6px">`).join('');
 const lr=lsr.rec;let lsl='';if(lr&&lr.t&&!(p.userPresenceType>0)){lsl=`<small style="display:block;margin-top:2px">Last online <b>${agoS(lr.t)}</b> (${esc(new Date(lr.t*1000).toLocaleString())})${lr.ing&&lr.g?' - was playing <b>'+esc(lr.g)+'</b>':''}</small>`+(lr.g&&!lr.ing?`<small style="display:block">Last game seen: <b>${esc(lr.g)}</b>${lr.gt?' - '+agoS(lr.gt):''}</small>`:'')}
  let acts='';
 if(S.connected&&!me){
  if(isF)acts+=`<span class=badge style="background:var(--green);margin:0 8px 0 0">✓ Friends</span><button class="btn alt" onclick="chatWith(${id})">💬 Message</button>`;
  else if(stt=='RequestSent')acts+=`<button class="btn alt" disabled>Request sent</button>`;
  else if(stt=='RequestReceived')acts+=`<button class=btn onclick="guard(rb('https://friends.roblox.com/v1/users/${id}/accept-friend-request',{}),'Friend request accepted').then(()=>prof(${id}))">Accept request</button>`;
  else acts+=`<button class=btn onclick="guard(rb('https://friends.roblox.com/v1/users/${id}/request-friend',{}),'Request sent').then(()=>prof(${id}))">Add friend</button>`;
  if(isF&&p.userPresenceType==2&&p.rootPlaceId)acts+=`<button class="btn play" style="padding:9px 22px" onclick="pl(${p.rootPlaceId},'${esc(p.gameId||'')}')">Join in Sober</button>`;
  acts+=`<button class="btn alt" onclick="guard(rb('https://friends.roblox.com/v1/users/${id}/follow',{}),'Following')">Follow</button>`;
 }
 acts+=`<button class="btn alt" onclick="open_('https://www.roblox.com/users/${id}/profile')">Open on roblox.com</button><button class="btn alt" onclick="copy('https://www.roblox.com/users/${id}/profile')">Copy link</button>`;
 box.innerHTML=`<div class=card><div class=row style="align-items:flex-start;gap:20px"><img src="${esc(av.data&&av.data[0]?av.data[0].imageUrl:'')}" style="width:220px"><div style="flex:1;min-width:260px"><h2 style="margin:0">${esc(i.displayName)}${i.hasVerifiedBadge?' ✔':''}</h2><small>@${esc(i.name)} · joined ${esc((i.created||'').slice(0,10))} · ID ${id}</small>${i.isBanned?'<small style="color:#e5484d">⛔ This account is banned</small>':''}${where?`<p style="margin:8px 0">${where}</p>`:''}${lsl}${i.description?`<p style="white-space:pre-wrap">${esc(i.description)}</p>`:'<small>No about text</small>'}
 <span class=stat><b>${fc.count??'—'}</b>friends</span><span class=stat><b>${fo.count??'—'}</b>followers</span><span class=stat><b>${fi.count??'—'}</b>following</span>${S.connected&&!me?`<span class=stat><b>${mut.length}</b>mutual</span>`:''}${pasts?`<small style="margin-top:8px">Past usernames: ${pasts}</small>`:''}${sls?`<div style="margin-top:8px">${sls}</div>`:''}${rbs?`<div style="margin-top:8px">${rbs}</div>`:''}
 <div class=row style="margin-top:12px">${acts}</div></div></div></div>`
 +psec('Currently wearing ('+wa.length+')',wa.length?`<div style="display:flex;flex-wrap:wrap;gap:10px">${wa.map(a=>`<div style="width:84px;text-align:center;cursor:pointer" title="${esc(a.name)}" onclick="open_('https://www.roblox.com/catalog/${a.id}')"><img loading=lazy src="${esc(ath[a.id]||'')}" style="width:72px;height:72px;border-radius:var(--radius);background:#ffffff0d"><small style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(a.name)}</small></div>`).join('')}</div>`:'')
 +psec('Experiences made by '+esc(i.displayName)+' ('+exp.length+')',exp.length?`<div class=rs>${exp.map(x=>tile({place:x.rootPlace.id,name:x.name,icon:gth[x.id]},(x.placeVisits||0).toLocaleString()+' visits')).join('')}</div>`:'')
 +psec('Favorite experiences',fav.length?`<div class=rs>${fav.map(x=>tile({place:x.rootPlace.id,name:x.name,icon:gth[x.id]},'')).join('')}</div>`:'')
 +psec('Groups ('+(gr.data||[]).length+')',(gr.data||[]).length?`<table>${gr.data.map(x=>`<tr><td><b>${esc((x.group||{}).name)}</b>${(x.group||{}).hasVerifiedBadge?' ✔':''}<small style="margin:0">${esc((x.role||{}).name||'')}</small></td><td style="text-align:right"><small style="margin:0">${((x.group||{}).memberCount||0).toLocaleString()} members</small></td><td style="text-align:right"><button class="btn alt sm" onclick="open_('https://www.roblox.com/groups/${(x.group||{}).id}')">Open</button></td></tr>`).join('')}</table>`:'')
 +(mut.length?psec('Mutual friends ('+mut.length+')',`<div style="display:flex;flex-wrap:wrap;gap:12px">${mut.map(f=>`<div class=fr onclick="prof(${f.id})" title="@${esc(f.name)}"><div class=ava><img loading=lazy src="${esc(fth[f.id]||'')}"></div><b>${esc(f.display)}</b><small>@${esc(f.name)}</small></div>`).join('')}</div>`):'')
 +psec('Friends ('+frl.length+(fc.count>frl.length?' of '+fc.count:'')+')',frl.length?`<div style="display:flex;flex-wrap:wrap;gap:12px;max-height:320px;overflow:auto">${frl.map(f=>`<div class=fr onclick="prof(${f.id})" title="@${esc(f.name)}"><div class=ava><img loading=lazy src="${esc(fth[f.id]||'')}"></div><b>${esc(f.display)}</b><small>@${esc(f.name)}</small></div>`).join('')}</div>`:'')
 +((bd.data||[]).length?psec('Recent badges',`<small>${bd.data.map(x=>esc(x.name)).join(' · ')}</small>`):'')}
// ---- roblox settings
const PRIV=[['Who can chat with me (app)','app-chat-privacy','appChatPrivacy',['NoOne','Friends','Everyone']],['Who can chat with me (in experience)','game-chat-privacy','gameChatPrivacy',['NoOne','Friends','Everyone']],['Who can see my inventory','inventory-privacy','inventoryPrivacy',['NoOne','Friends','FriendsAndFollowing','AllUsers']]];
async function privLoad(){let h='';for(const [n,u,k,o] of PRIV){const r=await rb('https://accountsettings.roblox.com/v1/'+u),v=r[k];
 h+=r.error?`<div>${n}: <small style="display:inline">unavailable</small></div>`:`<div class=row style="margin:6px 0"><span style="width:300px">${n}</span><select onchange="guard(rb('https://accountsettings.roblox.com/v1/${u}',{${k}:this.value}),'Saved')">${[...new Set([v,...o])].map(x=>`<option ${x==v?'selected':''}>${x}</option>`).join('')}</select></div>`}
 $('#priv').innerHTML=h}
const saveAbout=()=>guard(rb('https://users.roblox.com/v1/description',{description:$('#about').value}),'About saved');
const saveDn=()=>guard(rb(`https://users.roblox.com/v1/users/${S.user.id}/display-names`,{newDisplayName:$('#dn').value},'PATCH'),'Display name changed');
// ---- messages
async function loadMsgs(reset){if(reset){MP=0;$('#msg').innerHTML=''}const r=await rb(`https://privatemessages.roblox.com/v1/messages?pageNumber=${MP}&pageSize=25&messageTab=${MT}`);const el=$('#msg');if(!el)return;
 if(r.error){el.innerHTML=esc(r.error);return}
 const html=(r.collection||[]).map(m=>`<details><summary>${m.isRead?'':'● '}<b>${esc(m.subject)}</b> — ${esc(MT=='Sent'?(m.recipient&&m.recipient.displayName):(m.sender&&m.sender.displayName))} <small style="display:inline">${(m.created||'').slice(0,10)}</small></summary><p>${esc(m.body)}</p></details>`).join('');
 el.insertAdjacentHTML('beforeend',html);if(MP==0&&!html)el.innerHTML='<small>Nothing here</small>';MP++;const mm=$('#mm');mm&&(mm.style.display=MP<(r.totalPages||0)?'inline-block':'none')}
Object.assign(PAGES,{
 avatar(){return needConn()||`<h2>Avatar</h2><div class=avx><div class="card avl"><div id=bwrap class=busy><img id=bust alt=""></div>
  <div class=row style="justify-content:center;margin:8px 0">${[['avatar','Full'],['avatar-bust','Bust'],['avatar-headshot','Head']].map(([v,n])=>`<button class="btn alt sm${v==AVV?' sel':''}" data-v=${v} onclick="avView('${v}')">${n}</button>`).join('')}</div>
  <div class=row style="justify-content:center"><button class="btn alt sm" onclick="avUndo()">↩ Undo</button><button class="btn alt sm" onclick="redraw()">⟳ Re-render</button><button class="btn sm" onclick="saveOutfit()">+ Save outfit</button></div>
  <div class=row id=avmeta style="justify-content:center;margin-top:8px"></div>
  <details style="text-align:left;margin-top:10px"><summary>Body, scale &amp; skin tone</summary><div id=avopt style="margin-top:8px"></div><div class=row style="margin-top:10px">${SKIN.map(c=>`<span style="width:28px;height:28px;border-radius:50%;cursor:pointer;background:${c[1]}" onclick="skin(${c[0]})"></span>`).join('')}</div></details></div>
 <div class=avr><div class=card><div class=row><b style="flex:1">Wearing</b><button class="btn alt sm" onclick="unwearAcc()">Remove accessories</button></div><div id=worn class="rs avw">Loading...</div></div>
  <div class=card><div class=row id=avtabs></div><div class=row id=avsub style="margin-top:8px"></div><div class=row style="margin-top:8px"><input id=wq placeholder="Search this category..." oninput="wfilter()" style="flex:1"></div><div id=ward class=grid style="margin-top:12px">...</div><button id=wmore class="btn alt" style="display:none;margin-top:10px" onclick="wardrobe(false)">Load more</button></div></div></div>`},
 inbox(){return needConn()||`<h2>Messages</h2><div class=row><select onchange="MT=this.value;loadMsgs(true)">${['Inbox','Sent','Archive'].map(t=>`<option ${t==MT?'selected':''}>${t}</option>`).join('')}</select></div><div id=msg class=card style="margin-top:12px">Loading…</div><button id=mm class="btn alt" style="display:none" onclick="loadMsgs()">Load more</button>`},
 groups(){return needConn()||'<h2>Communities</h2><div id=gr class=card>Loading…</div>'},
 profile(){return `<h2>Profile</h2><div class=row><input id=pu placeholder=Username value="${esc(PU||(S.user?S.user.name:''))}" onkeydown="if(event.key=='Enter')prof()"><button class=btn onclick="prof()">Look up</button></div><div id=pf style="margin-top:14px"></div>`},
 rset(){return needConn()||`<h2>Roblox Settings</h2><div class=card><b>Real Roblox settings</b><small>Opens the official roblox.com page in your browser</small><div class=row style="margin-top:8px">${[['Account Info','info'],['Security','security'],['Privacy','privacy'],['Parental Controls','parental-controls'],['Notifications','notifications'],['Billing','billing']].map(([n,h])=>`<button class=btn onclick="open_('https://www.roblox.com/my/account#!/${h}')">${n}</button>`).join('')}</div></div><h3>Quick edits inside Sober Hub</h3><div class=card><b>Profile</b><textarea id=about rows=3 placeholder="About me"></textarea><div class=row><button class=btn onclick="saveAbout()">Save about</button><input id=dn placeholder="New display name"><button class="btn alt" onclick="saveDn()">Change display name</button></div></div><div class=card><b>Privacy</b><div id=priv>Loading…</div></div>`}});
Object.assign(AFTER,{
 avatar(){if(S.connected)avLoad()},
 inbox(){if(S.connected)loadMsgs(true)},
 async groups(){if(!S.connected)return;const r=await rb(`https://groups.roblox.com/v1/users/${S.user.id}/groups/roles`);$('#gr').innerHTML=r.error?esc(r.error):'<table>'+(r.data||[]).map(x=>`<tr><td><b>${esc(x.group.name)}</b></td><td>${esc(x.role.name)}</td><td>${x.group.memberCount.toLocaleString()} members</td><td><button class="btn alt" onclick="open_('https://www.roblox.com/communities/${x.group.id}')">Open</button></td></tr>`).join('')+'</table>'},
 profile(){if(S.user||PU){prof();PU=''}},
 async rset(){if(!S.connected)return;rb(`https://users.roblox.com/v1/users/${S.user.id}`).then(r=>{$('#about')&&($('#about').value=r.description||'')});privLoad()}});
// ---- right-click menu, hidden games / chats, private servers
const hidSet=()=>new Set((S.hidden_games||[]).map(x=>String(x.place)));
const hcSet=()=>new Set(Object.keys(S.hidden_chat||{}));
function closeCtx(){const m=$('#ctx');m&&m.remove()}
function ctxMenu(e,items){e.preventDefault();e.stopPropagation();closeCtx();CTXI=items;const m=document.createElement('div');m.id='ctx';
 m.style='position:fixed;z-index:30;background:var(--panel);border:1px solid #ffffff1f;border-radius:var(--radius);padding:4px;min-width:230px;box-shadow:0 8px 30px #0009';
 m.innerHTML=items.map((x,i)=>x==='-'?'<hr style="border:0;border-top:1px solid #ffffff14;margin:4px 0">':`<a class=cmd onclick="ctxRun(${i})">${esc(x[0])}</a>`).join('');
 document.body.appendChild(m);m.style.left=Math.max(4,Math.min(e.clientX,innerWidth-m.offsetWidth-6))+'px';m.style.top=Math.max(4,Math.min(e.clientY,innerHeight-m.offsetHeight-6))+'px'}
function ctxRun(i){const x=CTXI[i];closeCtx();x&&x[1]()}
document.addEventListener('click',closeCtx);document.addEventListener('contextmenu',e=>{closeCtx();if(/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)||e.target.closest('#modal'))return;airMenu(e)});window.addEventListener('blur',closeCtx);window.addEventListener('scroll',closeCtx,true);
document.addEventListener('keydown',e=>{if(e.key=='Escape')closeCtx()});
// -- games
function gameMenu(e,p){const g=GM[p]||{place:p,name:nameOf(p)},isF=S.favs.some(f=>f.place==p),hd=hidSet().has(String(p));
 ctxMenu(e,[['▶ Play in Sober',()=>play(p,g.name||'',g.icon||'')],['ℹ Details',()=>openGame(p)],['🔒 Private servers…',()=>openGame(p,'priv')],'-',
  [isF?'★ Remove from favorites':'☆ Add to favorites',async()=>{await fav(g,!isF);toast(isF?'Removed from favorites':'Added to favorites')}],
  [(S.later||[]).some(x=>x.place==p)?'🕒 Remove from Play later':'🕒 Play later',()=>laterToggle(g)],['🔀 Server hop',()=>hop(p)],
  hd?['👁 Show in recommendations again',()=>unhideGame(p)]:['🙈 Hide from recommendations',()=>hideGame(g)],'-',
  ['🔗 Copy link',()=>copy('https://www.roblox.com/games/'+p)]])}
function drawDisc(){const el=$('#disc');if(!el||!Array.isArray(DISC))return;const h=hidSet();
 el.innerHTML=DISC.map(s=>({name:s.name,games:s.games.filter(g=>!h.has(String(g.place)))})).filter(s=>s.games.length).map(s=>`<h3>${esc(s.name)}</h3><div class=rs>${s.games.map(dtile).join('')}</div>`).join('')}
async function hideGame(g){const l=(S.hidden_games||[]).filter(x=>x.place!=g.place);l.push({place:g.place,name:g.name||nameOf(g.place),icon:g.icon||''});
 await save({hidden_games:l},1);toast('Hidden from recommendations. Undo it in Hub Settings.',4000);drawDisc();refreshHome()}
async function unhideGame(p,reload){await save({hidden_games:(S.hidden_games||[]).filter(x=>x.place!=p)},1);toast('Will show in recommendations again');reload?go('hub'):(drawDisc(),refreshHome())}
// -- chats
const uname=id=>{for(const c of CV){const u=(c.user_data||{})[id];if(u)return u.combined_name||u.display_name||u.name}const f=FR.find(x=>x.id==id);return f?f.display:'User '+id};
async function hideChat(id,name){const h={...(S.hidden_chat||{})};h[id]=name||uname(id);await save({hidden_chat:h},1);toast('Chats from '+h[id]+' are hidden');
 const c=CV.find(x=>x.id==CID);if(c&&c.type=='one_to_one'&&cvOther(c)==id){CID=null;CLAST='';const p=$('#cvp');p&&(p.innerHTML='<small>Pick a chat on the left.</small>')}
 if(tab=='chat'){await loadConvs(true,1);CID&&openConv(CID)}}
async function unhideChat(id,reload){const h={...(S.hidden_chat||{})};delete h[id];await save({hidden_chat:h},1);reload?go('hub'):(tab=='chat'&&loadConvs(true,1))}
function convMenu(e,i){const c=CV[i];if(!c)return;const o=cvOther(c),one=c.type=='one_to_one'&&o;
 ctxMenu(e,[['💬 Open chat',()=>openConv(c.id)],...(one?[['🙂 View profile',()=>{PU=((c.user_data||{})[o]||{}).name||'';go('profile')}],'-',['🙈 Hide chats from '+uname(o),()=>hideChat(o)]]:[['ℹ Group chat: right-click a person\'s message to hide them',()=>{}]])])}
function msgMenu(e,uid){const c=CV.find(x=>x.id==CID)||{};
 ctxMenu(e,[['🙈 Hide chats from '+uname(uid),()=>hideChat(uid)],['🙂 View profile',()=>{PU=((c.user_data||{})[uid]||{}).name||'';go('profile')}]])}
function friendMenu(e,id){const f=FR.find(x=>x.id==id);if(!f)return;const hd=hcSet().has(String(id));
 ctxMenu(e,[...(f.status==2&&f.place?[['▶ Join in Sober',()=>joinFriend(id)],['ℹ View game',()=>openGame(f.place)]]:[]),['💬 Message',()=>chatWith(id)],['🙂 Profile',()=>{PU=f.name;go('profile')}],
  [(S.pinned||[]).includes(id)?'★ Unpin':'☆ Pin',()=>pin(id)],
  ['✏ '+(f.nick?'Change nickname…':'Set nickname…'),()=>setNick(id)],...(f.nick?[['🧹 Remove nickname',()=>setNick(id,1)]]:[]),'-',hd?['👁 Unhide chats',()=>unhideChat(id)]:['🙈 Hide chats from '+f.display,()=>hideChat(id,f.display)]])}
// -- hub settings card
const hiddenCard=()=>{const g=S.hidden_games||[],c=Object.entries(S.hidden_chat||{}),row=(t,f)=>`<div class=row style="margin:4px 0"><span style="flex:1">${t}</span>${f}</div>`;
 return `<div class=card><b>Hidden items</b><small>Hiding only affects this hub. Nothing changes on your Roblox account.</small>
 <h3 style="margin:12px 0 4px">🙈 Games hidden from recommendations (${g.length})</h3>${g.map(x=>row(esc(x.name||x.place),`<button class="btn alt sm" onclick="unhideGame(${+x.place},1)">Unhide</button>`)).join('')||'<small>None. Right-click any game tile and choose Hide from recommendations.</small>'}
 <h3 style="margin:12px 0 4px">💬 Players with hidden chats (${c.length})</h3>${c.map(([id,n])=>row(esc(n),`<button class="btn alt sm" onclick="unhideChat(${+id},1)">Unhide</button>`)).join('')||'<small>None. Right-click a chat, a message or a friend and choose Hide chats.</small>'}</div>`};
// -- private servers
const pOf=s=>s.placeId||s.rootPlaceId||(s.game&&(s.game.rootPlaceId||s.game.placeId||(s.game.rootPlace&&s.game.rootPlace.id)))||(s.rootPlace&&s.rootPlace.id)||(s.universe&&s.universe.rootPlaceId)||null;
const pvIds=s=>[...new Set([s.vipServerId,s.vipServerID,s.privateServerId,s.id,s.serverId].filter(v=>v!=null&&/^\d+$/.test(String(v))&&String(v)!='0').map(String))];
async function pvFetch(s){let ids=pvIds(s),err='';
 if(!ids.length&&s.name){const m=await rb('https://games.roblox.com/v1/private-servers/my-private-servers'),l=m.data||(Array.isArray(m)?m:[]),f=l.find(x=>x.name==s.name);if(f)ids=pvIds(f)}
 for(const id of ids){const d=await rb('https://games.roblox.com/v1/vip-servers/'+id);if(!d.error)return{id,d};err=d.error}
 return{err:(err||'Could not find this server\'s numeric id')+' [tried: '+(ids.join(', ')||'none')+'; fields: '+Object.keys(s).slice(0,8).join(', ')+']'}}
const pvBtns=i=>`<button class="btn sm" onclick="pvJoin(${i})">Join</button><button class="btn alt sm" onclick="pvCopy(${i})">Copy link</button><button class="btn alt sm" onclick="pvRemove(${i})">✕</button>`;
const pvJoin=i=>{const x=(S.privs||[])[i];x&&play(x.place,x.name||'','',null,x.code)};
const pvCopy=i=>{const x=(S.privs||[])[i];x&&copy(`https://www.roblox.com/games/${x.place}?privateServerLinkCode=${x.code}`)};
async function pvRemove(i){const l=(S.privs||[]).slice();l.splice(i,1);await save({privs:l},1);pvSaved(cur&&cur.place);tab=='privs'&&go('privs')}
const privCard=p=>`<div class=card id=privcard><b>🔒 Private servers</b><small>Saved links stay on this PC. Creating and managing servers uses your connected Roblox account.</small>
 <div id=pvs style="margin-top:8px"></div>
 <div class=row style="margin-top:8px"><input id=pvl placeholder="Paste a private server link…" style="flex:1;min-width:220px"><input id=pvn placeholder="Label (optional)" style="width:170px"><button class=btn onclick="pvAdd(${p})">＋ Add</button></div>
 ${S.connected?`<div class=row style="margin-top:10px"><button class="btn alt" onclick="pvLoad(${p})">Show private servers I can join</button><button class="btn alt" onclick="pvCreate(${p})">＋ Create private server</button></div><div id=pvr></div>`:'<small style="margin-top:10px">Connect your account to list, create and manage Roblox private servers.</small>'}</div>`;
function pvSaved(p){const el=$('#pvs');if(!el)return;const l=(S.privs||[]).map((x,i)=>[x,i]).filter(([x])=>x.place==p);
 el.innerHTML=l.length?'<table>'+l.map(([x,i])=>`<tr><td><b>${esc(x.label||'Private server')}</b></td><td style="text-align:right;white-space:nowrap">${pvBtns(i)}</td></tr>`).join('')+'</table>':'<small>No saved private servers for this game yet</small>'}
async function pvParse(s){s=String(s||'').trim();const l=parseLink(s);if(l&&l.code)return l;
 const m=s.match(/share\?code=(\w+)&type=Server/i)||s.match(/[?&]code=(\w+)[^#]*type=Server/i);
 if(m){const r=await rb('https://apis.roblox.com/sharelinks/v1/resolve-link',{linkId:m[1],linkType:'Server'}),d=r.privateServerInviteData;
  if(d&&d.linkCode&&d.placeId)return{place:String(d.placeId),code:d.linkCode};return{err:r.error||'Roblox could not resolve that share link'}}
 return null}
async function pvAdd(p){const l=await pvParse($('#pvl').value);if(!l||l.err)return toast(l?l.err:'Paste a private server link (with privateServerLinkCode=… or a roblox.com/share link)',5000);
 if(l.place&&String(l.place)!=String(p))return toast('That link is for a different game',4000);
 const lab=$('#pvn').value.trim()||('Private server '+((S.privs||[]).filter(x=>x.place==p).length+1));
 await save({privs:[...(S.privs||[]).filter(x=>x.code!=l.code),{place:+p,name:(cur&&cur.name)||nameOf(p),code:l.code,label:lab}]},1);toast('Private server saved');$('#pvl').value='';$('#pvn').value='';pvSaved(p)}
async function pvAddAny(){const l=await pvParse($('#pvl').value);if(!l||l.err||!l.place)return toast(l&&l.err?l.err:'Paste a private server link that includes the game',5000);
 const g=await api('game',{place:l.place}),lab=$('#pvn').value.trim()||'Private server';
 await save({privs:[...(S.privs||[]).filter(x=>x.code!=l.code),{place:+l.place,name:g.error?nameOf(l.place):g.name,code:l.code,label:lab}]},1);toast('Private server saved');go('privs')}
async function pvCode(s){if(s.accessCode)return s.accessCode;const f=await pvFetch(s);if(f.err)throw new Error(f.err);const d=f.d;
 return d.joinCode||((String(d.link||'').match(/privateServerLinkCode=([\w-]+)/)||[])[1])||''}
const findPrice=(o,d=0)=>{if(!o||typeof o!='object'||d>2)return null;for(const k of Object.keys(o))if(/price/i.test(k)&&typeof o[k]=='number')return o[k];for(const k of Object.keys(o))if(k!='data'&&o[k]&&typeof o[k]=='object'){const v=findPrice(o[k],d+1);if(v!=null)return v}return null};
const PVP={},GNM={};
async function gName(p){if(GNM[p])return GNM[p];const k=nameOf(p);if(k&&k!=String(p))return GNM[p]=k;try{const g=await api('game',{place:p});if(g&&g.name)return GNM[p]=g.name}catch(e){}return'game '+p}
async function pvJoinMine(i){const s=MYP[i];if(!s)return;let p=pOf(s);try{
 if(!p){const f=await pvFetch(s);if(!f.err)p=pOf(f.d)}
 if(!p)return toast('Roblox did not say which game this server is for',5000);
 const c=await pvCode(s);if(!c)return toast('Roblox did not return a join code for this server',5000);
 const nm=await gName(p);play(p,nm,'',null,c)}catch(e){toast(e.message,5000)}}
async function pvGamePrice(uid,extra){if(uid&&PVP[uid])return PVP[uid];let v={on:true,price:findPrice(extra)};
 if(uid){const en=await rb('https://games.roblox.com/v1/private-servers/enabled-in-universe/'+uid);if(!en.error){v.on=en.privateServersEnabled!==false;const pr=findPrice(en);if(pr!=null)v.price=pr}PVP[uid]=v}return v}
async function pvPriceLine(el,r){const v=await pvGamePrice(cur&&cur.uid,r);if(!el.isConnected)return;
 el.insertAdjacentHTML('afterbegin',`<small style="display:block;margin:8px 0">💰 ${!v.on?'This game does not offer private servers':v.price==null?'Roblox did not return a price for this game':v.price?'Creating a private server costs R$ '+v.price+' per month':'Private servers are free for this game'}</small>`)}
async function pvMineP(s,i){const f=await pvFetch(s),e=$('#mp'+i);if(!e||f.err)return;
 if(!pOf(s)){const q=pOf(f.d);if(q)s.placeId=q}
 const ge=$('#mg'+i);if(ge&&!ge.textContent){const nm=(f.d.universe&&f.d.universe.name)||(pOf(s)&&await gName(pOf(s)));if(nm)ge.textContent='🎮 '+nm}
 const sb=(f.d&&f.d.subscription)||{};
 e.textContent=[sb.price!=null?(sb.price?'R$ '+sb.price+'/month':'Free'):'',sb.active===true?'renewal on':sb.active===false?'renewal off':'',sb.expiresAt?'until '+String(sb.expiresAt).slice(0,10):''].filter(Boolean).join(' · ')}
async function pvLoad(p){const el=$('#pvr');el.innerHTML='Loading…';const r=await rb(`https://games.roblox.com/v1/games/${p}/private-servers`);if(r.error){el.innerHTML=esc(r.error);return}
 PVL=r.data||[];const me=S.user.id;
 el.innerHTML=PVL.length?'<table>'+PVL.map((s,i)=>{const o=s.owner||{},mine=o.id==me||o.userId==me;
  return `<tr><td><b>${esc(s.name||'Private server')}</b>${mine?' <span class=badge style="background:var(--green)">yours</span>':''}<small style="margin:0">by ${esc(o.displayName||o.name||'?')} · ${s.playing??0}/${s.maxPlayers??'?'} players</small></td><td style="text-align:right;white-space:nowrap"><button class="btn sm" onclick="pvJoinR(${p},${i})">Join</button>${mine?`<button class="btn alt sm" onclick="pvManage(PVL[${i}],${p})">⚙ Manage</button>`:''}<button class="btn alt sm" onclick="pvSaveR(${p},${i})">💾 Save</button></td></tr>`}).join('')+'</table>':'<small style="margin-top:8px">No private servers you can access for this game</small>';pvPriceLine(el,r)}
async function pvJoinR(p,i){try{const c=await pvCode(PVL[i]);if(!c)return toast('Roblox did not return a join code for this server',5000);play(p,(cur&&cur.name)||'',(cur&&cur.icon)||'',null,c)}catch(e){toast(e.message,5000)}}
async function pvSaveR(p,i){try{const s=PVL[i],c=await pvCode(s);if(!c)return toast('Roblox did not return a join code for this server',5000);
 await save({privs:[...(S.privs||[]).filter(x=>x.code!=c),{place:+p,name:(cur&&cur.name)||nameOf(p),code:c,label:s.name||'Private server'}]},1);toast('Saved');pvSaved(p)}catch(e){toast(e.message,5000)}}
async function pvCreate(p){if(!cur||!cur.uid)return toast('Open the game page first');
 const en=await rb('https://games.roblox.com/v1/private-servers/enabled-in-universe/'+cur.uid);if(en.error)return toast(en.error,5000);if(en.privateServersEnabled===false)return toast('This game does not offer private servers',4000);
 const name=(await askText('Name for the new private server','My server',{title:'New private server'})||'').trim();if(!name)return;
 const pr=await askText((findPrice(en)!=null?'Roblox lists this game at R$ '+findPrice(en)+' per month. ':'')+'Price in Robux per month. 0 means free.',String(findPrice(en)??0),{title:'Monthly price',ok:'Next'});if(pr===null)return;const price=Math.max(0,parseInt(pr)||0);
 if(!await ask(`Create "${name}" for ${price?'R$ '+price+' (charged to your Robux balance)':'free'}?\nYour balance: R$ ${S.user.robux??'?'}`,{ok:'Create',title:'Confirm'}))return;
 const r=await guard(rb('https://games.roblox.com/v1/games/vip-servers/'+cur.uid,{name,expectedPrice:price}),'Private server created');if(!r.error)pvLoad(p)}
async function pvManage(s,p){const f=await pvFetch(s);if(f.err)return toast(f.err,9000);const id=f.id,d=f.d;
 const code=d.joinCode||((String(d.link||'').match(/privateServerLinkCode=([\w-]+)/)||[])[1])||s.accessCode||'',perm=d.permissions||{};
 PVM={id,p:p||pOf(s)||(cur&&cur.place)||null,code,name:d.name||s.name||'',sub:d.subscription||{},hasActive:typeof d.active==='boolean',active:d.active!==false,friends:!!perm.friendsAllowed,users:perm.users||[]};pvDraw()}
function pvDraw(){const P=PVM,sub=P.sub,place=P.p;let m=$('#modal');
 if(!m){m=document.createElement('div');m.id='modal';m.style='position:fixed;inset:0;background:#000a;z-index:20;display:flex;align-items:center;justify-content:center';m.onclick=e=>{if(e.target==m)m.remove()};document.body.appendChild(m)}
 const st=[(sub.price!=null?(sub.price?'R$ '+sub.price+'/month':'Free'):''),sub.active===true?'Renewal on':sub.active===false?'Renewal off':'',sub.expired?'expired':'',sub.expiresAt?'until '+String(sub.expiresAt).slice(0,10):''].filter(Boolean).join(' · ');
 const fo=(FR||[]).filter(f=>!P.users.some(u=>u.id==f.id));
 m.innerHTML=`<div class=card style="width:min(440px,94vw);max-height:88vh;overflow:auto;margin:0"><h3 style="margin:0">🔒 ${esc(P.name||'Private server')}</h3><small>${esc(st||'Your private server')}</small>
 <div class=row style="margin:10px 0"><input id=pvnm value="${esc(P.name)}" maxlength=50 style="flex:1"><button class=btn onclick="pvRename()">Rename</button></div>
 <div class=row>${P.code&&place?`<button class="btn play" style="padding:9px 22px" onclick="pvPlayM()">▶ Join</button><button class="btn alt" onclick="pvCopyM()">🔗 Copy link</button><button class="btn alt" onclick="pvSaveM()">💾 Save</button>`:''}<button class="btn alt" onclick="pvNewLink()">🔄 New link</button></div>
 <h3 style="margin:14px 0 4px">Who can join</h3>
 ${P.hasActive?`<label style="display:block;margin:6px 0"><input type=checkbox ${P.active?'checked':''} onchange="pvToggle('link')"> Joining on <small style="display:inline">· off blocks everyone using the link</small></label>`:''}
 <label style="display:block;margin:6px 0"><input type=checkbox ${P.friends?'checked':''} onchange="pvToggle('friends')"> Friends can join <small style="display:inline">· your friends, no link needed</small></label>
 <div class=row style="margin-top:4px"><button class="btn alt sm" onclick="pvLock()">🔐 Lock down (joins off, friends off)</button><button class="btn alt sm" onclick="pvOpen()">🔓 Open up (joins on, friends on)</button></div>
 <h3 style="margin:14px 0 4px">Members (${P.users.length})</h3><small style="margin:0 0 6px">Members can always join, even when friends are off.</small>
 ${P.users.map(u=>`<div class=row style="margin:4px 0"><span style="flex:1">${esc(u.displayName||u.name)} <small style="display:inline">@${esc(u.name||'')}</small></span><button class="btn alt sm" onclick="pvMember(${+u.id},0)">Remove</button></div>`).join('')||'<small>No members yet</small>'}
 <div class=row style="margin-top:8px"><input id=pvu placeholder="Add by username" style="flex:1" onkeydown="if(event.key=='Enter')pvMemberName()"><button class=btn onclick="pvMemberName()">Add</button></div>
 ${fo.length?`<div class=row style="margin-top:6px"><select id=pvf style="flex:1">${fo.map(f=>`<option value=${f.id}>${esc(f.display)} (@${esc(f.name)})</option>`).join('')}</select><button class="btn alt" onclick="pvMember(+$('#pvf').value,1)">Add friend</button></div>`:''}
 ${sub.active!=null?`<div class=row style="margin-top:12px"><button class="btn alt sm" onclick="pvRenew()">${sub.active?'⏸ Turn off renewal':'▶ Turn on renewal'}</button></div>`:''}
 <div class=row style="margin-top:12px">${place?`<button class="btn alt sm" onclick="open_('https://www.roblox.com/games/${+place}')">🌐 Open game on roblox.com</button>`:''}<button class="btn alt sm" onclick="$('#modal').remove()">Close</button></div></div>`}
const pvAgain=r=>{if(r&&r.error)pvDraw();else pvManage({id:PVM.id},PVM.p)};
const pvPatch=(path,b,ok)=>guard(rb('https://games.roblox.com/v1/vip-servers/'+PVM.id+path,b,'PATCH'),ok);
async function pvToggle(k){const on=k=='link'?!PVM.active:!PVM.friends;
 pvAgain(k=='link'?await pvPatch('',{active:on},on?'Joining turned on':'Joining turned off'):await pvPatch('/permissions',{friendsAllowed:on},on?'Friends can join':'Friends can no longer join'))}
async function pvLock(){if(!await ask('Turn off joining and friend access? Only members can still get in.',{ok:'Lock down',danger:1}))return;let r={};if(PVM.hasActive)r=await pvPatch('',{active:false});if(!r.error)r=await pvPatch('/permissions',{friendsAllowed:false},'Server locked down');pvAgain(r)}
async function pvOpen(){let r={};if(PVM.hasActive)r=await pvPatch('',{active:true});if(!r.error)r=await pvPatch('/permissions',{friendsAllowed:true},'Server opened up');pvAgain(r)}
async function pvMember(id,add){if(!id)return;pvAgain(await pvPatch('/permissions',add?{usersToAdd:[id]}:{usersToRemove:[id]},add?'Member added':'Member removed'))}
async function pvMemberName(){const n=$('#pvu').value.trim();if(!n)return;const r=await rb('https://users.roblox.com/v1/usernames/users',{usernames:[n],excludeBannedUsers:true}),u=r.data&&r.data[0];if(!u)return toast(r.error||'User not found');pvMember(u.id,1)}
const pvPlayM=()=>{if(!PVM.code||!PVM.p)return toast('No join code available');$('#modal')&&$('#modal').remove();play(PVM.p,nameOf(PVM.p),'',null,PVM.code)};
const pvCopyM=()=>copy(`https://www.roblox.com/games/${PVM.p}?privateServerLinkCode=${PVM.code}`);
async function pvSaveM(){await save({privs:[...(S.privs||[]).filter(x=>x.code!=PVM.code),{place:+PVM.p,name:nameOf(PVM.p),code:PVM.code,label:PVM.name||'Private server'}]},1);toast('Saved');pvSaved(PVM.p)}
async function pvRename(){const n=$('#pvnm').value.trim();if(!n)return;const r=await guard(rb('https://games.roblox.com/v1/vip-servers/'+PVM.id,{name:n},'PATCH'),'Renamed');if(!r.error){PVM.name=n;PVL.forEach(s=>{if((s.vipServerId||s.id)==PVM.id)s.name=n});pvDraw();tab=='privs'&&go('privs')}}
async function pvNewLink(){if(!await ask('Generate a new link? The old link stops working for everyone who has it.',{ok:'Generate'}))return;
 const r=await guard(rb('https://games.roblox.com/v1/vip-servers/'+PVM.id,{newJoinCode:true},'PATCH'),'New link generated');if(r.error)return;
 const nc=r.joinCode||((String(r.link||'').match(/privateServerLinkCode=([\w-]+)/)||[])[1]);
 if(nc&&PVM.code&&(S.privs||[]).some(x=>x.code==PVM.code))await save({privs:S.privs.map(x=>x.code==PVM.code?{...x,code:nc}:x)},1);
 pvManage({id:PVM.id},PVM.p)}
async function pvRenew(){const on=!PVM.sub.active;if(!await ask(on?'Turn renewal back on? Roblox may charge your Robux balance.':'Turn off renewal? The server will stop when the current period ends.',{ok:on?'Turn on':'Turn off',danger:!on}))return;
 const b={active:on};if(on&&PVM.sub.price!=null)b.price=PVM.sub.price;const r=await guard(rb(`https://games.roblox.com/v1/vip-servers/${PVM.id}/subscription`,b,'PATCH'),on?'Renewal on':'Renewal off');if(!r.error)pvManage({id:PVM.id},PVM.p)}
Object.assign(PAGES,{privs(){const l=S.privs||[],by={};l.forEach((x,i)=>(by[x.place]=by[x.place]||[]).push([x,i]));
 return `<h2>Private servers</h2><div class=card><b>Saved links</b><small>Stored on this PC only. You can also add one from any game page (🔒 Private servers) or by right-clicking a game.</small>
 <div class=row style="margin:8px 0"><input id=pvl placeholder="Paste a private server link…" style="flex:1;min-width:240px"><input id=pvn placeholder="Label (optional)" style="width:170px"><button class=btn onclick="pvAddAny()">＋ Add</button></div>
 ${Object.entries(by).map(([p,a])=>`<h3 style="margin:12px 0 4px">${esc(nameOf(p))}</h3><table>${a.map(([x,i])=>`<tr><td><b>${esc(x.label||'Private server')}</b></td><td style="text-align:right;white-space:nowrap">${pvBtns(i)}</td></tr>`).join('')}</table>`).join('')||'<small>Nothing saved yet</small>'}</div>
 ${S.connected?'<div class=card><b>Servers you own or rent on Roblox</b><div id=pmine>Loading…</div></div>':needConn()}`}});
Object.assign(AFTER,{async privs(){if(!S.connected)return;const r=await rb('https://games.roblox.com/v1/private-servers/my-private-servers'),el=$('#pmine');if(!el)return;if(r.error){el.innerHTML=esc(r.error);return}
 MYP=r.data||(Array.isArray(r)?r:[]);el.innerHTML=MYP.length?'<table>'+MYP.map((s,i)=>`<tr><td><b>${esc(s.name||'Private server')}</b><small id=mg${i} style="margin:0">${(()=>{const q=pOf(s),n=(s.game&&s.game.name)||s.gameName||(q&&nameOf(q)!=String(q)?nameOf(q):'');return n?'🎮 '+esc(n):''})()}</small><small id=mp${i} style="margin:0;color:var(--green)"></small></td><td style="text-align:right;white-space:nowrap"><button class="btn sm" onclick="pvJoinMine(${i})">▶ Join</button><button class="btn alt sm" onclick="pvManage(MYP[${i}],pOf(MYP[${i}]))">⚙ Manage</button></td></tr>`).join('')+'</table>':'<small>You do not own or rent any private servers</small>';MYP.forEach((s,i)=>{const q=pOf(s),e=$('#mg'+i);if(q&&e&&!e.textContent)gName(q).then(n=>{e.textContent='🎮 '+n})});MYP.slice(0,15).forEach(pvMineP)}});
// -- empty-space context menu
function airMenu(e){const sel=String(getSelection()||'').trim(),lj=S.history&&S.history.find(x=>x.job||x.code),it=[];
 if(sel)it.push(['📋 Copy selection',()=>copy(sel)],'-');
 if(tab=='game')it.push(['← Back',()=>go(PT||'games')]);
 it.push(['↻ Refresh',()=>tab=='game'&&cur?openGame(cur.place):go(tab)],['⌨ Quick jump',()=>cmdk()],['🚀 Open Sober',()=>openSober()],'-');
 if(lj)it.push(['↩ Rejoin last server',()=>play(lj.place,lj.name||'',lj.icon||'',lj.job||null,lj.code||null)]);
 if(S.favs.length)it.push(['🎲 Random favorite',()=>rnd()]);
 it.push(['🔒 Private servers',()=>go('privs')],'-',['🎨 Style',()=>go('style')],[S.theme.compact=='1'?'▢ Roomy layout':'▣ Compact layout',()=>save({theme:{compact:S.theme.compact=='1'?'0':'1'}},1).then(()=>go(tab))],['⚙ Sober settings',()=>go('sober')]);
 ctxMenu(e,it)}
// -- bulk friend requests
async function addMany(){const names=[...new Set(($('#addmany').value||'').split(/[\s,;]+/).map(x=>x.replace(/^@/,'').trim()).filter(Boolean))];
 if(!names.length)return toast('Type or paste some usernames first');if(names.length>50)return toast('50 at a time, please');
 if(!await ask('Send friend requests to '+names.length+' '+(names.length==1?'person':'people')+'?',{ok:'Send'}))return;
 const r=await rb('https://users.roblox.com/v1/usernames/users',{usernames:names,excludeBannedUsers:true});if(r.error)return toast(r.error);
 const found=r.data||[];let ok=0;const fail=[];
 for(const u of found){const x=await rb(`https://friends.roblox.com/v1/users/${u.id}/request-friend`,{});if(!x.error)ok++;else{fail.push(u.name+': '+x.error);if(/too many|flood|limit/i.test(x.error))break}await new Promise(z=>setTimeout(z,500))}
 const miss=names.length-found.length;toast('Sent '+ok+(miss?' · '+miss+' not found':'')+(fail.length?' · '+fail.length+' failed ('+fail[0]+')':''),8000);if(!fail.length)$('#addmany').value=''}
async function toggleReq(){await save({opts:{hidereq:!(S.opts&&S.opts.hidereq)}},1);reqs()}
// ---- friend popup, quick-jump palette, shortcuts
function showFriend(id){const f=FR.find(x=>x.id==id);if(!f)return;$('#modal')&&$('#modal').remove();const m=document.createElement('div');m.id='modal';m.style='position:fixed;inset:0;background:#000a;z-index:20;display:flex;align-items:center;justify-content:center';m.onclick=e=>{if(e.target==m)m.remove()};
 const pn=(S.pinned||[]).includes(f.id);
 m.innerHTML=`<div class=card style="min-width:320px;text-align:center;margin:0"><img class=av src="${esc(f.avatar||'')}"><h3 style="margin:8px 0 0">${esc(f.display)}</h3><small>@${esc(f.name)}${f.nick?' · '+esc(f.real||''):''}</small><p>${f.status==2?'🟢 Playing <b>'+esc(f.where||'a game')+'</b>':f.status==1?'🔵 Online':f.status==3?'🟠 In Studio':'⚫ Offline<br><small>'+esc(seenTxt(f))+'</small>'}</p>
 ${f.status==2&&f.place?`<button class="btn play" onclick="joinFriend(${f.id});$('#modal').remove()">Join in Sober</button><button class="btn alt" onclick="$('#modal').remove();openGame(${f.place})">View game</button>`:''}<button class="btn alt" onclick="$('#modal').remove();chatWith(${f.id})">💬 Message</button><button class="btn alt" onclick="$('#modal').remove();PU='${esc(f.name)}';go('profile')">Profile</button><button class="btn alt" onclick="pin(${f.id}).then(()=>{$('#modal')&&$('#modal').remove();const e=$('#fr');e&&(e.innerHTML=frs(FR))})">${pn?'★ Unpin':'☆ Pin'}</button><button class="btn alt" onclick="$('#modal').remove();setNick(${f.id})">✏ Nickname</button><button class="btn alt" onclick="$('#modal').remove()">Close</button></div>`;document.body.appendChild(m)}
let CML=[];
function cmRun(i){const x=CML[i];$('#modal')&&$('#modal').remove();x&&x.f()}
function cmdk(){if($('#modal'))return $('#modal').remove();const m=document.createElement('div');m.id='modal';m.style='position:fixed;inset:0;background:#000a;z-index:20;display:flex;align-items:flex-start;justify-content:center;padding-top:14vh';m.onclick=e=>{if(e.target==m)m.remove()};
 m.innerHTML=`<div class=card style="width:min(560px,92vw);margin:0"><input id=ck2 placeholder="Jump to a tab or game — or paste a Roblox link / ID…" style="width:100%"><div id=cl2 style="margin-top:8px;max-height:50vh;overflow:auto"></div></div>`;document.body.appendChild(m);
 const inp=$('#ck2');const draw=()=>{const q=inp.value.trim().toLowerCase(),out=[],l=parseLink(inp.value);
  if(l)out.push({t:'▶ Open game '+l.place,f:()=>openGame(l.place)});
  Object.entries(TABS).forEach(([k,v])=>{if(!q||v.toLowerCase().includes(q))out.push({t:v,f:()=>go(k)})});
  const seen=new Set();[...hist(),...S.favs].forEach(g=>{if(seen.has(g.place))return;seen.add(g.place);if(q&&String(g.name||'').toLowerCase().includes(q))out.push({t:'🎮 '+g.name,f:()=>openGame(g.place)})});
  if(q&&!l)out.push({t:'🔎 Search Roblox for "'+inp.value.trim()+'"',f:()=>{pendingQ=inp.value.trim();go('games')}});
  CML=out.slice(0,12);$('#cl2').innerHTML=CML.map((x,i)=>`<a class=cmd onclick="cmRun(${i})">${esc(x.t)}</a>`).join('')};
 inp.oninput=draw;inp.onkeydown=e=>{if(e.key=='Enter')cmRun(0)};draw();inp.focus()}
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()=='k'){e.preventDefault();cmdk()}else if(e.key=='Escape'){$('#modal')&&$('#modal').remove()}else if(e.key=='/'&&!/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)){e.preventDefault();$('#ts')&&$('#ts').focus()}});
let BRKN={},LIMD='',DOCR=[];
const loadFriends=async()=>{const l=await api('friends');return Array.isArray(l)?setFriends(l):FR};

// ---- friend nicknames (shown only inside Sober Hub)
async function setNick(id,clear){const f=FR.find(x=>x.id==id);if(!f)return;const N={...(S.nicks||{})};
 if(clear)delete N[id];else{const n=(await askText('Nickname for '+(f.real||f.display)+'. It is only shown inside Sober Hub.',f.nick?f.display:'',{title:'Set nickname',ok:'Save'})||'').trim().slice(0,30);if(!n)return;N[id]=n}
 await save({nicks:N},1);toast(clear?'Nickname removed':'Nickname saved');await loadFriends();load()}

// ---- play later
async function laterToggle(g){if(!g||!g.place)return;const old=S.later||[],l=old.filter(x=>x.place!=g.place),had=l.length!=old.length;if(!had)l.push({place:+g.place,name:g.name||nameOf(g.place),icon:g.icon||''});
 await save({later:l},1);toast(had?'Removed from Play later':'Added to Play later');if(tab=='library'||tab=='home')go(tab)}
const laterDel=p=>laterToggle({place:p});

// ---- server hop: a different public server than your recent ones, with room, not empty, lowest ping first
async function hop(p){toast('Looking for a fresh server…',4000);const [a,b]=await Promise.all([api('servers',{place:p,sort:'Desc'}),api('servers',{place:p,sort:'Asc'})]);
 const all=new Map();[...(a.list||[]),...(b.list||[])].forEach(s=>all.set(s.id,s));if(!all.size)return toast(a.error||b.error||'No public servers found');
 const seen=new Set(S.history.filter(x=>x.place==p&&x.job).slice(0,6).map(x=>x.job)),pool=[...all.values()].filter(s=>!seen.has(s.id));
 let c=pool.filter(s=>s.playing<s.max-1&&s.playing>=2);if(!c.length)c=pool.filter(s=>s.playing<s.max);if(!c.length)return toast('No other server with room right now');
 c.sort((x,y)=>(x.ping??9999)-(y.ping??9999));const s=c[Math.random()*Math.min(3,c.length)|0];
 toast('Hopping to a server with '+s.playing+' player'+(s.playing==1?'':'s')+(s.ping!=null?' · '+s.ping+' ms':''),5000);pl(p,s.id)}

// ---- healthy play: break reminders and a daily limit (checked every 5 s from pollActive)
function remind(title,body){if((S.opts||{}).nsound!='0')chime(true);toast(title+' - '+body,9000);api('dnotify',{title,body,icon:''})}
function breakCheck(){const o=S.opts||{};if(!ACT.length){BRKN={};return}
 if(o.brk=='1'){const bm=Math.max(5,+o.brkmin||60)*60;for(const a of ACT){const n=BRKN[a.place]||bm;if(a.secs>=n){BRKN[a.place]=a.secs+bm;remind('Time for a break','You have been playing '+a.name+' for '+fmt(a.secs)+'. Stretch, drink some water and rest your eyes.')}}}
 const lim=(+o.lim||0)*60;if(lim>0&&LIMD!=lk(new Date())){const t=todaySecs()+ACT.reduce((s,a)=>s+a.secs,0);if(t>=lim){LIMD=lk(new Date());remind('Daily play limit reached','You have played '+fmt(t)+' today. Your limit is '+fmt(lim)+'.')}}}

// ---- health check
async function doctor(b){b.disabled=true;const el=$('#docr');el.innerHTML='Checking…';const r=await api('doctor',{});b.disabled=false;if(!$('#docr'))return;if(r.error){el.textContent=r.error;return}DOCR=r;
 el.innerHTML=`<table>${r.map(x=>`<tr><td style="width:32px">${{ok:'✅',warn:'⚠️',bad:'❌'}[x.state]||'•'}</td><td><b>${esc(x.name)}</b><small style="margin:0">${esc(x.detail)}</small></td></tr>`).join('')}</table><button class="btn alt sm" style="margin-top:8px" onclick="docCopy()">Copy report</button>`}
const docCopy=()=>copy(DOCR.map(x=>({ok:'OK',warn:'WARN',bad:'FAIL'}[x.state]||'?')+' - '+x.name+': '+x.detail).join('\n'));

load().then(()=>{updPoll();setInterval(updPoll,60000);chatWatch();setInterval(chatWatch,45000);pollActive();setInterval(pollActive,5000);watch();setInterval(watch,30000)});
</script></body></html>"""

def install_shortcut():
    """Write a .desktop launcher and icon into ~/.local/share so the hub shows up in the app menu."""
    icons = os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps"); apps = os.path.expanduser("~/.local/share/applications")
    os.makedirs(icons, exist_ok=True); os.makedirs(apps, exist_ok=True)
    open(icons + "/sober-hub.svg", "w").write(ICON)
    open(apps + "/sober-hub.desktop", "w").write(
        "[Desktop Entry]\nType=Application\nName=Sober Hub\nComment=Launcher for Sober\n"
        f'Exec="{sys.executable}" "{os.path.abspath(__file__)}"\nIcon=sober-hub\nStartupWMClass={WMCLASS}\nStartupNotify=false\nCategories=Game;\nTerminal=false\n')
    print("Installed", apps + "/sober-hub.desktop")

if __name__ == "__main__":
    if "--install" in sys.argv: sys.exit(install_shortcut())
    os.makedirs(D, mode=0o700, exist_ok=True)
    want = int(os.environ.pop("SOBER_HUB_PORT", "0") or 0)   # set only when the hub restarts itself after an update
    if not want and "--no-update" not in sys.argv and load()["opts"].get("autoupd", "1") != "0":
        if update_check(startup=True)["state"] == "installed":
            try: restart()   # the new process shows "Sober Hub updated automatically!"
            except Exception: pass
    try: srv = ThreadingHTTPServer(("127.0.0.1", want), H)
    except OSError: srv = ThreadingHTTPServer(("127.0.0.1", 0), H); want = 0
    PORT[0] = srv.server_port
    col_recover()
    threading.Thread(target=_upd_loop, daemon=True).start()
    serving = False
    try:
        import webview   # optional (pip install pywebview): native window without an address bar
    except ImportError:
        webview = None
    if webview and "--browser" not in sys.argv:
        # Match the window class to the shortcut's StartupWMClass so docks group them into a single icon.
        sys.argv[0] = WMCLASS; os.environ["RESOURCE_NAME"] = WMCLASS
        try:
            import gi; gi.require_version("Gdk", "3.0")
            from gi.repository import GLib, Gdk
            GLib.set_prgname(WMCLASS); GLib.set_application_name("Sober Hub"); Gdk.set_program_class(WMCLASS)
        except Exception: pass
        threading.Thread(target=srv.serve_forever, daemon=True).start(); serving = True
        try: webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception: pass
        try:
            WIN[0] = webview.create_window("Sober Hub", f"http://127.0.0.1:{srv.server_port}/", width=1280, height=820, min_size=(900, 600))
            webview.start()
            sys.exit(0)
        except Exception as e:   # pywebview installed but no GTK/Qt backend: carry on in a browser window instead
            print("Native window unavailable (" + str(e) + "), using the browser instead.")
    url = f"http://sober-hub.localhost:{srv.server_port}/"
    print("Sober Hub running at", url, "(install pywebview for a native window)")
    for b in (() if want else ("brave-browser", "brave", "chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "microsoft-edge")):
        if shutil.which(b):
            # Separate browser profile and window class keep this window apart from your normal browser and let it use the shortcut's icon.
            subprocess.Popen([b, f"--app={url}", f"--class={WMCLASS}", f"--name={WMCLASS}", "--user-data-dir=" + D + "/browser",
                              "--no-first-run", "--no-default-browser-check"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); break
    else:
        if not want: webbrowser.open(url)   # on a self-restart the existing window just reloads
    threading.Event().wait() if serving else srv.serve_forever()
