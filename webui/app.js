/* Hermes achievements — Steam-style showcase renderer.
   Pure vanilla JS, no dependencies, works offline.
   Loads state.json (relative to this page by default, or a path given
   via ?state=/path/to/state.json), merges it with ACHIEVEMENT_DEFS,
   and renders groups, tiles, stats, progress, lock states. */
'use strict';

(function () {
  const DEFS = window.ACHIEVEMENT_DEFS || {};
  const DEF_IDS = Object.keys(DEFS);

  const GROUP_EMOJIS = {
    'Getting Started': '🚀', 'Tools & Skills': '🛠️', 'Power User': '⚡',
    'Expert': '👑', 'Milestones': '🎯', 'Community': '🤝',
  };
  const GROUP_ORDER = ['Getting Started', 'Tools & Skills', 'Power User', 'Expert', 'Milestones', 'Community'];
  const RARITY_COLORS = {
    'common': '#9CA3AF', 'uncommon': '#22C55E', 'rare': '#3B82F6', 'epic': '#A855F7', 'legendary': '#F59E0B',
  };
  const RARITY_ORDER = ['common', 'uncommon', 'rare', 'epic', 'legendary'];
  const RARITY_WEIGHT = { common: 0, uncommon: 1, rare: 2, epic: 3, legendary: 4 };

  const app = document.getElementById('app');
  const state = { data: null, groupFilter: 'all', search: '', sort: 'group' };

  /* ── utils ─────────────────────────────────────────── */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function fmtDate(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      if (isNaN(d)) return '';
      return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      });
    } catch (e) { return iso; }
  }

  // Sum tool call counts across tools_used map (or absent -> 0)
  function totalToolCalls(stats) {
    const tu = stats && stats.tools_used;
    if (!tu) return 0;
    if (Array.isArray(tu)) return tu.length;
    return Object.values(tu).reduce((a, b) => a + Number(b || 0), 0);
  }

  /* ── data loading ──────────────────────────────────── */
  function statePathFromQuery() {
    try {
      const p = new URLSearchParams(window.location.search).get('state');
      return p ? p : null;
    } catch (e) { return null; }
  }

  async function loadState() {
    const explicit = statePathFromQuery();
    let url = explicit || 'state.json';
    // Prefix filesystem-ish paths with a tiny guard so we still fetch relative-ish
    try {
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) {
      if (explicit) {
        // user asked for a specific path that failed — report clearly
        throw new Error('Could not load state from "' + url + '": ' + (e.message || e));
      }
      // default state.json missing — degrade gracefully
      return null;
    }
  }

  /* ── normalization ─────────────────────────────────── */
  function buildView(data) {
    const stats = (data && data.stats) || {};
    const achState = (data && data.achievements) || {};

    // group defs by group in canonical order (defs not in any known group -> "Other")
    const groups = new Map();
    const items = DEF_IDS.map(id => {
      const def = DEFS[id];
      const st = achState[id];
      const g = GROUP_ORDER.includes(def.group) ? def.group : 'Other';
      const unlocked = !!(st && st.unlocked);
      const progress = (!unlocked && st && st.progress) ? st.progress : null;
      return {
        id, def, group: g,
        unlocked,
        unlocked_at: (st && st.unlocked_at) || null,
        progress,
        pct: progress ? Math.round((progress.current / progress.target) * 100) : 0,
        secret: !!def.secret,
      };
    });

    for (const it of items) {
      if (!groups.has(it.group)) groups.set(it.group, []);
      groups.get(it.group).push(it);
    }

    const unlockedCount = items.filter(i => i.unlocked).length;
    const completionistUnlocked = !!(achState.completionist && achState.completionist.unlocked);

    const recent = (data && Array.isArray(data.newly_unlocked)) ? data.newly_unlocked : [];

    return {
      groups, items,
      stats,
      totalTurns: stats.total_turns || 0,
      toolCalls: totalToolCalls(stats),
      longestStreak: stats.longest_streak || 0,
      currentStreak: stats.current_streak || 0,
      totalDefs: DEF_IDS.length,
      unlockedCount,
      lockedCount: DEF_IDS.length - unlockedCount,
      pct: Math.round((unlockedCount / DEF_IDS.length) * 100),
      completionistUnlocked,
      recent,
      lastUpdated: data && data.last_updated,
    };
  }

  /* ── rendering ─────────────────────────────────────── */
  function statBox(num, label, opts) {
    opts = opts || {};
    const colorStyle = opts.color ? ' style="--rc:' + opts.color + '"' : '';
    return '<div class="stat' + (opts.rarity ? ' rarity' : '') + '"' + colorStyle + '>' +
      '<div class="num">' + esc(num) + '</div>' +
      '<div class="lbl">' + esc(label) + '</div></div>';
  }

  function renderHeader(v) {
    const rarityCounts = {};
    for (const id of DEF_IDS) {
      const it = DEFS[id];
      const unlocked = !!(v.data.achievements && v.data.achievements[id] && v.data.achievements[id].unlocked);
      rarityCounts[it.rarity] = rarityCounts[it.rarity] || 0;
      if (unlocked) rarityCounts[it.rarity]++;
    }

    const strip = document.getElementById('stat-strip');
    let html = statBox(v.unlockedCount + ' / ' + v.totalDefs, 'Unlocked');
    let progbars = '<div class="intro-progress"><div class="pct"><span><b>' + v.unlockedCount +
      '</b> of <b>' + v.totalDefs + '</b> achievements</span><span><b>' + v.pct + '%</b> complete</span></div>' +
      '<div class="bar"><span style="width:' + v.pct + '%"></span></div></div>';

    for (const r of RARITY_ORDER) {
      const c = RARITY_COLORS[r];
      html += statBox((rarityCounts[r] || 0), RARITY_ORDER.length && r.charAt(0).toUpperCase() + r.slice(1), { rarity: true, color: c });
    }
    html += statBox(v.totalTurns, 'Total turns') +
      statBox(v.toolCalls, 'Tool calls') +
      statBox(v.longestStreak ? v.longestStreak + ' days' : '—', 'Longest streak');

    strip.innerHTML = html;
    // insert progress bar right after banner (before strip)
    const banner = document.getElementById('banner');
    const existingIntro = banner.querySelector('.intro-progress');
    if (existingIntro) existingIntro.remove();
    banner.insertAdjacentHTML('beforeend', progbars);

    // completionist banner
    const comp = document.getElementById('completionist-banner');
    if (v.completionistUnlocked) {
      comp.classList.remove('hidden');
    } else {
      comp.classList.add('hidden');
    }

    renderTicker(v);
  }

  function renderTicker(v) {
    const wrap = document.getElementById('ticker-inner');
    if (!v.recent.length) {
      document.getElementById('recent').classList.add('hidden');
      return;
    }
    document.getElementById('recent').classList.remove('hidden');
    // repeat items so the marquee loop looks continuous; cap display
    const shown = v.recent.slice(0, 20);
    const doubled = shown.concat(shown);
    wrap.innerHTML = doubled.map(id => {
      const def = DEFS[id];
      if (!def) return '';
      return '<span class="ticker-item">' +
        '<span class="e">' + esc(def.emoji || '🏅') + '</span>' +
        esc(def.name || id) + '</span>';
    }).join('');
  }

  function tileHTML(it) {
    const def = it.def;
    const rc = RARITY_COLORS[def.rarity] || '#9CA3AF';
    const cls = 'tile ' + (it.unlocked ? 'unlocked' : 'locked') + (it.secret ? ' secret' : '');
    const dateStr = it.unlocked ? fmtDate(it.unlocked_at) : '';

    let body = '';
    if (it.unlocked) {
      body = '<div class="meta"><span class="chip" style="--rc:' + rc + '">' + esc(def.rarity) +
        '</span><span class="stamp">✓ UNLOCKED</span></div>';
    } else if (it.progress) {
      body = '<div class="meta" style="justify-content:center"><span class="chip" style="--rc:' + rc + '">' +
        esc(def.rarity) + '</span></div>' +
        '<div class="progress-wrap">' +
        '<div class="progress-lbl"><span>' + esc(it.progress.current) + ' / ' + esc(it.progress.target) +
        '</span><span class="pct">' + it.pct + '%</span></div>' +
        '<div class="progress-bar"><span style="width:' + it.pct + '%; --rc:' + rc + '; --rc-glow:' + rc + '33;"></span></div>' +
        '</div>';
    } else {
      body = '<div class="meta" style="justify-content:center"><span class="chip" style="--rc:' + rc + '">' +
        esc(def.rarity) + '</span></div>';
    }

    return '<div class="' + cls + '" style="--rc:' + rc + '; --rc-glow:' + rc + '55;" data-id="' +
      esc(def.id) + '" data-rarity="' + esc(def.rarity) + '" data-group="' + esc(it.group) + '">' +
      '<div class="badge"><span class="emoji">' + esc(def.emoji || '🏅') + '</span></div>' +
      '<div class="name">' + esc(it.unlocked ? def.name : (it.secret ? '???' : def.name)) + '</div>' +
      '<div class="desc">' + esc(it.unlocked ? def.description : (it.secret ? 'Secret achievement' : def.description)) + '</div>' +
      body +
      (dateStr ? '<div class="date">' + esc(dateStr) + '</div>' : '') +
      '</div>';
  }

  function renderGroups(v) {
    const main = app;
    const q = state.search.trim().toLowerCase();
    const matchesSearch = it =>
      !q || (it.def.name || '').toLowerCase().includes(q) ||
      (it.def.description || '').toLowerCase().includes(q) ||
      (it.def.id || '').toLowerCase().includes(q);

    let html = '';
    // sort options
    const sortByAsc = state.sort === 'asc';

    const visibleGroupCounts = {};
    for (const [g, items] of v.groups) {
      if (state.groupFilter !== 'all' && state.groupFilter !== g) continue;
      for (const it of items) {
        if (!matchesSearch(it)) continue;
        visibleGroupCounts[g] = (visibleGroupCounts[g] || 0) + 1;
      }
    }

    for (const g of v.groups.keys()) {
      if (state.groupFilter !== 'all' && state.groupFilter !== g) continue;
      const items = v.groups.get(g).filter(matchesSearch);
      if (!items.length) continue;

      let sorted = items.slice();
      if (state.sort === 'unlocked') sorted.sort((a, b) => (b.unlocked - a.unlocked));
      else if (state.sort === 'rarity') sorted.sort((a, b) => RARITY_WEIGHT[b.def.rarity] - RARITY_WEIGHT[a.def.rarity]);
      else if (state.sort === 'newest') sorted.sort((a, b) => (b.unlocked_at || '').localeCompare(a.unlocked_at || ''));

      const gcount = sorted.length;
      const gunlock = sorted.filter(i => i.unlocked).length;
      html += '<section class="ach-section" data-group="' + esc(g) + '">' +
        '<div class="sec-header"><span class="ge">' + esc(GROUP_EMOJIS[g] || '🏅') + '</span>' +
        '<span class="gt">' + esc(g) + '</span>' +
        '<span class="gs">' + gunlock + ' / ' + gcount + ' unlocked</span></div>' +
        '<div class="grid">' + sorted.map(tileHTML).join('') + '</div></section>';
    }

    if (!html) {
      html = '<div class="empty">' +
        '<span class="e">🔍</span>' +
        '<h2>No achievements match</h2>' +
        '<p>Try a different search or clear the group filter.</p></div>';
    }
    main.innerHTML = html;
  }

  function render(v) {
    renderHeader(v);
    renderGroups(v);
  }

  /* ── sidebar wiring ────────────────────────────────── */
  function buildSidebar(v) {
    const wrap = document.getElementById('group-filters');
    const counts = {};
    for (const [g, items] of v.groups) {
      counts[g] = { total: items.length, unlocked: 0 };
      items.forEach(it => { if (it.unlocked) counts[g].unlocked++; });
    }
    let html = '<button class="filter-btn' + (state.groupFilter === 'all' ? ' active' : '') + '" data-g="all">' +
      '<span class="grp-emoji">🏆</span>All' +
      '<span class="cnt">' + v.unlockedCount + '/' + v.totalDefs + '</span></button>';
    for (const g of v.groups.keys()) {
      const c = counts[g];
      html += '<button class="filter-btn' + (state.groupFilter === g ? ' active' : '') + '" data-g="' + esc(g) + '">' +
        '<span class="grp-emoji">' + esc(GROUP_EMOJIS[g] || '🏅') + '</span>' + esc(g) +
        '<span class="cnt">' + c.unlocked + '/' + c.total + '</span></button>';
    }
    wrap.innerHTML = html;

    wrap.querySelectorAll('.filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        state.groupFilter = btn.dataset.g;
        buildSidebar(v);
        renderGroups(v);
      });
    });

    // sort buttons
    const sortWrap = document.getElementById('sort-btns');
    sortWrap.innerHTML = [
      ['group', 'Default'], ['unlocked', 'Unlocked first'], ['rarity', 'By rarity'], ['newest', 'Newest'],
    ].map(([k, lbl]) =>
      '<button class="srt-btn' + (state.sort === k ? ' active' : '') + '" data-sort="' + k + '">' + lbl + '</button>'
    ).join('');
    sortWrap.querySelectorAll('.srt-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        state.sort = btn.dataset.sort;
        buildSidebar(v);
        renderGroups(v);
      });
    });

    // search
    const search = document.getElementById('search');
    let timer = null;
    search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        state.search = search.value;
        renderGroups(v);
      }, 120);
    });
  }

  function showMissing() {
    app.innerHTML =
      '<div style="max-width:560px;margin:60px auto" class="empty">' +
      '<span class="e">🎖️</span>' +
      '<h2>No achievements yet</h2>' +
      '<p>Hermes has no achievement data to show. Drop a <code>state.json</code> into this directory ' +
      '(or symlink <code>~/.hermes/achievements/state.json</code> to <code>state.json</code> here), or ' +
      'point at a file with <code>?state=/path/to/state.json</code>.</p>' +
      '<p style="margin-top:14px;font-size:12px">Load command:<br><code>ln -s ~/.hermes/achievements/state.json state.json</code></p>' +
      '</div>';
  }

  function showError(msg) {
    app.innerHTML =
      '<div class="empty"><span class="e">⚠️</span><h2>Could not load state</h2>' +
      '<p>' + esc(msg) + '</p></div>';
  }

  /* ── init ───────────────────────────────────────────── */
  async function init() {
    if (!DEF_IDS.length) {
      showError('Achievement definitions missing (defs.js not loaded).');
      return;
    }
    let data;
    try {
      data = await loadState();
    } catch (e) {
      showError(e.message);
      return;
    }
    if (!data) { showMissing(); return; }
    state.data = data;
    const v = buildView(data);
    v.data = data;
    buildSidebar(v);
    render(v);
  }

  window.addEventListener('DOMContentLoaded', init);
})();