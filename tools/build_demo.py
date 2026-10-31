#!/usr/bin/env python3
"""Builds /demo/: the real OHLCV dashboard, replaying a recorded snapshot.

    python3 tools/build_demo.py [raw_dir] [--app path/to/Grow/index.html] [--date "31 Oct 2026"]

Inputs
  raw_dir   what tools/demo_recorder.js + demo_record_server.py captured
            (default ~/Desktop/ohlcvlabs-demo-raw). Private until cleaned.
  --app     the dashboard page (default ../Grow/index.html).

Output
  demo/index.html        the dashboard, unchanged, with the replay layer
                         (DEMO_SHIM below) injected first in <head>
  demo/data/*.json       one cleaned response per recorded request
  demo/data/sse-*.json   recorded server-sent events, replayed on a loop

Cleaning, before anything is written:
  - Endpoints in HIDE are not shipped at all: real-money and broker data
    (Groww holdings, positions, cash, margin, F&O capital) and anything about
    logins or tokens. The page shows them as "hidden in this demo".
  - Every key that names a credential (token, secret, api key, password,
    PIN, chat id, client id, account, email, phone, TOTP, cookie) has its
    value replaced, at any depth, in every response.
  - A last pass refuses to write a file that still contains anything that
    looks like a JWT, a long hex/base64 secret, a bot token or an email.
The replay layer answers the app's own session check with "locked", so the
real lock screen and intro play; the PIN types itself and the app's own
unlock call is answered "yes". Every write is answered "Demo — nothing was
sent" and never leaves the page.
"""

import argparse
import hashlib
import html
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "demo")
DATA = os.path.join(OUT, "data")

# Not shipped: real money at the broker, and anything about logging in.
HIDE = [
    r"^/api/margin\b", r"^/api/fno/capital\b", r"^/api/fno/positions\b",
    r"^/api/portfolio-analysis\b", r"^/api/portfolio-review",
    r"^/api/holdings\b", r"^/api/positions\b", r"^/api/funds\b", r"^/api/balance\b",
    r"^/api/refresh-token\b", r"^/api/fyers/",
    r"^/api/groww", r"^/api/auth\b", r"^/api/session\b", r"^/api/unlock\b", r"^/api/logout\b",
    r"^/api/telegram/(configure|test)\b", r"^/api/account\b", r"^/api/users?\b", r"^/api/lock-palette\b",
]
HIDE_RE = [re.compile(p) for p in HIDE]

# A key naming a credential or a personal identifier: its value is replaced.
SECRET_KEY = re.compile(
    r"(^|[_\-.])(tokens?|secrets?|api[_\-]?keys?|apikeys?|passwords?|passwd|pwd|pins?|"
    r"chat[_\-]?ids?|client[_\-]?ids?|app[_\-]?ids?|account[_\-]?(id|no|number)?|emails?|phones?|mobiles?|"
    r"totp|cookies?|session[_\-]?ids?|bearer|webhooks?|ucc|pan|login[_\-]?urls?|redirect[_\-]?uris?|auth[_\-]?urls?)([_\-.]|$)", re.I)
HIDDEN = "hidden in demo"

# Anything still looking like a secret after cleaning stops the build.
LEAK = [
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),          # JWT
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b"),                     # Telegram bot token
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), # email
    re.compile(r"\b[a-f0-9]{40,}\b"),                                     # long hex
    re.compile(r"client_id=[A-Za-z0-9]"),                                  # an app's OAuth id in a link
]


def hidden(path):
    return any(r.search(path) for r in HIDE_RE)


def scrub(v, key=""):
    if key and SECRET_KEY.search(key) and not isinstance(v, (dict, list)):
        return HIDDEN if v not in (None, "", False, 0) else v
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            # A config row: {"key": "telegram.bot_token", "value": "..."}, or
            # one the app itself marks sensitive.
            if k == "value" and (v.get("sensitive") is True
                                 or (isinstance(v.get("key"), str) and SECRET_KEY.search(v["key"]))):
                out[k] = HIDDEN if x not in (None, "") else x
            else:
                out[k] = scrub(x, k)
        return out
    if isinstance(v, list):
        return [scrub(x, key) for x in v]
    return v


# Query parameters that only bust caches; ignored when matching a request.
CACHE_BUST = {"_", "t", "ts", "cb", "nocache", "v"}


