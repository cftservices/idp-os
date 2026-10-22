"""Smoke-test every panel query of the factory dashboards.

TDengine targets go through Grafana's /api/ds/query (backend plugin). The JSON datasource is
frontend-only (no backend binary), so those targets are replayed straight against the batch
engine with a small JSONPath evaluator covering the subset the generator emits.

    docker exec -i -e GF_PASS=... vla-batch-engine python - < validate_dashboards.py
Exit code 1 when any panel errors or is unexpectedly empty.
"""
import itertools, json, os, re, sys, urllib.parse, urllib.request as u

GF = os.getenv("GF_URL", "http://grafana:3000/grafana")  # served from sub path
API = os.getenv("API_URL", "http://localhost:8000/api/v1")
BOARDS = ["fabriek-overzicht", "fabriek-lijn", "fabriek-voorbereiding", "fabriek-oee",
          "fabriek-logistiek", "fabriek-onderhoud", "fabriek-historian"]
# state-dependent, may legitimately be empty
MAY_BE_EMPTY = {"Open werkorders", "Afgekeurd", "Open alarmen", "Afwegen", "Premix", "Gereed", "Verbruikt"}  # noValue "0"

# basic auth is disabled on purpose (hardening), so log in for a session cookie
_login = u.urlopen(u.Request(GF + "/login", data=json.dumps({"user": "admin", "password": os.environ["GF_PASS"]}).encode(),
                             headers={"Content-Type": "application/json"}), timeout=30)
COOKIE = "; ".join(c.split(";")[0] for c in _login.headers.get_all("Set-Cookie") or [])


def gf(path, body=None):
    req = u.Request(GF + path, data=json.dumps(body).encode() if body else None,
                    headers={"Cookie": COOKIE, "Content-Type": "application/json"})
    try:
        return json.load(u.urlopen(req, timeout=60))
    except u.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read().decode()[:300]}"}


def jpath(doc, path):
    """$.a.b | $[*].x | $.a[*].b.c | $.a[?(@.k=='v')].f  ->  list of matches"""
    cur = [doc]
    for tok in re.findall(r"\[\?\(@\.\w+=='[^']*'\)\]|\[\*\]|\.\w+", path[1:]):
        nxt = []
        for c in cur:
            if tok == "[*]":
                nxt += c if isinstance(c, list) else []
            elif tok.startswith("[?"):
                k, v = re.match(r"\[\?\(@\.(\w+)=='([^']*)'\)\]", tok).groups()
                nxt += [x for x in (c if isinstance(c, list) else []) if isinstance(x, dict) and str(x.get(k)) == v]
            elif isinstance(c, dict) and tok[1:] in c:
                nxt.append(c[tok[1:]])
        cur = nxt
    return cur


def jsonata(doc, expr):
    """Subset the generator emits: $count(A[k='v']) | A[k='v'].f | $map(A, function($a){ $a.f })"""
    m = re.fullmatch(r"\$count\((\w+)\[(\w+)='([^']*)'\]\)", expr)
    if m:
        a, k, v = m.groups()
        return [sum(1 for x in doc.get(a, []) if str(x.get(k)) == v)]
    m = re.fullmatch(r"(\w+)\[(\w+)='([^']*)'\]\.(\w+)", expr)
    if m:
        a, k, v, f = m.groups()
        return [x[f] for x in doc.get(a, []) if str(x.get(k)) == v and f in x]
    m = re.fullmatch(r"\$map\((.+), function\(\$a\)\{ \$a\.(\w+) \}\)", expr)
    if m:
        arr = jpath(doc, "$" + ("" if m.group(1) == "$" else "." + m.group(1)) + "[*]")
        return [x.get(m.group(2)) for x in arr]
    raise ValueError(f"jsonata niet ondersteund in validator: {expr}")


def run_json(t):
    if t.get("queryParams"):
        return None, "queryParams wordt door de plugin genegeerd, gebruik params"
    # mirror the plugin: only `params` pairs reach the URL
    qs = urllib.parse.urlencode([tuple(kv) for kv in t.get("params") or []])
    url = API + t["urlPath"] + ("?" + qs if qs else "")
    try:
        doc = json.load(u.urlopen(url, timeout=60))
    except Exception as e:
        return None, f"{url}: {e}"
    if any("?(" in f["jsonPath"] for f in t["fields"]):
        return None, "JSONPath-filter [?()] wordt door de plugin geweigerd, gebruik jsonata"
    counts = {f["jsonPath"]: len(jsonata(doc, f["jsonPath"]) if f.get("language") == "jsonata"
                                 else jpath(doc, f["jsonPath"])) for f in t["fields"]}
    rows = max(counts.values()) if counts else 0
    missing = [p for p, n in counts.items() if n == 0]
    if missing and rows:
        return rows, f"leeg pad: {missing}"
    if len(set(counts.values())) > 1:
        # the plugin refuses a frame whose fields differ in length ("Fields have different lengths")
        return rows, f"ongelijke lengtes: {counts}"
    return rows, None


def run_td(t, rng):
    r = gf("/api/ds/query", {"from": rng, "to": "now", "queries": [t]})
    if "_error" in r:
        return None, r["_error"]
    res = r["results"][t["refId"]]
    if res.get("error"):
        return None, res["error"][:250]
    return sum(len(f["data"]["values"][0]) if f["data"]["values"] else 0 for f in res.get("frames", [])), None


def subst(s, var):
    s = re.sub(r"\$\{(\w+):text\}", lambda m: var[m.group(1)][0], s)
    s = re.sub(r"\$\{(\w+)\}", lambda m: var[m.group(1)][1], s)
    return re.sub(r"\$(\w+)", lambda m: var[m.group(1)][1] if m.group(1) in var else m.group(0), s)


orders = json.load(u.urlopen(API + "/orders", timeout=30))
order = sorted(o["order_id"] for o in orders)[-1]
fail = 0
for uid in BOARDS:
    d = gf(f"/api/dashboards/uid/{uid}")
    if "_error" in d:
        print(f"!! {uid}: niet geladen {d['_error']}"); fail += 1; continue
    dash = d["dashboard"]
    choices = []
    for v in dash["templating"]["list"]:
        opts = [(o["text"], o["value"]) for o in v.get("options") or []] if v["name"] == "lijn" else []
        cur = v.get("current") or {}
        choices.append([(v["name"], o) for o in opts] or [(v["name"], (cur.get("text", ""), cur.get("value", "")))])
    for combo in itertools.product(*choices) if choices else [()]:
        var = dict(combo)
        if "order" in var:
            var["order"] = (order, order)
        print(f"== {uid} {({k: v[1] for k, v in var.items()})}")
        for p in dash["panels"]:
            if not p.get("targets"):
                continue
            # a panel is empty only when all its targets are (a timeline may lack one area)
            out, errs = [], []
            for t in p["targets"]:
                t = json.loads(subst(json.dumps(t), var))
                rows, err = (run_json(t) if "urlPath" in t else run_td(t, dash["time"]["from"]))
                out.append(f"{t['refId']}:{rows}")
                if err:
                    errs.append(f"{t['refId']}: {err}")
            empty = not any(o.split(":")[1] not in ("0", "None") for o in out)
            bad = bool(errs) or (empty and p["title"] not in MAY_BE_EMPTY)
            fail += bad
            print(f"  {'FOUT' if bad else 'ok  '} {p['title']}: {' '.join(out)} {' | '.join(errs)}")
print(f"\n{fail} panelen met fout of leeg")
sys.exit(1 if fail else 0)