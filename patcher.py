#!/usr/bin/env python3
"""
Primo Portfolio – Patch v4
===========================
Features injected into app.html:
  1. Sort any column  – click header (↕ → ↑/↓)
  2. Day toggle       – $/%  button in the "יום זה" header
  3. Live USD/ILS     – fetched from /api/usdils, badge next to refresh
  4. Portfolio day Σ  – total daily change shown in the summary cards area

Also adds  /api/usdils  endpoint to server.py.

Usage
-----
  Place this script next to app.html (and server.py), then run:
      python3 patch_app.py
  Backup created as app.html.bak on first run.
"""

import os, shutil

# ── The injected <script> ─────────────────────────────────────────────────────
PATCH = r"""
<script>
/* ═══════════════════════════════════════════════════
   Primo Portfolio Feature Patch v4
   Sort | Day Toggle | Live USD/ILS | Portfolio Day Σ
   ═══════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── 1. CSS ───────────────────────────────────────────── */
  var sty = document.createElement('style');
  sty.textContent = `
    .pp-th      { cursor:pointer; user-select:none; }
    .pp-th:hover{ color:hsl(195 100% 60%) !important; }
    .pp-ind     { opacity:.35; font-size:9px; margin-right:2px; vertical-align:middle; }
    .pp-active .pp-ind { opacity:1; color:hsl(195 100% 50%); }
    .pp-day-btn {
      display:inline-block; font-size:9px; padding:1px 5px;
      margin-right:4px; border:1px solid hsl(222 14% 20%);
      background:transparent; color:hsl(210 10% 55%);
      cursor:pointer; border-radius:2px; transition:all .15s;
      vertical-align:middle; font-family:monospace;
    }
    .pp-day-btn:hover, .pp-day-btn.on {
      border-color:hsl(195 100% 50% /.6); color:hsl(195 100% 60%);
    }
    .pp-rate {
      font-size:10px; color:hsl(210 10% 40%);
      margin-right:8px; vertical-align:middle;
    }
  `;
  document.head.appendChild(sty);

  /* ── 2. USD/ILS live rate ─────────────────────────────── */
  var _rate = 3.72;

  function refreshRate() {
    fetch('/api/usdils')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) { if (d && d.rate) { _rate = d.rate; updateRateBadges(); } })
      .catch(function() {});
  }

  function updateRateBadges() {
    document.querySelectorAll('.pp-rate').forEach(function(el) {
      el.textContent = '\u20aa/$ ' + _rate.toFixed(3);
    });
  }

  refreshRate();
  setInterval(refreshRate, 5 * 60 * 1000);

  /* Inject badge next to the refresh button */
  function injectRateBadge() {
    document.querySelectorAll('button').forEach(function(btn) {
      if (btn._ppBadge) return;
      if (btn.textContent.includes('\u21bb') || /\u05e8\u05e2\u05e0\u05df/.test(btn.textContent)) {
        btn._ppBadge = true;
        var b = document.createElement('span');
        b.className = 'pp-rate';
        b.textContent = '\u20aa/$ ' + _rate.toFixed(3);
        if (btn.parentNode) btn.parentNode.insertBefore(b, btn.nextSibling);
      }
    });
  }

  /* ── 3. Holdings table enhancer ──────────────────────── */
  /*
   * Column map (index → key):
   * 0: נייר  1: סוג  2: מניות  3: מחיר עלות  4: מחיר נוכחי
   * 5: שווי  6: רווח/הפסד  7: יום זה
   */
  var COLS = [
    { i:0, k:'ticker',        label:'\u05e0\u05d9\u05d9\u05e8',                          t:'str'    },
    { i:1, k:null,             label:'\u05e1\u05d5\u05d2',                                t:null     },
    { i:2, k:'shares',         label:'\u05de\u05e0\u05d9\u05d5\u05ea',                    t:'num'    },
    { i:3, k:'avgPrice',       label:'\u05de\u05d7\u05d9\u05e8 \u05e2\u05dc\u05d5\u05ea', t:'num'    },
    { i:4, k:'currentPrice',   label:'\u05de\u05d7\u05d9\u05e8 \u05e0\u05d5\u05db\u05d7\u05d9', t:'num' },
    { i:5, k:'value',          label:'\u05e9\u05d5\u05d5\u05d9',                          t:'num'    },
    { i:6, k:'gain',           label:'\u05e8\u05d5\u05d5\u05d7/\u05d4\u05e4\u05e1\u05d3', t:'signed' },
    { i:7, k:'dayGain',        label:'\u05d9\u05d5\u05dd \u05d6\u05d4',                   t:'signed' },
  ];

  var sortKey = 'value', sortDir = -1, dayMode = '$';
  var enhanced = new WeakSet();

  /* Parse a number from a table cell (strips $, %, +, ₪, spaces, —) */
  function parseCell(cell) {
    if (!cell) return null;
    var t = cell.textContent.replace(/[\u20aa$,%+\s]/g, '').trim();
    if (t === '\u2014' || t === '') return null;
    var n = parseFloat(t);
    return isNaN(n) ? null : n;
  }

  /* Build interactive headers */
  function buildHeaders(ths) {
    COLS.forEach(function(col) {
      var th = ths[col.i];
      if (!th || !col.k) return;
      var active = (sortKey === col.k);
      th.className = 'py-2 px-2 text-[10px] text-muted-foreground uppercase tracking-wider '
        + 'font-medium whitespace-nowrap pp-th ' + (active ? 'pp-active' : '');
      th.innerHTML = '';

      /* Day column gets toggle button */
      if (col.k === 'dayGain') {
        var btn = document.createElement('button');
        btn.className = 'pp-day-btn' + (dayMode === '%' ? ' on' : '');
        btn.textContent = dayMode;
        btn.addEventListener('click', function(e) {
          e.stopPropagation();
          dayMode = (dayMode === '$') ? '%' : '$';
          var tbl = th.closest('table');
          if (tbl) doSort(ths, tbl);
        });
        th.appendChild(btn);
      }

      var sp = document.createElement('span');
      sp.textContent = col.label;
      th.appendChild(sp);

      var ind = document.createElement('span');
      ind.className = 'pp-ind';
      ind.textContent = active ? (sortDir > 0 ? ' \u2191' : ' \u2193') : ' \u2195';
      th.appendChild(ind);

      th.addEventListener('click', function() {
        if (sortKey === col.k) sortDir *= -1;
        else { sortKey = col.k; sortDir = -1; }
        var tbl = th.closest('table');
        if (tbl) doSort(ths, tbl);
      });
    });
  }

  function doSort(ths, table) {
    buildHeaders(ths);

    var tbody = table.querySelector('tbody');
    if (!tbody) return;

    var allRows = Array.from(tbody.querySelectorAll('tr'));

    /* Group main rows with their "description" tooltip rows */
    var groups = [];
    allRows.forEach(function(row) {
      var cells = row.cells;
      if (cells.length === 1 && cells[0].colSpan > 4) {
        /* description row — attach to last main row */
        if (groups.length) groups[groups.length - 1].desc = row;
      } else if (cells.length >= 7) {
        /* Cache numeric values for sort/toggle */
        var vc = cells[5], dc = cells[7];
        if (dc && !dc._ppOrig) dc._ppOrig = dc.innerHTML;
        row._ppValue  = parseCell(vc);
        row._ppDayGain = parseCell(dc);
        groups.push({ main: row, desc: null });
      }
    });

    if (!groups.length) return;

    /* Sort */
    var col = COLS.find(function(c) { return c.k === sortKey; });
    groups.sort(function(a, b) {
      var ai, bi;
      if (col && col.t === 'str') {
        ai = a.main.cells[col.i] ? a.main.cells[col.i].textContent.trim() : '';
        bi = b.main.cells[col.i] ? b.main.cells[col.i].textContent.trim() : '';
        return sortDir * ai.localeCompare(bi);
      }
      ai = col ? parseCell(a.main.cells[col.i]) : null;
      bi = col ? parseCell(b.main.cells[col.i]) : null;
      if (ai === null) ai = sortDir > 0 ?  Infinity : -Infinity;
      if (bi === null) bi = sortDir > 0 ?  Infinity : -Infinity;
      return sortDir * (ai - bi);
    });

    /* Re-append */
    groups.forEach(function(g) {
      tbody.appendChild(g.main);
      if (g.desc) tbody.appendChild(g.desc);
    });

    /* Apply day mode */
    groups.forEach(function(g) {
      var dc = g.main.cells[7];
      if (!dc) return;

      if (dayMode === '%') {
        var dv = g.main._ppDayGain, v = g.main._ppValue;
        if (dv != null && v != null && v !== 0) {
          var prev = v - dv;
          var pct  = prev !== 0 ? dv / prev * 100 : 0;
          var pos  = pct >= 0;
          dc.innerHTML =
            '<span class="num" style="color:' + (pos ? '#34d399' : '#f87171') + '">'
            + (pos ? '+' : '') + pct.toFixed(2) + '%</span>';
        }
      } else {
        if (dc._ppOrig) dc.innerHTML = dc._ppOrig;
      }
    });

    /* Update portfolio-day-total display */
    updateDayTotal(groups);
  }

  /* ── 4. Portfolio day-total badge ────────────────────── */
  function updateDayTotal(groups) {
    var total = 0, allKnown = true;
    groups.forEach(function(g) {
      var dv = g.main._ppDayGain;
      if (dv == null) allKnown = false;
      else total += dv;
    });
    if (!allKnown) return;

    /* Find/create the badge container */
    var badge = document.getElementById('pp-day-total');
    if (!badge) {
      /* Try to inject near the "שינוי יומי" card */
      var labels = document.querySelectorAll('span');
      var anchor = null;
      labels.forEach(function(el) {
        if (el.textContent.trim() === '\u05e9\u05d9\u05e0\u05d5\u05d9 \u05d9\u05d5\u05de\u05d9') anchor = el;
      });
      if (!anchor) return;
      badge = document.createElement('div');
      badge.id = 'pp-day-total';
      badge.className = 'text-[10px] text-muted-foreground num mt-0.5';
      anchor.closest('[class]').appendChild(badge);
    }

    var pos = total >= 0;
    badge.innerHTML =
      'סה"כ יומי: <span style="color:' + (pos ? '#34d399' : '#f87171') + ';font-weight:600">'
      + (pos ? '+' : '') + '$' + Math.abs(total).toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2})
      + '</span>';
  }

  /* ── 5. MutationObserver wires everything together ───── */
  function enhance(table) {
    if (enhanced.has(table)) return;
    var ths   = Array.from(table.querySelectorAll('thead th'));
    var labels = ths.map(function(h) { return h.textContent.trim(); });

    /* Holdings table: must contain the Hebrew header "נייר" and "שווי" */
    if (!labels.includes('\u05e0\u05d9\u05d9\u05e8')) return;
    if (!labels.some(function(l) { return l.includes('\u05e9\u05d5\u05d5\u05d9'); })) return;

    enhanced.add(table);
    buildHeaders(ths);
    doSort(ths, table);
  }

  var obs = new MutationObserver(function() {
    document.querySelectorAll('table').forEach(enhance);
    injectRateBadge();
  });
  obs.observe(document.body, { childList: true, subtree: true });

  /* Initial run after React has rendered */
  setTimeout(function() {
    document.querySelectorAll('table').forEach(enhance);
    injectRateBadge();
  }, 1200);

})();
</script>
"""