def norm(url):
    path, _, q = url.partition("?")
    keep = []
    for part in filter(None, q.split("&")):
        k, _, val = part.partition("=")
        if k in CACHE_BUST or re.fullmatch(r"\d{12,14}", val or ""):
            continue
        keep.append(part)
    return path + ("?" + "&".join(sorted(keep)) if keep else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", nargs="?", default=os.path.expanduser("~/Desktop/ohlcvlabs-demo-raw"))
    ap.add_argument("--app", default=os.path.join(ROOT, "..", "Grow", "index.html"))
    ap.add_argument("--date", default=date.today().strftime("%-d %b %Y"))
    a = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    for f in os.listdir(DATA):
        os.remove(os.path.join(DATA, f))

    manifest, dropped, leaks = {}, [], []
    api_dir = os.path.join(a.raw, "api")
    for name in sorted(os.listdir(api_dir)):
        with open(os.path.join(api_dir, name), encoding="utf-8") as fh:
            rec = json.load(fh)
        url = rec["url"]
        path = url.split("?")[0]
        if hidden(path) or rec.get("status", 200) >= 500:
            dropped.append(url)
            continue
        body = rec.get("body", "")
        try:
            body = json.dumps(scrub(json.loads(body)), ensure_ascii=False, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError):
            pass
        hits = [r.pattern for r in LEAK if r.search(body)]
        if hits:
            leaks.append((url, hits))
            continue
        fname = hashlib.sha1(norm(url).encode()).hexdigest()[:16] + ".json"
        with open(os.path.join(DATA, fname), "w", encoding="utf-8") as fh:
            fh.write(body)
        manifest[norm(url)] = [fname, rec.get("status", 200)]

    # Server-sent events: grouped per stream, at most the first 90s.
    streams = {}
    sse = os.path.join(a.raw, "sse.jsonl")
    if os.path.exists(sse):
        for line in open(sse, encoding="utf-8"):
            ev = json.loads(line)
            if ev["t"] > 90000:
                continue
            data = ev.get("data") or ""
            try:
                data = json.dumps(scrub(json.loads(data)), ensure_ascii=False, separators=(",", ":"))
            except (json.JSONDecodeError, TypeError):
                pass
            if any(r.search(data) for r in LEAK):
                continue
            streams.setdefault(ev["path"], []).append([ev["t"], ev["type"], data])
    sse_files = {}
    for path, evs in streams.items():
        fname = "sse-" + re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") + ".json"
        with open(os.path.join(DATA, fname), "w", encoding="utf-8") as fh:
            json.dump(sorted(evs), fh, ensure_ascii=False, separators=(",", ":"))
        sse_files[path] = fname

    with open(a.app, encoding="utf-8") as fh:
        app = fh.read()
    shim = (DEMO_SHIM.replace("__MANIFEST__", json.dumps(manifest, separators=(",", ":")))
            .replace("__SSE__", json.dumps(sse_files))
            .replace("__DATE__", html.escape(a.date))
            .replace("__CACHE_BUST__", json.dumps(sorted(CACHE_BUST))))
    page, n = re.subn(r"<head[^>]*>", lambda m: m.group(0) + "\n" + shim, app, count=1)
    if not n:
        sys.exit("build_demo: no <head> in the app page")
    page = page.replace("<title>", '<meta name="robots" content="noindex">\n<title>', 1)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)

    size = sum(os.path.getsize(os.path.join(DATA, f)) for f in os.listdir(DATA))
    print("demo: %d responses (%.1f MB), %d streams; %d hidden/failed, %d refused for possible secrets"
          % (len(manifest), size / 1e6, len(sse_files), len(dropped), len(leaks)))
    for url, hits in leaks:
        print("  REFUSED (looks like a secret): %s  %s" % (url, hits))


