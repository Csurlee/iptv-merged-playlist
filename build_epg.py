#!/usr/bin/env python3
"""Build a trimmed XMLTV guide that actually matches playlist.m3u.

Sources (priority order): free-epg.de per-country files, then epgshare01 ALL_SOURCES.
Each playlist channel is matched to a source channel (exact id, id without @feed,
normalised id, normalised name within the same country). Matched playlist rows get
their tvg-id rewritten to the guide's channel id; the guide only contains matched
channels and programmes in a rolling window. Standard library only.

usage: build_epg.py [--cache DIR] [--out DIR] [--days N] [--countries de,hu] [--skip-epgshare]
"""
import argparse, collections, datetime as dt, gzip, io, os, re, sys, time
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET

UA = "iptv-merged-playlist-epg-builder (+https://github.com/Csurlee/iptv-merged-playlist)"
FREE_EPG = "https://free-epg.de"
EPGSHARE = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"
EUROPE_GROUPS = {
    "Albania", "Andorra", "Austria", "Belarus", "Belgium", "Bosnia and Herzegovina", "Bulgaria", "Croatia",
    "Cyprus", "Czech Republic", "Denmark", "Estonia", "Faroe Islands", "Finland", "France", "Germany", "Greece",
    "Holy See (Vatican City State)", "Hungary", "Iceland", "Ireland", "Italy", "Kosovo", "Latvia", "Lithuania",
    "Luxembourg", "Malta", "Moldova", "Monaco", "Montenegro", "Netherlands", "North Macedonia", "Norway", "Poland",
    "Portugal", "Romania", "Russia", "San Marino", "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden",
    "Switzerland", "Turkey", "Ukraine", "United Kingdom",
}
EUROPE_CODES = {
    "al", "ad", "at", "by", "be", "ba", "bg", "hr", "cy", "cz", "dk", "ee", "fo", "fi", "fr", "de", "gr", "va", "hu",
    "is", "ie", "it", "xk", "lv", "lt", "lu", "mt", "md", "mc", "me", "nl", "mk", "no", "pl", "pt", "ro", "ru", "sm",
    "rs", "sk", "si", "es", "se", "ch", "tr", "ua", "gb", "uk",
}
GUIDE_URL = "https://github.com/Csurlee/iptv-merged-playlist/releases/download/epg/guide.xml.gz"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fetch(url, dest, tries=20):
    """Download url -> dest, honouring 503/429 retry-after. Returns True on success."""
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r, open(dest + ".part", "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            os.replace(dest + ".part", dest)
            return True
        except urllib.error.HTTPError as e:
            wait = int(e.headers.get("retry-after") or 30) if e.code in (429, 502, 503, 504) else 0
            log(f"  {url}: HTTP {e.code}" + (f", retry in {wait}s" if wait else ""))
            if not wait:
                return False
            time.sleep(min(wait, 180))
        except Exception as e:  # network hiccup
            log(f"  {url}: {e!r}, retry")
            time.sleep(10)
    return False


def open_xml(path):
    with open(path, "rb") as f:
        magic = f.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def norm(s):
    s = s.lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s)
    s = re.sub(r"\b(hd|sd|fhd|uhd|4k|tv|channel)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def cc_of(cid):
    m = re.search(r"\.([a-z]{2,3})(?:@|$)", cid.lower())
    return m.group(1) if m else ""


def base_of(cid):
    return cid.split("@")[0]


def parse_time(s):
    try:
        s = s.strip()
        if len(s) <= 14:
            return dt.datetime.strptime(s[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
        return dt.datetime.strptime(s, "%Y%m%d%H%M%S %z")
    except Exception:
        return None


class Source:
    def __init__(self, name, path):
        self.name, self.path = name, path
        self.channels = {}                    # id -> element
        self.with_prog = set()
        self.by_id_lower = {}
        self.by_norm_id = collections.defaultdict(list)   # (cc, norm(base)) -> ids
        self.by_name = collections.defaultdict(list)      # (cc, norm(name)) -> ids

    def scan(self):
        root = None
        for ev, el in ET.iterparse(open_xml(self.path), events=("start", "end")):
            if root is None:
                root = el
            if ev != "end":
                continue
            if el.tag == "channel":
                self.channels[el.get("id")] = el      # kept: needed for output
            elif el.tag == "programme":
                self.with_prog.add(el.get("channel"))
                root.clear()                          # drop finished programmes (channels stay referenced)
        for cid, el in self.channels.items():
            if cid not in self.with_prog:
                continue
            cc = cc_of(cid)
            self.by_id_lower[cid.lower()] = cid
            self.by_norm_id[(cc, norm(re.sub(r"\.[a-z]{2,3}$", "", base_of(cid))))].append(cid)
            for dn in el.findall("display-name"):
                t = re.sub(r"^[A-Za-z]{2,3}\s*-\s*", "", (dn.text or "").strip())
                if norm(t):
                    self.by_name[(cc, norm(t))].append(cid)

    def match(self, pid, name, cc):
        if pid:
            for cand in (pid, base_of(pid)):
                if cand in self.with_prog:
                    return cand
            lo = self.by_id_lower.get(base_of(pid).lower())
            if lo:
                return lo
            hit = self.by_norm_id.get((cc_of(pid) or cc, norm(re.sub(r"\.[a-z]{2,3}$", "", base_of(pid)))))
            if hit:
                return hit[0]
        n = norm(name)
        if n:
            hit = self.by_name.get((cc, n)) or self.by_norm_id.get((cc, n))
            if hit:
                return hit[0]
        return None


def read_playlist(path):
    lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
    rows = []   # (line_index, id, group, name)
    for i, l in enumerate(lines):
        if l.startswith("#EXTINF"):
            m = re.search(r'tvg-id="([^"]*)"', l)
            g = re.search(r'group-title="([^"]*)"', l)
            rows.append((i, m.group(1) if m else "", g.group(1) if g else "", l.rsplit(",", 1)[-1].strip()))
    return lines, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--playlist", default="playlist.m3u")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--out", default="out")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--countries", default="")
    ap.add_argument("--skip-epgshare", action="store_true")
    ap.add_argument("--region", choices=["all", "europe"], default="all",
                    help="europe: only European countries (guides downloaded, playlist entries kept)")
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)

    # ---- download sources
    req = urllib.request.Request(FREE_EPG + "/countries", headers={"User-Agent": UA})
    page = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    codes = sorted(set(re.findall(r"/api/epg/([a-z0-9_-]+)\.xml\.gz", page)))
    if a.countries:
        codes = [c for c in codes if c in a.countries.split(",")]
    if a.region == "europe":
        codes = [c for c in codes if c in EUROPE_CODES]
    sources, failed = [], []

    def get_country(c):
        dest = os.path.join(a.cache, f"free-epg_{c}.xml")
        if os.path.exists(dest) and time.time() - os.path.getmtime(dest) < 12 * 3600:
            return c, dest, True          # fresh enough (re-runs / local testing)
        log(f"free-epg {c}")
        return c, dest, fetch(f"{FREE_EPG}/api/epg/{c}.xml.gz", dest)

    # the server builds each file on demand (first hit often 503 + retry-after), so run a few in parallel
    with ThreadPoolExecutor(max_workers=6) as ex:
        for c, dest, ok in ex.map(get_country, codes):
            if ok:
                sources.append(("free-epg", c, dest))
            else:
                failed.append(c)
    if not a.skip_epgshare:
        dest = os.path.join(a.cache, "epgshare01_all.xml.gz")
        log("epgshare01 ALL")
        if fetch(EPGSHARE, dest):
            sources.append(("epgshare01", "all", dest))
        else:
            failed.append("epgshare01")
    if failed:
        log("FAILED sources:", failed)
    if not sources:
        sys.exit("no sources downloaded")

    # ---- scan
    srcs = []
    for kind, c, path in sources:
        s = Source(f"{kind}:{c}", path)
        log("scan", s.name)
        s.scan()
        srcs.append(s)

    # ---- match
    lines, rows = read_playlist(a.playlist)
    if a.region == "europe":
        rows = [r for r in rows if r[2] in EUROPE_GROUPS]
    ccs = collections.defaultdict(collections.Counter)
    for _, pid, g, _ in rows:
        if pid and cc_of(pid):
            ccs[g][cc_of(pid)] += 1
    group_cc = {g: c.most_common(1)[0][0] for g, c in ccs.items()}
    assign = {}       # (src_index, guide_id) used set
    used = {}         # line index -> (src, guide_id)
    cache_by_key = {}
    for i, pid, g, name in rows:
        key = (pid, norm(name), g)
        if key in cache_by_key:
            res = cache_by_key[key]
        else:
            res = None
            cc = (cc_of(pid) if pid else "") or group_cc.get(g, "")
            for s in srcs:
                gid = s.match(pid, name, cc)
                if gid:
                    res = (s, gid)
                    break
            cache_by_key[key] = res
        if res:
            used[i] = res

    # ---- write guide (first source wins per guide id)
    now = dt.datetime.now(dt.timezone.utc)
    lo, hi = now - dt.timedelta(hours=6), now + dt.timedelta(days=a.days)
    wanted = collections.defaultdict(set)   # source -> guide ids
    owner = {}                              # guide id -> source name
    for s, gid in used.values():
        if gid not in owner:
            owner[gid] = s.name
        if owner[gid] == s.name:
            wanted[s.name].add(gid)
    guide_path = os.path.join(a.out, "guide.xml.gz")
    n_prog = 0
    with gzip.open(guide_path, "wb", compresslevel=9) as out:
        out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="Csurlee/iptv-merged-playlist">\n')
        for s in srcs:
            for gid in sorted(wanted.get(s.name, ())):
                out.write(ET.tostring(s.channels[gid], encoding="utf-8") .rstrip() + b"\n")
        for s in srcs:
            ids = wanted.get(s.name)
            if not ids:
                continue
            log("write", s.name, len(ids), "channels")
            root = None
            for ev, el in ET.iterparse(open_xml(s.path), events=("start", "end")):
                if root is None:
                    root = el
                if ev != "end" or el.tag != "programme":
                    continue
                if el.get("channel") in ids:
                    st = parse_time(el.get("start", ""))
                    en = parse_time(el.get("stop", "")) or st
                    if st and en and en >= lo and st <= hi:
                        out.write(ET.tostring(el, encoding="utf-8").rstrip() + b"\n")
                        n_prog += 1
                root.clear()
        out.write(b"</tv>\n")

    # ---- playlist + report
    new = list(lines)
    changed = 0
    for i, (s, gid) in used.items():
        l = new[i]
        if 'tvg-id="' in l:
            nl = re.sub(r'tvg-id="[^"]*"', f'tvg-id="{gid}"', l, count=1)
        else:
            nl = l.replace("#EXTINF:-1", f'#EXTINF:-1 tvg-id="{gid}"', 1)
        changed += nl != l
        new[i] = nl
    head = re.sub(r'url-tvg="[^"]*"', f'url-tvg="{GUIDE_URL}"', new[0]) if 'url-tvg="' in new[0] \
        else new[0].replace("#EXTM3U", f'#EXTM3U url-tvg="{GUIDE_URL}"', 1)
    new[0] = head
    if a.region == "europe":      # keep header + only the entries (EXTINF block up to the next one) of kept rows
        starts = [i for i, l in enumerate(lines) if l.startswith("#EXTINF")] + [len(lines)]
        keep = {r[0] for r in rows}
        kept = [new[0]]
        for k, st in enumerate(starts[:-1]):
            if st in keep:
                kept.extend(new[st:starts[k + 1]])
        new = kept
    with open(os.path.join(a.out, "playlist.m3u"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(new))

    per = collections.OrderedDict()
    for i, pid, g, name in rows:
        d = per.setdefault(g, [0, 0])
        d[0] += 1
        d[1] += i in used
    with open(os.path.join(a.out, "coverage.md"), "w", encoding="utf-8") as f:
        tot = sum(v[0] for v in per.values()); got = sum(v[1] for v in per.values())
        f.write(f"# EPG coverage\n\nBuilt {now:%Y-%m-%d %H:%M} UTC. **{got} of {tot} playlist entries ({100*got/tot:.1f}%)** "
                f"have guide data; {len(owner)} guide channels, {n_prog} programmes, {a.days} days ahead.\n\n"
                "| Country | Entries | With guide | % |\n|---|---:|---:|---:|\n")
        for g, (t, m) in sorted(per.items()):
            f.write(f"| {g} | {t} | {m} | {100*m/t:.0f}% |\n")
        if failed:
            f.write(f"\nSources that failed to download this run: {', '.join(failed)}\n")
    log(f"DONE: {got}/{tot} entries matched ({100*got/tot:.1f}%), {len(owner)} guide channels, "
        f"{n_prog} programmes, {changed} tvg-ids rewritten, guide {os.path.getsize(guide_path)/1e6:.1f} MB")


if __name__ == "__main__":
    main()
