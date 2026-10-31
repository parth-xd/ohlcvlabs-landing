/* Demo recorder — paste into the console of the UNLOCKED dashboard
   (http://127.0.0.1:8000) with tools/demo_record_server.py running.

   It loads a second copy of the dashboard in a full-window frame, with a hook
   installed before any of the app's own scripts:
     - every GET /api/* response is sent to the record server (latest wins);
     - every non-GET /api/* request is REFUSED and never leaves the page, so
       nothing the walk-through clicks can place an order, toggle a setting or
       write a record. Two GETs that are known to be heavy or to write anyway
       (/api/fno/sync-capital, /api/scan) are refused as well;
     - server-sent events (/api/stream/prices, /api/events) are recorded with
       their timing, for replay.
   Then it walks the dashboard: every tab, every stock's analysis, research
   details, news, journal entries, settings sections, chart periods.

   Progress: window.__demoRec (counts, current step). Nothing here changes the
   dashboard you are looking at; the walk happens in the frame. */
(function () {
  const SAVE = 'http://127.0.0.1:8793';

  // Runs inside the frame before the app. Kept self-contained (it is
  // stringified into the frame's HTML).
  function hook(SAVE) {
    const ofetch = window.fetch.bind(window);
    const DENY = /^\/api\/(fno\/sync-capital|scan)(\/|$)/;
    const rec = window.__rec = { pending: 0, saved: 0, blocked: [], lastActivity: Date.now() };
    const send = (kind, payload) => ofetch(SAVE + '/' + kind, {
      method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: JSON.stringify(payload),
    }).catch(() => {});
    window.fetch = async function (input, init) {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href);
      const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      const isApi = url.origin === location.origin && url.pathname.startsWith('/api/');
      if (!isApi) return ofetch(input, init);
      const key = url.pathname + url.search;
      if ((method !== 'GET' && method !== 'HEAD') || DENY.test(url.pathname)) {
        if (url.pathname !== '/api/session') {
          rec.blocked.push(method + ' ' + key);
          send('blocked', { method, url: key, body: init && typeof init.body === 'string' ? init.body.slice(0, 2000) : null });
          return new Response(JSON.stringify({ success: false, error: 'Blocked by the demo recorder' }),
            { status: 409, headers: { 'Content-Type': 'application/json' } });
        }
      }
      rec.pending++; rec.lastActivity = Date.now();
      try {
        const r = await ofetch(input, init);
        r.clone().text().then(body => {
          send('save', { url: key, status: r.status, ctype: r.headers.get('content-type'), body });
          rec.saved++;
        }).finally(() => { rec.pending--; rec.lastActivity = Date.now(); });
        return r;
      } catch (e) { rec.pending--; rec.lastActivity = Date.now(); throw e; }
    };
    navigator.sendBeacon = () => true;
    const OES = window.EventSource;
    function RecES(u, o) {
      const es = new OES(u, o);
      const path = new URL(u, location.href).pathname, t0 = Date.now(), seen = new Set();
      const orig = es.addEventListener.bind(es);
      const tap = type => { if (seen.has(type)) return; seen.add(type); orig(type, ev => send('sse', { path, type, t: Date.now() - t0, data: ev.data })); };
      tap('message');
      es.addEventListener = (type, fn, opt) => { tap(type); return orig(type, fn, opt); };
      return es;
    }
    RecES.prototype = OES.prototype;
    RecES.CONNECTING = 0; RecES.OPEN = 1; RecES.CLOSED = 2;
    window.EventSource = RecES;
  }

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const status = window.__demoRec = { step: 'starting', log: [] };
  const say = m => { status.step = m; status.log.push(new Date().toISOString().slice(11, 19) + ' ' + m); };

  async function start() {
    const html = await (await fetch('/', { cache: 'no-store' })).text();
    const pre = '<script>(' + hook.toString() + ')(' + JSON.stringify(SAVE) + ')</' + 'script>';
    const withHook = html.replace(/<head[^>]*>/i, m => m + pre);
    const f = document.createElement('iframe');
    f.id = '__demo_frame';
    f.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;border:0;z-index:2147483647;background:#fff';
    f.src = '/api/session';
    document.body.appendChild(f);
    await new Promise(r => f.addEventListener('load', r, { once: true }));
    const d = f.contentDocument;
    d.open(); d.write(withHook); d.close();
    return f.contentWindow;
  }

  // Quiet = no request in flight and none started for `ms`.
  async function idle(w, ms = 2000, max = 120000) {
    const t0 = Date.now();
    while (Date.now() - t0 < max) {
      const r = w.__rec;
      if (r && r.pending === 0 && Date.now() - r.lastActivity > ms) return true;
      await sleep(250);
    }
    return false;
  }
  const ev = (w, src) => { try { return w.eval(src); } catch (e) { return undefined; } };
  const all = (w, sel) => [...w.document.querySelectorAll(sel)];

  async function clickEach(w, sel, label, after, cap = 500) {
    const els = all(w, sel).slice(0, cap);
    for (let i = 0; i < els.length; i++) {
      say(label + ' ' + (i + 1) + '/' + els.length);
      try { els[i].click(); } catch (e) {}
      await idle(w, 1500);
      if (after) { try { after(els[i]); } catch (e) {} await idle(w, 800); }
    }
  }

  async function walk() {
    const w = await start();
    say('waiting for unlock');
    for (let i = 0; i < 240 && ev(w, '_sessionState') !== 'unlocked'; i++) await sleep(250);
    if (ev(w, '_sessionState') !== 'unlocked') { say('NOT UNLOCKED — unlock the dashboard first'); return; }
    await idle(w, 4000, 180000);

    const tabs = all(w, '.tab').map(t => t.dataset.tab);
    for (const tab of tabs) { say('tab ' + tab); w.activateTab(tab); await idle(w, 2500, 180000); }

    w.activateTab('predictions'); await idle(w, 2500);
    await clickEach(w, 'button.wl-view', 'stock analysis', el => el.click());
    await clickEach(w, '[onclick^="setChartPeriod"]', 'chart period');

    w.activateTab('research'); await idle(w, 2500);
    await clickEach(w, '[onclick^="loadResearchDetail"]', 'research detail', () => w.researchBack && w.researchBack());

    w.activateTab('news'); await idle(w, 2500);
    await clickEach(w, '[onclick^="filterWorldNews"]', 'news filter');
    await clickEach(w, '[onclick^="loadStockNewsDetail"]', 'stock news');

    w.activateTab('journal'); await idle(w, 2500);
    await clickEach(w, '[onclick^="toggleJournalDetail"]', 'journal entry', null, 60);

    w.activateTab('fno-backtest'); await idle(w, 2500);
    await clickEach(w, '[onclick^="btSelectSegment"]', 'backtest segment');
    await clickEach(w, '[onclick^="switchDecisionTab"]', 'decision tab');

    w.activateTab('settings'); await idle(w, 2500);
    await clickEach(w, '[onclick^="showSettingsSection"]', 'settings section');

    say('recording live streams (30s)');
    await sleep(30000);
    await idle(w, 2000);
    status.blocked = w.__rec.blocked.slice();
    status.saved = w.__rec.saved;
    say('DONE');
  }

  walk().catch(e => say('ERROR ' + e.message));
})();