DEMO_SHIM = r"""<script>
/* OHLCV DEMO — generated by tools/build_demo.py. The dashboard below is the
   real app, unchanged; this layer answers its requests from a recorded
   snapshot. Nothing leaves the page: writes are refused here. */
(function () {
  var M = __MANIFEST__, SSE = __SSE__, BUST = __CACHE_BUST__, DATA = '/demo/data/';
  var realFetch = window.fetch.bind(window);
  var unlocked = false;
  function json(obj, status) {
    return new Response(JSON.stringify(obj), { status: status || 200, headers: { 'Content-Type': 'application/json' } });
  }
  function norm(url) {
    var keep = [];
    url.searchParams.forEach(function (v, k) {
      if (BUST.indexOf(k) < 0 && !/^\d{12,14}$/.test(v)) keep.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    });
    return url.pathname + (keep.length ? '?' + keep.sort().join('&') : '');
  }
  function find(url) {
    var k = norm(url);
    if (M[k]) return M[k];
    // Same endpoint, query in a different order or form: first recorded one.
    for (var key in M) if (key.split('?')[0] === url.pathname) return M[key];
    return null;
  }
  window.fetch = function (input, init) {
    var url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin !== location.origin || url.pathname.indexOf('/api/') !== 0) return realFetch(input, init);
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (url.pathname === '/api/session') return Promise.resolve(json({ authenticated: unlocked }));
    if (url.pathname === '/api/unlock') { unlocked = true; return Promise.resolve(json({ success: true })); }
    // Real cash at the broker is not shipped. The header shows "Connected"
    // only when this call succeeds, and at snapshot time it did, so it
    // succeeds here with no figures; the margin box says so (see below).
    if (url.pathname === '/api/margin') return Promise.resolve(json({ demo_hidden: true }));
    if (method !== 'GET' && method !== 'HEAD') return Promise.resolve(json({ success: false, error: 'Demo — nothing was sent' }, 403));
    var hit = find(url);
    if (!hit) { (window.__demoMisses = window.__demoMisses || []).push(url.pathname + url.search); return Promise.resolve(json({ success: false, error: 'Hidden in this demo' }, 404)); }
    return realFetch(DATA + hit[0]).then(function (r) { return r.text(); }).then(function (t) {
      return new Response(t, { status: hit[1], headers: { 'Content-Type': 'application/json' } });
    });
  };
  navigator.sendBeacon = function () { return true; };

  // Server-sent events: the recording replayed on a loop, with its timing.
  function DemoES(u) {
    var es = this, path = new URL(u, location.href).pathname, timers = [], ls = {};
    es.url = u; es.readyState = 0; es.onopen = es.onmessage = es.onerror = null;
    es.addEventListener = function (t, f) { (ls[t] = ls[t] || []).push(f); };
    es.removeEventListener = function (t, f) { ls[t] = (ls[t] || []).filter(function (g) { return g !== f; }); };
    es.close = function () { es.readyState = 2; timers.forEach(clearTimeout); };
    function fire(t, data) {
      var e = new MessageEvent(t, { data: data });
      if (t === 'message' && es.onmessage) es.onmessage(e);
      (ls[t] || []).forEach(function (f) { f.call(es, e); });
    }
    setTimeout(function () {
      es.readyState = 1;
      if (es.onopen) es.onopen(new Event('open'));
      (ls.open || []).forEach(function (f) { f.call(es, new Event('open')); });
      if (!SSE[path]) return;
      realFetch(DATA + SSE[path]).then(function (r) { return r.json(); }).then(function (evs) {
        if (!evs.length) return;
        var span = evs[evs.length - 1][0] + 2000;
        (function loop() {
          if (es.readyState === 2) return;
          evs.forEach(function (ev) { timers.push(setTimeout(function () { if (es.readyState === 1) fire(ev[1], ev[2]); }, ev[0])); });
          timers.push(setTimeout(loop, span));
        })();
      });
    }, 50);
  }
  DemoES.CONNECTING = 0; DemoES.OPEN = 1; DemoES.CLOSED = 2;
  window.EventSource = DemoES;

  // The PIN types itself once the app's real intro has landed on the
  // keypad, through the app's own pinKey(); any PIN a visitor types works too.
  function autotype() {
    var lock = document.getElementById('pin-lock');
    if (!lock) return;
    var done = false;
    new MutationObserver(function () {
      var ready = !lock.classList.contains('hidden') && !lock.classList.contains('staging') && lock.classList.contains('shot-full');
      if (!ready || done || unlocked) return;
      done = true;
      var t = 600;
      ['2', '7', '0', '4'].forEach(function (d) {
        setTimeout(function () { if (!unlocked && typeof pinKey === 'function') pinKey(d); }, t);
        t += 280 + Math.round(Math.random() * 120);
      });
    }).observe(lock, { attributes: true, attributeFilter: ['class'] });
  }

  function banner() {
    var b = document.createElement('div');
    b.setAttribute('role', 'note');
    b.innerHTML = '<span>Demo · snapshot of __DATE__ · paper trading · not investment advice</span>'
      + '<a href="/" title="Back to the OHLCV Labs home page">Exit demo</a>';
    b.style.cssText = 'position:fixed;left:50%;bottom:14px;transform:translateX(-50%);'
      + 'z-index:9998;display:flex;align-items:center;gap:14px;padding:9px 10px 9px 16px;border-radius:999px;'
      + 'background:rgba(17,16,20,.92);color:#f1e8db;font:600 11px/1 -apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;'
      + 'letter-spacing:.08em;text-transform:uppercase;white-space:nowrap;box-shadow:0 8px 30px rgba(0,0,0,.25);max-width:calc(100vw - 24px);';
    var a = b.querySelector('a');
    a.style.cssText = 'color:#111014;background:#f1e8db;text-decoration:none;padding:7px 12px;border-radius:999px;';
    var s = b.querySelector('span');
    s.style.cssText = 'overflow:hidden;text-overflow:ellipsis;';
    // Room at the end of the page, so the last line can scroll clear of it.
    var room = document.createElement('div');
    room.setAttribute('aria-hidden', 'true');
    room.style.height = '64px';
    document.body.appendChild(room);
    document.body.appendChild(b);
    // On phones the app has a fixed tab bar along the bottom; the banner sits
    // above whatever is fixed there instead of covering it.
    function place() {
      b.style.visibility = 'hidden';
      var el = document.elementFromPoint(innerWidth / 2, innerHeight - 3), top = innerHeight;
      for (; el && el !== document.body; el = el.parentElement) {
        if (getComputedStyle(el).position === 'fixed') { top = el.getBoundingClientRect().top; break; }
      }
      b.style.visibility = '';
      b.style.bottom = (top < innerHeight - 1 ? innerHeight - top + 10 : 14) + 'px';
    }
    addEventListener('resize', place);
    [300, 1500, 4000, 8000, 12000].forEach(function (t) { setTimeout(place, t); });
    document.addEventListener('click', function () { setTimeout(place, 400); }, true);
  }
  // Figures that would be real money read "Hidden in demo", never a made-up 0.
  function hideMoney() {
    ['stat-margin'].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      var set = function () { if (el.textContent !== 'Hidden in demo') el.textContent = 'Hidden in demo'; };
      set();
      new MutationObserver(set).observe(el, { childList: true, characterData: true, subtree: true });
    });
  }
  // P&L tab, demo only: the app's card counts winners / losers after
  // charges. The story in this data is the charges, so the demo leads with
  // it: how many trades made money before costs, and what the costs did.
  // Computed from the app's own figures (cachedPnLData) every time the card
  // updates, so the "Last N trades" filter keeps it right.
  function costStory() {
    var el = document.getElementById('pnl-trades');
    if (!el) return;
    var label = el.nextElementSibling, grid = el.closest('.grid'), note;
    function money(v) { return '\u20b9' + Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
    function apply() {
      var d = typeof cachedPnLData !== 'undefined' && cachedPnLData;
      if (!d || !d.pnl_data || !d.pnl_data.length || !d.statistics) return;
      var rows = d.pnl_data, n = rows.length;
      var gross = rows.filter(function (r) { return r.gross_pnl > 0; }).length;
      var lost = rows.filter(function (r) { return r.gross_pnl > 0 && r.net_pnl < 0; }).length;
      var text = gross + ' / ' + n;
      if (el.textContent !== text) el.textContent = text;
      el.style.color = 'var(--green)';
      if (label) label.textContent = 'Profitable before costs';
      if (!note) {
        note = document.createElement('p');
        note.style.cssText = 'margin:12px 2px 0;font-size:13px;line-height:1.5;color:var(--text)';
        grid.parentNode.insertBefore(note, grid.nextSibling);
      }
      note.innerHTML = '<strong>' + (gross === n ? 'All ' + n : gross + ' of ' + n) + ' trades made money before costs.</strong> '
        + 'Charges of ' + money(d.statistics.total_charges) + ' turned ' + lost + ' of them into net losses.';
    }
    new MutationObserver(apply).observe(el, { childList: true, characterData: true, subtree: true });
    apply();
  }
  document.addEventListener('DOMContentLoaded', function () { autotype(); banner(); hideMoney(); costStory(); });
})();
</script>"""

if __name__ == "__main__":
    main()