# ── Server endpoint ────────────────────────────────────────────────────────────
ENDPOINT = """
@app.route('/api/usdils')
def usdils():
    try:
        info = yf.Ticker('USDILS=X').fast_info
        rate = info.last_price
        prev = info.previous_close
        if rate:
            return jsonify({'rate': round(rate, 4), 'prev': round(prev, 4) if prev else None})
    except:
        pass
    return jsonify({'rate': None, 'prev': None})
"""


def patch_html(path):
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()

    # Remove previous patch if present
    MARKER = 'Primo Portfolio Feature Patch v'
    if MARKER in html:
        start = len(html)
        for i in range(len(html) - 1, -1, -1):
            if html[i:].startswith('<script>') or html[i:].startswith('\n<script>'):
                chunk = html[i:i+400]
                if MARKER in chunk:
                    start = i
                    break
        if start < len(html):
            end = html.find('</script>', start)
            if end > 0:
                html = html[:start] + html[end + len('</script>'):]
                print('✓ Removed previous patch')

    if '</body>' in html:
        html = html.replace('</body>', PATCH + '\n</body>', 1)
    else:
        html += PATCH

    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'✓ Patched {path}')


def patch_server(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    if '/api/usdils' in content:
        print('✓ server.py already has /api/usdils')
        return

    marker = "@app.route('/', defaults={'path': ''})"
    if marker in content:
        content = content.replace(marker, ENDPOINT + '\n' + marker)
    else:
        content = content.replace(
            "if __name__ == '__main__':",
            ENDPOINT + "\nif __name__ == '__main__':"
        )

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'✓ Patched {path}')


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    html_path   = os.path.join(here, 'app.html')
    server_path = os.path.join(here, 'server.py')

    if not os.path.exists(html_path):
        print(f'✗ app.html not found in {here}')
        return

    bak = html_path + '.bak'
    if not os.path.exists(bak):
        shutil.copy2(html_path, bak)
        print(f'✓ Backup: {bak}')

    patch_html(html_path)

    if os.path.exists(server_path):
        patch_server(server_path)
    else:
        print('⚠  server.py not found — add this endpoint manually:')
        print(ENDPOINT)

    print()
    print('✅ Done!  Restart the server and refresh the browser.')
    print()
    print('Features:')
    print('  • Sort any column – click its header (↕ → ↑/↓)')
    print('  • Day toggle – $ ↔ % button in the "יום זה" header')
    print('  • USD/ILS live badge next to the refresh button')
    print('  • Portfolio total daily change badge')


if __name__ == '__main__':
    main()
