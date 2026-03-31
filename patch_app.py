#!/usr/bin/env python3
"""Primo Portfolio Patch v5 – safe DOM sort + day toggle"""
import os, shutil, re

MARKER = 'Primo Portfolio Feature Patch v'

PATCH = r"""
<script>
/* ═══════════════════════════════════════════════════
   Primo Portfolio Feature Patch v5
   Sort | Day Toggle | Live USD/ILS
   ═══════════════════════════════════════════════════ */
(function () {
  'use strict';

  var sty = document.createElement('style');
  sty.textContent = [
    '.pp-th{cursor:pointer;user-select:none;}',
    '.pp-th:hover{color:hsl(195 100% 60%)!important;}',
    '.pp-asc .pp-ind::after{content:" ↑";opacity:1;color:hsl(195 100% 50%);}',
    '.pp-desc .pp-ind::after{content:" ↓";opacity:1;color:hsl(195 100% 50%);}',
    '.pp-ind::after{content:" ↕";opacity:.35;font-size:9px;}',
    '.pp-day-btn{display:inline-block;font-size:9px;padding:1px 5px;margin-left:3px;',
    'border:1px solid hsl(222 14% 20%);background:transparent;color:hsl(210 10% 55%);',
    'cursor:pointer;border-radius:2px;transition:all .15s;vertical-align:middle;font-family:monospace;}',
    '.pp-day-btn.on,.pp-day-btn:hover{border-color:hsl(195 100% 50%/.6);color:hsl(195 100% 60%);}',
    '.pp-rate{font-size:10px;color:hsl(210 10% 40%);margin-left:8px;vertical-align:middle;}'
  ].join('');
  document.head.appendChild(sty);

  var _rate = 3.72;
  function refreshRate() {
    fetch('/api/usdils')
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(d){ if (d && d.rate) { _rate = d.rate; document.querySelectorAll('.pp-rate').forEach(function(el){ el.textContent = '₪/$ '+_rate.toFixed(3); }); } })
      .catch(function(){});
  }
  refreshRate();
  setInterval(refreshRate, 5*60*1000);

  function injectRateBadge() {
    document.querySelectorAll('button').forEach(function(btn) {
      if (btn._ppBadge) return;
      if (btn.textContent.includes('↻') || /רענן/.test(btn.textContent)) {
        btn._ppBadge = true;
        var b = document.createElement('span');
        b.className = 'pp-rate';
        b.textContent = '₪/$ '+_rate.toFixed(3);
        btn.parentNode && btn.parentNode.insertBefore(b, btn.nextSibling);
      }
    });
  }

  var enhanced = new WeakSet();

  function parseNum(cell) {
    if (!cell) return null;
    var t = cell.textContent.replace(/[₪$,%+\s]/g,'').replace(/—/g,'').trim();
    if (!t) return null;
    var n = parseFloat(t);
    return isNaN(n) ? null : n;
  }

  /* Snapshot row data ONCE */
  function snapshotRows(tbody) {
    Array.from(tbody.rows).forEach(function(row) {
      if (row._ppSnap) return;
      if (row.cells.length === 1 && row.cells[0].colSpan > 4) return;
      if (row.cells.length < 7) return;
      row._ppSnap = {
        ticker:   row.cells[0] ? row.cells[0].textContent.trim() : '',
        shares:   parseNum(row.cells[2]),
        avgPrice: parseNum(row.cells[3]),
        curPrice: parseNum(row.cells[4]),
        value:    parseNum(row.cells[5]),
        gain:     parseNum(row.cells[6]),
        dayGain:  parseNum(row.cells[7]),
        dayHTML:  row.cells[7] ? row.cells[7].innerHTML : ''
      };
    });
  }

  function collectGroups(tbody) {
    var groups = [];
    Array.from(tbody.rows).forEach(function(row) {
      if (row.cells.length === 1 && row.cells[0].colSpan > 4) {
        if (groups.length) groups[groups.length-1].desc = row;
      } else if (row.cells.length >= 7) {
        groups.push({ main: row, desc: null });
      }
    });
    return groups;
  }

  function buildHeaders(table, ths, state) {
    var COLS = [
      {i:0,k:'ticker',  label:'נייר',        t:'str'},
      {i:1,k:null,       label:'סוג',          t:null},
      {i:2,k:'shares',   label:'מניות',        t:'num'},
      {i:3,k:'avgPrice', label:'מחיר עלות',    t:'num'},
      {i:4,k:'curPrice', label:'מחיר נוכחי',   t:'num'},
      {i:5,k:'value',    label:'שווי',         t:'num'},
      {i:6,k:'gain',     label:'רווח/הפסד',    t:'num'},
      {i:7,k:'dayGain',  label:'יום זה',       t:'num'},
    ];
    COLS.forEach(function(col) {
      var th = ths[col.i];
      if (!th) return;
      var isActive = (state.sortKey === col.k);
      var dir = isActive ? (state.sortDir > 0 ? 'pp-asc' : 'pp-desc') : '';
      th.className = 'py-2 px-2 text-[10px] text-muted-foreground uppercase tracking-wider font-medium whitespace-nowrap'
        + (col.k ? ' pp-th ' + dir : '');
      th.innerHTML = '';

      if (col.k === 'dayGain') {
        var btn = document.createElement('button');
        btn.className = 'pp-day-btn' + (state.dayMode === '%' ? ' on' : '');
        btn.textContent = state.dayMode;
        btn.addEventListener('click', function(e) {
          e.stopPropagation();
          state.dayMode = (state.dayMode === '$') ? '%' : '$';
          renderTable(table, ths, state);
        });
        th.appendChild(btn);
      }

      var sp = document.createElement('span');
      sp.textContent = col.label;
      th.appendChild(sp);

      if (col.k) {
        var ind = document.createElement('span');
        ind.className = 'pp-ind';
        th.appendChild(ind);

        th.addEventListener('click', function() {
          if (state.sortKey === col.k) state.sortDir *= -1;
          else { state.sortKey = col.k; state.sortDir = -1; }
          renderTable(table, ths, state);
        });
      }
    });
  }

  function renderTable(table, ths, state) {
    buildHeaders(table, ths, state);
    var tbody = table.querySelector('tbody');
    if (!tbody) return;

    snapshotRows(tbody);
    var groups = collectGroups(tbody);
    if (!groups.length) return;

    var KEY = state.sortKey;
    groups.sort(function(a, b) {
      var sa = a.main._ppSnap, sb = b.main._ppSnap;
      if (!sa || !sb) return 0;
      if (KEY === 'ticker') return state.sortDir * sa.ticker.localeCompare(sb.ticker);
      var av = sa[KEY], bv = sb[KEY];
      if (av === null) av = state.sortDir > 0 ? Infinity : -Infinity;
      if (bv === null) bv = state.sortDir > 0 ? Infinity : -Infinity;
      return state.sortDir * (av - bv);
    });

    var frag = document.createDocumentFragment();
    groups.forEach(function(g) {
      frag.appendChild(g.main);
      if (g.desc) frag.appendChild(g.desc);
    });
    tbody.appendChild(frag);

    groups.forEach(function(g) {
      var dc = g.main.cells[7], snap = g.main._ppSnap;
      if (!dc || !snap) return;
      if (state.dayMode === '%') {
        var dv = snap.dayGain, v = snap.value;
        if (dv != null && v != null) {
          var prev = v - dv;
          var pct = prev !== 0 ? dv / prev * 100 : 0;
          var pos = pct >= 0;
          dc.innerHTML = '<span class="num" style="color:'+(pos?'#34d399':'#f87171')+';">'+(pos?'+':'')+pct.toFixed(2)+'%</span>';
        }
      } else {
        dc.innerHTML = snap.dayHTML;
      }
    });
  }

  function enhance(table) {
    if (enhanced.has(table)) return;
    var ths = Array.from(table.querySelectorAll('thead th'));
    var labels = ths.map(function(h){ return h.textContent.trim(); });
    if (!labels.includes('נייר')) return;
    if (!labels.some(function(l){ return l.includes('שווי'); })) return;
    enhanced.add(table);
    var state = { sortKey: 'value', sortDir: -1, dayMode: '$' };
    renderTable(table, ths, state);
  }

  var obs = new MutationObserver(function() {
    document.querySelectorAll('table').forEach(enhance);
    injectRateBadge();
  });
  obs.observe(document.body, { childList: true, subtree: true });

  setTimeout(function() {
    document.querySelectorAll('table').forEach(enhance);
    injectRateBadge();
  }, 1200);

})();
</script>
"""

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

def strip_old_patch(html):
    pattern = re.compile(
        r'\n?<script>\s*/\*[^*]*Primo Portfolio Feature Patch v.*?</script>',
        re.DOTALL
    )
    cleaned, n = pattern.subn('', html)
    if n:
        print(f'✓ Removed {n} old patch block(s)')
    return cleaned

def patch_html(path):
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    html = strip_old_patch(html)
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
        content = content.replace("if __name__ == '__main__':", ENDPOINT + "\nif __name__ == '__main__':")
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
        print('⚠  server.py not found')
    print()
    print('✅ Done! Restart server + hard refresh (Ctrl+Shift+R)')

if __name__ == '__main__':
    main()
