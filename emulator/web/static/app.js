/* Bio-Signal Emulator web control panel (vanilla JS, no build step) */
(() => {
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const api = async (path, opt = {}) => {
  const r = await fetch('/api/v1' + path, { headers: { 'Content-Type': 'application/json' }, ...opt });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
};
const post = (p, body) => api(p, { method: 'POST', body: JSON.stringify(body || {}) });
const patch = (body) => api('/config', { method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-Source': 'gui' }, body: JSON.stringify(body) });   // tagged in the config history
const fmt = (n, d = 0) => (n === undefined || n === null || isNaN(n)) ? '-' : Number(n).toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d });
const toast = (m) => { const t = $('#toast'); t.textContent = m; t.classList.add('on'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), 2200); };
const mmss = (sec) => { sec = Math.max(0, Math.round(sec)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; };
const hhmm = (t) => new Date(t * 1000).toLocaleTimeString('en-GB', { hour12: false });

const LEAD_KO = { bipolar: '바이폴라', unipolar: '유니폴라', leadless: '리드리스' };
const pmSummary = (i) => i ? `<span class="tag warn">⚡ ${i.type === 'icd' ? 'ICD' : i.mode}</span> ${i.type_label} · ${LEAD_KO[i.lead]} 리드${i.type === 'icd' ? ' · <span class="sub">백업 페이싱만: 평소 고유 리듬 유지, 페이스 마커 없음</span>' : ''}<details class="pmd"${pmOpen ? ' open' : ''}><summary>상세</summary><div class="sub">${i.vendor} · ${i.implant_year}년 삽입<br>적응증: ${i.indication}<br>LR ${i.lower_rate} / UR ${i.upper_rate} bpm${i.av_delay_ms ? ' · AVD ' + i.av_delay_ms + ' ms' : ''}<br>스파이크 ${i.spike_amp_mv} mV · 마커 검출 ${i.detect_pct}%<br>기기 배터리: ${i.battery_status}</div></details>` : '<span class="tag">없음</span>';
// ---------------------------------------------------------------- theme
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const TH = () => ({ waveBg: cssVar('--wave-bg'), ecg: cssVar('--ecg'), ppg: cssVar('--ppg'), resp: cssVar('--resp'), ax: [cssVar('--acc-x'), cssVar('--acc-y'), cssVar('--acc-z')],
  muted: cssVar('--muted'), acc: cssVar('--acc'), acc2: cssVar('--acc2'), warn: cssVar('--warn'), err: cssVar('--err'), gl: cssVar('--gl'),
  mapBg: cssVar('--map-bg'), mapCorr: cssVar('--map-corr'), room: cssVar('--room'), room2: cssVar('--room2'), line: cssVar('--line'),
  gridMinor: cssVar('--grid-minor'), gridMajor: cssVar('--grid-major'), temp: cssVar('--temp') });
function setTheme(t) {
  document.documentElement.dataset.theme = t; try { localStorage.setItem('theme', t); } catch (e) { }
  $('#btnTheme').textContent = t === 'light' ? '☀️' : '🌙';
  Object.values(waves).forEach(st => { st.w = 0; });                       // repaint canvases with the new palette
  if ($('[data-tab="hosp"]') && $('[data-tab="hosp"]').classList.contains('on')) loadFloor();
}

// ---------------------------------------------------------------- custom dropdown (touch friendly, replaces native <select>)
// The hidden <select> stays as the data model: options are read from it, value/change are proxied to it.
const dropdowns = new Map();
let ddOpen = null;
const ddBackdrop = document.createElement('div'); ddBackdrop.className = 'dd-backdrop'; document.body.appendChild(ddBackdrop);
function closeDropdown() { if (ddOpen) { ddOpen.wrap.classList.remove('open'); ddOpen = null; ddBackdrop.classList.remove('on'); } }
ddBackdrop.addEventListener('click', closeDropdown);
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDropdown(); });
function makeDropdown(sel, opts = {}) {
  if (dropdowns.has(sel)) return dropdowns.get(sel);
  sel.classList.add('dd-src');
  const wrap = document.createElement('div'); wrap.className = 'dd' + (opts.compact ? ' compact' : '') + (opts.sheet !== false ? ' sheet' : '') + (opts.cls ? ' ' + opts.cls : '');
  const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'dd-btn'; btn.innerHTML = '<span></span><i>▾</i>';
  const menu = document.createElement('div'); menu.className = 'dd-menu';
  const filter = document.createElement('input'); filter.type = 'text'; filter.className = 'dd-filter'; filter.placeholder = '검색...';
  const list = document.createElement('div'); list.className = 'dd-list';
  menu.appendChild(filter); menu.appendChild(list); wrap.appendChild(btn); wrap.appendChild(menu);
  setTimeout(() => makeClearable(filter), 0);
  sel.parentNode.insertBefore(wrap, sel.nextSibling);
  const dd = { sel, wrap, btn, menu, filter, list, opts };
  const render = (q = '') => {
    list.innerHTML = ''; const ql = q.toLowerCase(); let n = 0;
    Array.from(sel.options).forEach(o => {
      if (o.disabled || (ql && !o.textContent.toLowerCase().includes(ql))) return;       // placeholder / disabled options are not pickable
      const it = document.createElement('div'); it.className = 'dd-item' + (o.value === sel.value ? ' on' : ''); it.textContent = o.textContent; it.dataset.value = o.value;
      it.addEventListener('click', () => { sel.value = o.value; sel.dispatchEvent(new Event('change', { bubbles: true })); sync(); closeDropdown(); });   // always fire: after a search the first item may already be the current value
      list.appendChild(it); n++;
    });
    if (!n) { const e = document.createElement('div'); e.className = 'dd-empty'; e.textContent = '항목 없음'; list.appendChild(e); }
    (filter.closest('.clr') || filter).style.display = sel.options.length > 8 ? '' : 'none';   // hide the wrapper too: it carries the magnifier button
  };
  const sync = () => { const o = sel.options[sel.selectedIndex]; btn.firstChild.textContent = o ? o.textContent : (opts.placeholder || '선택'); };
  dd.sync = sync; dd.render = render;
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (ddOpen === dd) { closeDropdown(); return; }
    closeDropdown(); ddOpen = dd; filter.value = ''; render();
    const r = btn.getBoundingClientRect(); wrap.classList.toggle('up', window.innerHeight - r.bottom < 260 && r.top > 300);
    wrap.classList.add('open'); ddBackdrop.classList.add('on');
    const on = list.querySelector('.dd-item.on'); if (on) on.scrollIntoView({ block: 'center' });
    if (sel.options.length > 8 && window.innerWidth > 640) setTimeout(() => filter.focus(), 30);
  });
  filter.addEventListener('input', () => render(filter.value));
  filter.addEventListener('keydown', e => { if (e.key === 'Enter') { const f = list.querySelector('.dd-item'); if (f) f.click(); } });
  new MutationObserver(() => { sync(); if (ddOpen === dd) render(filter.value); }).observe(sel, { childList: true, subtree: true, characterData: true });
  sel.addEventListener('change', sync);
  sync(); dropdowns.set(sel, dd); return dd;
}
const syncDropdowns = () => dropdowns.forEach(d => d.sync());

const battIcon = (pct, charging = false) => {
  pct = Math.max(0, Math.min(100, Number(pct) || 0));
  const col = pct <= 15 ? 'var(--err)' : pct <= 40 ? 'var(--warn)' : 'var(--acc)';
  const w = Math.round(18 * pct / 100);
  return `<span class="batt" title="배터리 ${pct}%"><svg viewBox="0 0 26 12" width="26" height="12" aria-hidden="true"><rect x="0.5" y="0.5" width="22" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1"/><rect x="23.5" y="3.5" width="2" height="5" rx="0.8" fill="currentColor"/><rect x="2.5" y="2.5" width="${w}" height="7" rx="1" fill="${col}"/>${charging ? '<path d="M11 1.5 L8 7 h3 l-1 3.5 3-5.5 h-3 z" fill="#fff"/>' : ''}</svg><b style="color:${col}">${pct}%</b></span>`;
};
let pmOpen = false;   // keep the pacemaker details toggle state across the 5 s card refresh
document.addEventListener('toggle', e => { if (e.target.classList && e.target.classList.contains('pmd')) pmOpen = e.target.open; }, true);
let curPaced = '', CFG = null, META = null, STATS = null, curRow = -1, curPid = null, ws = null, floors = [], lastEvSeq = 0, evAll = [];

// ---------------------------------------------------------------- tabs
const tabs = $$('section.tab');
const nav = $('#tabs'), sel = $('#tabSelect');
tabs.forEach(t => {
  const b = document.createElement('button'); b.textContent = t.dataset.title; b.dataset.tab = t.dataset.tab; nav.appendChild(b);
  const o = document.createElement('option'); o.value = t.dataset.tab; o.textContent = '메뉴: ' + t.dataset.title; sel.appendChild(o);
});
function showTab(id) {
  tabs.forEach(t => t.classList.toggle('on', t.dataset.tab === id));
  $$('button', nav).forEach(b => b.classList.toggle('on', b.dataset.tab === id));
  sel.value = id; syncDropdowns(); try { localStorage.setItem('tab', id); } catch (e) { }
  if (id === 'hosp') { loadFloor(); drawElevation(); }
  if (id === 'proto') loadProto();
  if (id === 'pat') loadPatients();
  if (id === 'log') renderLog();
  if (id === 'data') { loadBankFiles(); loadRegistry(); loadDb(); $('#dbRun').click(); loadConfigHistory(); }
  if (id === 'scn' || id === 'tx') loadScripts();
  if (id === 'scn') loadDevices();
}
nav.addEventListener('click', e => { const b = e.target.closest('button'); if (b) showTab(b.dataset.tab); });
sel.addEventListener('change', () => showTab(sel.value));
let initTab = 'dash'; try { initTab = localStorage.getItem('tab') || 'dash'; } catch (e) { }

// ---------------------------------------------------------------- config binding
const B = {  // element id -> config path
  g_active: ['general', 'active_patients'], g_out: ['general', 'outpatient_count'], g_adm: ['general', 'admissions_per_hour'], g_dis: ['general', 'discharges_per_hour'],
  g_speed: ['general', 'sim_speed'], g_beds: ['general', 'bed_capacity'], g_prof: ['general', 'profile_count'], g_seed: ['general', 'seed'], g_heart: ['general', 'heart_disease_ratio'], g_kr: ['general', 'korean_ratio'],
  s_site: ['scenario', 'site'], s_ep: ['scenario', 'rhythm_episodes'], s_hop: ['scenario', 'variant_hopping'], s_devpol: ['scenario', 'devices', 'policy'], d_mf: ['scenario', 'devices', 'spo2_mix', 'fingertip'], d_mr: ['scenario', 'devices', 'spo2_mix', 'ring'], d_mw: ['scenario', 'devices', 'spo2_mix', 'wrist_ptt'], n_en: ['scenario', 'network', 'enabled'], n_int: ['scenario', 'network', 'intensity'], n_wl: ['scenario', 'network', 'wireless_noise'], n_wd: ['scenario', 'network', 'wired_failure'], n_lat: ['scenario', 'network', 'latency'], n_pw: ['scenario', 'network', 'power_outage'],
  x_ratio: ['scenario', 'exam_trip_ratio'], a_en: ['scenario', 'artifacts', 'enabled'], a_int: ['scenario', 'artifacts', 'intensity'], a_mo: ['scenario', 'artifacts', 'motion'], a_sh: ['scenario', 'artifacts', 'shower'], a_ex: ['scenario', 'artifacts', 'exam_trips'], a_re: ['scenario', 'artifacts', 'patch_reattach'], a_tr: ['scenario', 'artifacts', 'transfer'], a_rp: ['scenario', 'artifacts', 'patch_replace'], a_hm: ['scenario', 'artifacts', 'home_interference'],
  p_days: ['scenario', 'patch', 'battery_days'], p_rep: ['scenario', 'patch', 'replace_below_pct'], p_dr: ['scenario', 'patch', 'battery_drain_enabled'], p_lo: ['scenario', 'patch', 'lead_off_enabled'],
  h_tpl: ['hospital', 'template'], h_size: ['hospital', 'size_by_patients'], h_head: ['hospital', 'headroom_pct'], h_maxb: ['hospital', 'max_buildings'], gw_f: ['scenario', 'gateway', 'fault_enabled'], gw_out: ['scenario', 'gateway', 'outage'], gw_deg: ['scenario', 'gateway', 'degrade'], gw_rep: ['scenario', 'gateway', 'replace'], gw_int: ['scenario', 'gateway', 'fault_intensity'], gw_cap: ['scenario', 'gateway', 'capacity'], gw_cor: ['scenario', 'gateway', 'corridor_gateways'],
  t_ip: ['transport', 'target_ip'], t_port: ['transport', 'target_port'], t_mode: ['transport', 'socket_mode'], t_bundle: ['transport', 'bundle_ms'], t_meta: ['transport', 'meta_every_n_frames'], t_gws: ['transport', 'gw_status_every_n_frames'],
  t_workers: ['transport', 'workers'], t_backlog: ['transport', 'max_send_backlog_bytes'], t_router: ['transport', 'router_status_url'],
  sf_en: ['transport', 'store_forward', 'enabled'], sf_max: ['transport', 'store_forward', 'max_bytes_per_gw'], sf_burst: ['transport', 'store_forward', 'burst_frames_per_cycle'],
  fz_en: ['transport', 'fuzz', 'enabled'], fz_rate: ['transport', 'fuzz', 'rate_per_1000'], t_storm: ['transport', 'storm_smoothing'], cap_en: ['transport', 'capture', 'enabled'],
  selRespSrc: ['signals', 'resp_source'], selEcgFs: ['signals', 'ecg_fs'], s_vpr: ['signals', 'variants_per_rhythm'], s_pm: ['signals', 'pacemaker_ratio'],
};
const getPath = (o, p) => p.reduce((a, k) => (a == null ? undefined : a[k]), o);
const setPath = (o, p, v) => { let c = o; for (let i = 0; i < p.length - 1; i++) { c[p[i]] = c[p[i]] || {}; c = c[p[i]]; } c[p[p.length - 1]] = v; return o; };
function fillForm() {
  for (const [id, p] of Object.entries(B)) {
    const el = document.getElementById(id); if (!el) continue;
    const v = getPath(CFG, p);
    if (el.type === 'checkbox') el.checked = !!v; else el.value = v;
    const o = document.getElementById(id + '_o'); if (o) o.value = v;
  }
  $('#g_active').max = CFG.general.bed_capacity;
  const fk = $('#fz_kinds'); if (fk) fk.value = ((CFG.transport.fuzz || {}).kinds || []).join(',');
  renderChips(); renderPresets(); syncDropdowns(); mixNote();
}
$('#fz_kinds').addEventListener('change', () => { const kinds = $('#fz_kinds').value.split(',').map(x => x.trim()).filter(Boolean); queue(['transport', 'fuzz', 'kinds'], kinds); });
$('#capClear').onclick = async () => { const r = await post('/capture', { clear: true }); toast('캡처 파일 삭제'); renderCapture(r); };
function renderCapture(r) { const el = $('#capInfo'); if (!el || !r) return; const mb = r.files.reduce((a, f) => a + f.bytes, 0) / 1e6; el.textContent = `${r.enabled ? '기록 중 · ' : ''}${r.files.length}개 파일 · ${mb.toFixed(1)} MB`; }
let pending = {}, pendT = null;
function queue(p, v) { setPath(pending, p, v); clearTimeout(pendT); pendT = setTimeout(flush, 350); }
async function flush() {
  const body = pending; pending = {}; if (!Object.keys(body).length) return;
  try {
    const r = await patch(body); CFG = r.config;
    if (r.needs_rebuild) toast('구조 설정 변경: [병원·환자 재구성] 버튼으로 반영');
    else if (r.needs_generate) toast('샘플링/변형 설정 변경: [루프 은행 재생성] 필요');
    else toast('설정 반영');
  } catch (e) { toast('설정 실패: ' + e.message); }
}
for (const [id, p] of Object.entries(B)) {
  const el = document.getElementById(id); if (!el) continue;
  el.addEventListener(el.type === 'range' ? 'input' : 'change', () => {
    let v = el.type === 'checkbox' ? el.checked : el.value;
    if (el.type === 'number' || el.type === 'range' || id === 'selEcgFs') v = Number(v);
    const o = document.getElementById(id + '_o'); if (o) o.value = v;
    if (id === 'g_active' || id === 'g_out') { CFG.general[B[id][1]] = v; renderPresets(); }
    if (getPath(CFG, p) === v) return;                 // unchanged (e.g. re-selecting the same dropdown item)
    queue(p, v);
  });
}
const PRESETS = { activePresets: { list: [50, 100, 200, 500, 1000, 2000], key: 'active_patients', input: 'g_active', cap: () => CFG.general.bed_capacity, capMsg: '병상 수' },
                  outPresets: { list: [10, 50, 100, 200], key: 'outpatient_count', input: 'g_out', cap: () => 500, capMsg: '최대' } };
function renderPresets() {
  for (const [id, pr] of Object.entries(PRESETS)) {
    const box = document.getElementById(id); if (!box) continue; box.innerHTML = '';
    const cap = pr.cap(), cur = CFG.general[pr.key];
    pr.list.forEach(n => {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = n.toLocaleString(); b.className = n === cur ? 'on' : ''; b.disabled = n > cap;
      b.title = n > cap ? `${pr.capMsg}(${cap})를 초과합니다` : '';
      b.addEventListener('click', () => { $('#' + pr.input).value = n; $('#' + pr.input + '_o').value = n; CFG.general[pr.key] = n; renderPresets(); queue(['general', pr.key], n); });
      box.appendChild(b);
    });
  }
}
function renderChips() {
  const box = $('#chanChips'); box.innerHTML = '';
  const names = { ecg: 'ECG', hr: 'HR', temp: '체온', resp: '호흡수', spo2: 'SpO2', glucose: '혈당', accel: '가속도', ppg: 'PPG 파형', resp_wave: '호흡 파형', pace: '페이스 마커 (페이스메이커 환자만)' };
  for (const [k, on] of Object.entries(CFG.signals.enabled)) {
    const l = document.createElement('label'); const c = document.createElement('input'); c.type = 'checkbox'; c.checked = on;
    c.addEventListener('change', () => queue(['signals', 'enabled', k], c.checked));
    l.appendChild(c); l.appendChild(document.createTextNode(names[k] || k)); box.appendChild(l);
  }
  $('#boxPpg').style.display = CFG.signals.enabled.ppg ? '' : 'none';
  $('#boxResp').style.display = CFG.signals.enabled.resp_wave ? '' : 'none';
  $('#boxAcc').style.display = CFG.signals.enabled.accel ? '' : 'none';
}
$('#btnStart').onclick = async () => { const r = await post('/control/start'); toast(r.result); refresh(); };
$('#btnStop').onclick = async () => { const r = await post('/control/stop'); toast(r.result); refresh(); };
$('#btnRebuild').onclick = async () => { toast('재구성 중...'); await post('/control/rebuild'); toast('재구성 완료'); await loadConfig(); loadPatientList(); };
$('#btnGen2').onclick = async () => { const r = await post('/control/generate'); toast('루프 생성: ' + r.result); };
$('#btnReset').onclick = async () => { await post('/config/reset'); await loadConfig(); toast('기본값 복원 (재구성 필요)'); };
$('#btnAtStart').onclick = async () => { const r = await post('/control/autotune', { action: 'start', step: Number($('#at_step').value), window_s: Number($('#at_win').value) }); toast(r.result); };
$('#btnAtStop').onclick = async () => { const r = await post('/control/autotune', { action: 'stop' }); toast(r.result); };
$$('button[data-trig]').forEach(b => b.onclick = async () => {
  const inSig = b.closest('[data-tab="sig"]');
  let extra = {}; try { extra = b.dataset.params ? JSON.parse(b.dataset.params) : {}; } catch (e) { }
  const r = await post('/control/trigger', { what: b.dataset.trig, target: inSig ? curPid : null, ...extra }); toast(r.result);
  if (inSig) setTimeout(loadPatCard, 300);
});
// ---- scenario scripts (repeatable drill sequences from scenarios/*.json)
async function loadScripts() {
  try {
    const r = await api('/control/script'); const sel = $('#scriptFile'); const cur = sel.value;
    sel.innerHTML = r.files.map(f => `<option value="${esc(f)}">${esc(f)}</option>`).join('') || '<option value="">(scenarios/ 비어 있음)</option>';
    if (cur && r.files.includes(cur)) sel.value = cur;
    syncDropdowns(); renderScript(r.status);
    try { renderCapture(await api('/capture')); } catch (e) { }
  } catch (e) { }
}
function renderScript(st) {
  if (!st || !st.active) { $('#scriptStatus').textContent = '실행 중인 스크립트 없음'; $('#scriptLog').innerHTML = ''; return; }
  $('#scriptStatus').textContent = `${st.name} · ${st.finished ? '완료' : '실행 중'} · ${st.done}/${st.total} 단계 · ${st.elapsed_s.toFixed(0)}s${st.next ? ` · 다음 +${st.next.at}s ${st.next.what}` : ''}`;
  $('#scriptLog').innerHTML = (st.log || []).slice().reverse().map(l => `<div>+${l.at}s ${esc(l.what)} → ${esc(l.result)}</div>`).join('');
}
$('#scriptStart').onclick = async () => { const f = $('#scriptFile').value; if (!f) return toast('스크립트 파일 없음'); try { const st = await post('/control/script', { file: f }); renderScript(st); toast('스크립트 시작'); } catch (e) { toast('실패: ' + e.message); } };
$('#scriptStop').onclick = async () => { try { const r = await fetch('/api/v1/control/script', { method: 'DELETE' }); renderScript(await r.json()); toast('스크립트 중지'); } catch (e) { } };
setInterval(() => { if ($('[data-tab="scn"]').classList.contains('on')) api('/control/script').then(r => renderScript(r.status)).catch(() => { }); }, 2000);
// event-injection switches: on -> trigger, off -> cancel; state + remaining time come from the patient runtime
const swState = { lead_off: 0, episode: 0, vfib: 0, exam: 0 };
let tripInfo = null, tripInfoAt = 0;
$$('label.sw').forEach(l => {
  const cb = l.querySelector('input');
  cb.addEventListener('change', async () => {
    if (!curPid) { const p = patList.find(x => x.row === Number($('#selPatient').value)); curPid = p ? p.id : null; }
    if (!curPid) { cb.checked = false; toast('환자를 먼저 선택하세요'); return; }
    l.classList.add('busy');
    const r = await post('/control/trigger', { what: cb.checked ? l.dataset.trig : l.dataset.off, target: curPid }); toast(r.result);
    setTimeout(() => { loadPatCard(); l.classList.remove('busy'); }, 300);
  });
});
function applySwitches(r) {
  const now = performance.now();
  tripInfo = r.trip || null; tripInfoAt = now;
  const rem = { lead_off: r.lead_off_remaining || 0, episode: (r.episode_remaining || 0), vfib: 0, exam: r.trip_active ? -1 : 0 };
  if (r.rhythm_now && r.rhythm_now.includes('VFib') && rem.episode > 0) { rem.vfib = rem.episode; rem.episode = 0; }
  if (r.lead_off && !rem.lead_off) rem.lead_off = -1;                  // lead-off without a known end (trip / auto)
  const speed = r.sim_speed || 1;
  $$('label.sw').forEach(l => {
    const k = l.dataset.trig, v = rem[k];
    swState[k] = v > 0 ? now + v / speed * 1000 : (v < 0 ? -1 : 0);
    l.querySelector('input').checked = v !== 0;
  });
  tickSwitches();
}
function tickSwitches() {
  const now = performance.now();
  $$('label.sw').forEach(l => {
    const k = l.dataset.trig, e = swState[k], cb = l.querySelector('input'), rm = l.querySelector('.rem');
    if (e === -1) { rm.textContent = (k === 'exam' && tripInfo) ? `${tripInfo.stage} · 단계 ${mmss(tripInfo.stage_remaining - (now - tripInfoAt) / 1000)} 남음 · 전체 ${mmss(tripInfo.total_remaining - (now - tripInfoAt) / 1000)} 남음` : '진행 중'; l.classList.toggle('long', rm.textContent.length > 8); cb.checked = true; return; }
    l.classList.remove('long');
    if (e > now) { rm.textContent = Math.ceil((e - now) / 1000) + 's'; cb.checked = true; }
    else { if (e) { swState[k] = 0; cb.checked = false; } rm.textContent = ''; if (!e) cb.checked = false; }
  });
}
setInterval(tickSwitches, 1000);

// ---------------------------------------------------------------- status / dashboard
const sparkP = [], sparkB = [];
function drawSpark(cv, data, color) {
  const dpr = window.devicePixelRatio || 1; const w = cv.clientWidth, h = cv.clientHeight; if (!w) return;
  if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  if (data.length < 2) return; const mx = Math.max(1, ...data);
  g.strokeStyle = color; g.lineWidth = 1.5; g.lineJoin = 'round'; g.beginPath();
  data.forEach((v, i) => { const x = i / (data.length - 1) * w, y = h - 3 - v / mx * (h - 8); i ? g.lineTo(x, y) : g.moveTo(x, y); });
  g.stroke(); g.fillStyle = TH().muted; g.font = '11px sans-serif'; g.fillText(fmt(mx, 0), 4, 12);
}
function kpi(l, v, cls = '') { return `<div class="kpi ${cls}"><div class="v">${v}</div><div class="l">${l}</div></div>`; }
async function refresh() {
  try { STATS = await api('/status'); } catch (e) { $('#runPill').className = 'pill err'; $('#runTxt').textContent = '서버 연결 실패'; return; }
  const s = STATS, L = s.last || {}, T = L.total || {};
  $('#runPill').className = 'pill ' + (s.running ? (s.generate_only || (T.connected || 0) > 0 ? 'run' : 'err') : '');
  $('#btnStart').disabled = s.running; $('#btnStop').disabled = !s.running;
  $('#btnStart').classList.toggle('live', s.running); $('#btnStop').classList.toggle('live', !s.running);
  $('#btnStart').textContent = s.running ? '● 실행 중' : '▶ 시작'; $('#runTxt').textContent = s.running ? (s.generate_only ? '실행 중 (생성만, 전송 없음)' : `전송 중 → ${s.target.ip}:${s.target.port} · TCP ${fmt(T.connected)}/${fmt(s.gateways)}`) : '정지';
  $('#pillPat').textContent = `환자 ${s.admitted} / 패치 연결 ${L.active_patches ?? 0}`;
  $('#pillRate').textContent = `${fmt(L.pkts_ps).padStart(6, ' ')} pkt/s · ${fmt((L.bytes_ps || 0) / 1024, 0).padStart(5, ' ')} KB/s`;
  if (CFG) {
    const net = CFG.scenario.network.enabled, art = CFG.scenario.artifacts.enabled;
    $('#pillNet').className = 'pill' + (net ? ' on' : ''); $('#pillNet').lastChild.textContent = net ? `NET 장애 ON ${CFG.scenario.network.intensity}%` : 'NET 장애 OFF';
    $('#pillArt').className = 'pill' + (art ? ' on' : ''); $('#pillArt').lastChild.textContent = art ? `아티팩트 ON ${CFG.scenario.artifacts.intensity}%` : '아티팩트 OFF';
    const gwf = CFG.scenario.gateway.fault_enabled;
    $('#pillGwf').className = 'pill' + (gwf ? ' on' : ''); $('#pillGwf').lastChild.textContent = gwf ? `GW 장애 ON ${CFG.scenario.gateway.fault_intensity}%` : 'GW 장애 OFF';
  }
  const errPkt = (T.drop_emul || 0) + (T.drop_backlog || 0) + (T.send_err || 0);
  const noconn = s.running && !s.generate_only && (T.connected || 0) === 0;
  $('#pillNoConn').hidden = !(noconn || (L.drop_noconn_ps || 0) > 0);
  $('#pillNoConn').className = 'pill' + (noconn ? ' bad' : ' on');
  $('#pillNoConn').textContent = noconn ? `라우터 미연결 · 드롭 ${fmt(T.drop_noconn)}` : `미연결 드롭 ${fmt(T.drop_noconn)}`;
  const errGw = (L.gw_down || 0) + (L.gw_degraded || 0);
  $('#pillErrPkt').className = 'pill' + (errPkt ? ' bad' : ''); $('#pillErrPkt').textContent = `오류 pkt ${fmt(errPkt)}`;
  $('#pillErrGw').className = 'pill' + (errGw ? ' bad' : ''); $('#pillErrGw').textContent = `오류 GW ${fmt(errGw)}`;
  $('#dashTime').textContent = '시뮬 시각 ' + new Date(s.sim_time * 1000).toLocaleString('ko-KR', { hour12: false });
  const w = L.workers || []; const build = Math.max(0, ...w.map(x => x.build_us)), send = Math.max(0, ...w.map(x => x.send_us));
  const bundleUs = (CFG ? CFG.transport.bundle_ms : 200) * 1000; const load = (build + send) / bundleUs * 100;
  $('#kpis').innerHTML = kpi('입원 환자', fmt(s.inpatients)) + kpi('MCOT 환자', fmt(s.outpatients)) + kpi('패치 스트리밍', fmt(L.active_patches), 'ok') + kpi('연결 끊김 패치', fmt(L.unlinked_patches), L.unlinked_patches ? 'warn' : '') +
    kpi('게이트웨이', `${fmt(s.gateways)}`) + kpi('GW 장애 / 저하', `${fmt(L.gw_down)} / ${fmt(L.gw_degraded)}`, L.gw_down ? 'err' : '') + kpi('pkt/s', fmt(L.pkts_ps), 'ok') + kpi('KB/s', fmt((L.bytes_ps || 0) / 1024)) +
    kpi('총 패킷', fmt(T.pkts)) + kpi('총 MB', fmt((T.bytes || 0) / 1e6, 1)) + kpi('에뮬 손실 pkt', fmt(T.drop_emul)) + kpi('백로그 드롭', fmt(T.drop_backlog), T.drop_backlog ? 'err' : '') +
    kpi('TCP 연결 GW', fmt(T.connected)) + kpi('틱 오버런', fmt(T.overruns), T.overruns ? 'warn' : '') + kpi('워커 부하', fmt(load, 0) + '%', load > 70 ? 'err' : load > 40 ? 'warn' : 'ok') + kpi('bank 변형', fmt(s.bank.variants));
  sparkP.push(L.pkts_ps || 0); sparkB.push((L.bytes_ps || 0) / 1024); if (sparkP.length > 120) { sparkP.shift(); sparkB.shift(); }
  drawSpark($('#sparkPkts'), sparkP, TH().acc); drawSpark($('#sparkBytes'), sparkB, TH().acc2);
  const b = s.bank, p = b.progress;
  $('#bankInfo2').textContent = `${b.loaded ? '로드됨' : '미생성'} · 변형 ${b.variants} · ${b.size_mb} MB · ${p.state}${p.state === 'running' ? ` ${p.done}/${p.total} (${p.message}, ETA ${fmt(p.eta_s)}s)` : ''}`;
  $('#bankBar2').style.width = (p.total ? p.done / p.total * 100 : (b.loaded ? 100 : 0)) + '%';
  $('#txKpis').innerHTML = kpi('저장후전송 버퍼', `${fmt((T.saf_bytes || 0) / 1024)} KB · ${fmt(T.saf_gateways)} GW`, T.saf_bytes ? 'warn' : '') + kpi('재전송 프레임', fmt(T.saf_replayed)) + kpi('버퍼 초과 폐기', fmt(T.drop_saf), T.drop_saf ? 'err' : '') + kpi('오염 주입', fmt(T.fuzz), T.fuzz ? 'warn' : '') +
    kpi('pkt/s', fmt(L.pkts_ps)) + kpi('KB/s', fmt((L.bytes_ps || 0) / 1024)) + kpi('총 패킷', fmt(T.pkts)) + kpi('총 MB', fmt((T.bytes || 0) / 1e6, 1)) + kpi('에뮬 손실', fmt(T.drop_emul)) + kpi('백로그 드롭', fmt(T.drop_backlog), T.drop_backlog ? 'err' : '') + kpi('미연결 드롭 / 전송오류', `${fmt(T.drop_noconn)} / ${fmt(T.send_err)}`) + kpi('TCP 연결', fmt(T.connected)) + kpi('오버런', fmt(T.overruns)) + kpi('가동 시간', fmt(s.uptime_s) + 's') + kpi('워커', fmt(s.n_workers));
  $('#wTable tbody').innerHTML = w.map(x => `<tr><td>${x.id}</td><td>${x.alive ? '<span class="tag ok">on</span>' : '<span class="tag err">off</span>'}</td><td>${x.n_patches}</td><td>${x.n_gw}</td><td>${fmt(x.ticks)}</td><td>${x.overruns}</td><td>${fmt(x.build_us)}</td><td>${fmt(x.send_us)}</td></tr>`).join('');
  const at = s.autotune; $('#atInfo').innerHTML = at ? `진행 중: 현재 ${at.current}명, 안정 최대 ${at.best}명, step ${at.step}<br>` + (at.history || []).slice(-6).map(h => `${h.n}명 ${h.ok ? '✅' : '❌'} build ${fmt(h.build_us)}µs send ${fmt(h.send_us)}µs overrun ${h.overruns} drop ${h.backlog_drops}`).join('<br>') : '';
  if (CFG && CFG.general.active_patients !== Number($('#g_active').value) && at) { $('#g_active').value = at.current; $('#g_active_o').value = at.current; }
  pollEvents();
}
async function pollEvents() {
  try {
    const r = await api('/events?since=' + lastEvSeq); if (!r.events.length) return;
    evAll = evAll.concat(r.events).slice(-600); lastEvSeq = evAll[evAll.length - 1].seq;
    const html = (list) => list.slice().reverse().map(e => `<div class="ev ${e.kind}"><span class="t">${hhmm(e.t)}</span><span class="k">${e.kind}</span><span>${e.msg}</span></div>`).join('');
    $('#dashEvents').innerHTML = html(evAll.slice(-40));
    if ($('[data-tab="log"]').classList.contains('on')) renderLog();
    if (r.events.some(e => e.kind === 'adt' || e.kind === 'system')) loadPatientList();
  } catch (e) { }
}
function renderLog() { const f = $('#logFilter').value; $('#logEvents').innerHTML = evAll.slice().reverse().filter(e => !f || e.kind === f).map(e => `<div class="ev ${e.kind}"><span class="t">${hhmm(e.t)}</span><span class="k">${e.kind}</span><span>${e.msg}</span></div>`).join(''); }
$('#logFilter').onchange = renderLog;

// ---------------------------------------------------------------- signals / live
// Smooth sweep renderer: incoming 200 ms frames are queued and drawn progressively
// by requestAnimationFrame at the channel's sample rate, with curve interpolation.
const waves = {};
const SWEEP_SECS = 6;
function mkWave(id, color, range, n) {
  const cv = document.getElementById(id);
  const st = { cv, color, range, n, queue: [], marks: new Map(), counter: 0, drawn: 0, pos: 0, last: null, lastT: 0, w: 0, h: 0 };
  waves[id] = st; return st;
}
function resetWave(st) { st.queue = []; st.marks.clear(); st.pos = 0; st.last = null; st.w = 0; st.drawn = st.counter; }
function feedWave(st, samples, scale, paceIdx, paceType) {
  const multi = Array.isArray(samples[0]);
  for (let i = 0; i < samples.length; i++) st.queue.push(multi ? samples[i].map(v => v * scale) : samples[i] * scale);
  if (paceIdx) paceIdx.forEach((i, k) => st.marks.set(st.counter + i, paceType ? paceType[k] : 1));
  st.counter += samples.length;
  if (st.queue.length > st.n * 1.5) { const drop = st.queue.length - st.n; st.queue.splice(0, drop); st.drawn += drop; st.last = null; }   // resync after tab sleep (gap, no joining line)
}
// paint background + ECG-paper grid for the x-range [x0, x0+wd): 1 mm boxes (0.04 s at 25 mm/s), bold line every 5 mm (0.2 s)
function paintBg(g, st, th, x0, wd, w, h) {
  g.fillStyle = th.waveBg; g.fillRect(x0, 0, wd, h);
  const minor = w / SWEEP_SECS / 25;                 // 0.04 s
  g.lineWidth = 1;
  const k0 = Math.floor(x0 / minor);
  for (let k = k0; k * minor <= x0 + wd; k++) {
    const x = Math.round(k * minor) + 0.5; if (x < x0 - 1) continue;
    g.strokeStyle = k % 5 === 0 ? th.gridMajor : th.gridMinor; g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke();
  }
  const yc = h / 2;                                  // square boxes, bold line through the centre line
  for (let j = -Math.ceil(yc / minor); j * minor <= yc; j++) {
    const y = Math.round(yc + j * minor) + 0.5; if (y < 0 || y > h) continue;
    g.strokeStyle = j % 5 === 0 ? th.gridMajor : th.gridMinor; g.beginPath(); g.moveTo(x0, y); g.lineTo(x0 + wd, y); g.stroke();
  }
}
function drawWave(st, now, colors) {
  const cv = st.cv, dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight; if (!w) return;
  const g = cv.getContext('2d');
  if (st.w !== w || cv.width !== Math.round(w * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); st.w = w; st.h = h; st.pos = 0; st.last = null;
    g.setTransform(dpr, 0, 0, dpr, 0, 0); st.theme = st.fixedTheme || TH(); paintBg(g, st, st.theme, 0, w, w, h);
  }
  const dt = st.lastT ? Math.min(0.1, (now - st.lastT) / 1000) : 0; st.lastT = now;
  if (!st.queue.length) return;
  // pace samples to the sample rate; nudge the rate to hold ~1 frame (200 ms) of buffer
  const target = st.n * 0.2, k = Math.max(0.6, Math.min(1.6, 0.7 + 0.3 * st.queue.length / target));
  st.acc = (st.acc || 0) + dt * st.n * k;
  let take = Math.floor(st.acc); if (take <= 0) return; st.acc -= take; take = Math.min(take, st.queue.length);
  const pts = st.queue.splice(0, take);
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const pxs = w / (SWEEP_SECS * st.n), [lo, hi] = st.range;
  const toY = v => h - 4 - (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * (h - 8);
  const x0 = st.pos, x1 = st.pos + take * pxs;
  const th = st.theme || TH();
  paintBg(g, st, th, x0, Math.min(w - x0, (x1 - x0) + 12), w, h); if (x1 + 12 > w) paintBg(g, st, th, 0, x1 + 12 - w, w, h);
  const multi = Array.isArray(pts[0]), nAx = multi ? pts[0].length : 1, cols = colors || [th[st.color] || st.color];
  g.lineWidth = 1.6; g.lineJoin = 'round'; g.lineCap = 'round';
  for (let ax = 0; ax < nAx; ax++) {
    g.strokeStyle = cols[ax % cols.length]; g.beginPath();
    let px = st.pos, py = st.last ? st.last[ax] : toY(multi ? pts[0][ax] : pts[0]);
    g.moveTo(px, py);
    for (let i = 0; i < take; i++) {
      const x = st.pos + (i + 1) * pxs, y = toY(multi ? pts[i][ax] : pts[i]);
      if (x > w) { g.stroke(); g.beginPath(); g.moveTo(x - w - pxs, py); }
      const xx = x > w ? x - w : x;
      const mx = (px + xx) / 2, my = (py + y) / 2;             // quadratic midpoint smoothing
      if (xx >= px) g.quadraticCurveTo(px, py, mx, my); else g.moveTo(xx, y);
      px = xx; py = y;
    }
    g.lineTo(px, py); g.stroke(); st.last = st.last || []; st.last[ax] = py;
  }
  if (st.marks.size) {
    const PC = [th.warn, th.err, th.gl];   // atrial / ventricular / LV spike markers
    for (let i = 0; i < take; i++) {
      const idx = st.drawn + i; if (!st.marks.has(idx)) continue;
      let x = st.pos + i * pxs; if (x > w) x -= w; const col = PC[st.marks.get(idx)] || PC[1];
      if (th.paceLine) {                   // monitor screens: full-height pacer pulse marker (A / V / LV colour-coded)
        g.save(); g.strokeStyle = th.paceLine[st.marks.get(idx)] || th.paceLine[1] || col; g.lineWidth = 1.5; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(Math.round(x) + 0.5, 2); g.lineTo(Math.round(x) + 0.5, h - 2); g.stroke(); g.restore();
        g.fillStyle = g.strokeStyle; g.fillRect(x - 1, 2, 3, 8);
      } else { g.fillStyle = col; g.fillRect(x, 2, 2, 10); }
      st.marks.delete(idx);
    }
  }
  st.drawn += take;
  st.pos = x1 >= w ? x1 - w : x1;
}
function animate(now) {
  if (mon.open) { for (const st of Object.values(mon.states)) if (st.cv && st.cv.isConnected) drawWave(st, now, st.colors); if (vm.open) for (const st of Object.values(vm.states)) if (st.cv && st.cv.isConnected) drawWave(st, now); }
  if ($('[data-tab="pat"]').classList.contains('on') && waves.wDetail && document.getElementById('wDetail')) { waves.wDetail.cv = document.getElementById('wDetail'); drawWave(waves.wDetail, now); }
  if ($('[data-tab="sig"]').classList.contains('on')) {
    drawWave(waves.wEcg, now); drawWave(waves.wPpg, now); drawWave(waves.wResp, now); drawWave(waves.wAcc, now, (waves.wAcc.theme || TH()).ax);
  }
  requestAnimationFrame(animate);
}
function setupWaves() {
  const fs = CFG.signals;
  mkWave('wEcg', 'ecg', [-1.5, 2.0], fs.ecg_fs);
  mkWave('wPpg', 'ppg', [-1.2, 1.5], fs.ppg_fs);
  mkWave('wResp', 'resp', [-1.5, 1.5], fs.resp_fs);
  mkWave('wAcc', 'ax', [-1.6, 1.6], fs.accel_fs);
}
let wsLastMsg = 0, wsInactive = 0, wsRetries = 0;
function connectWs() {
  if (ws) { try { ws.onclose = null; ws.close(); } catch (e) { } }
  ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/live?row=' + curRow);
  wsLastMsg = performance.now();
  ws.onopen = () => { wsLastMsg = performance.now(); if (curRow >= 0) ws.send(JSON.stringify({ row: curRow })); };
  ws.onmessage = (m) => {
    wsLastMsg = performance.now(); wsRetries = 0;
    const d = JSON.parse(m.data);
    if (d.error) {                                   // row inactive (patient discharged / server restarted): re-sync the selection
      if (++wsInactive === 10) { wsInactive = 0; loadPatientList().then(() => { const p = patList.find(x => x.row === curRow); if (!p && patList.length) selectRow(patList[0].row); else if (p && p.id !== curPid) selectRow(curRow); }); }
      return;
    }
    wsInactive = 0;
    if (d.ecg) { feedWave(waves.wEcg, d.ecg, 0.001, d.pace, d.pace_type); if (waves.wDetail && document.getElementById('wDetail')) feedWave(waves.wDetail, d.ecg, 0.001, d.pace, d.pace_type); }
    if (document.getElementById('dHr')) { const n = d.num || {}; $('#dHr').textContent = n.hr || '--'; $('#vDetailHr').textContent = n.hr ? n.hr + ' bpm' : ''; $('#dSpo2').textContent = n.spo2 || '--'; $('#dResp').textContent = n.resp || '--'; $('#dTemp').textContent = n.temp ? n.temp.toFixed(1) : '--'; }
    if (d.ppg) feedWave(waves.wPpg, d.ppg, 0.001);
    if (d.resp_wave) feedWave(waves.wResp, d.resp_wave, 0.001);
    if (d.accel) feedWave(waves.wAcc, d.accel, 0.001);
    const n = d.num || {};
    $('#nHr').textContent = n.hr || '--'; $('#vHr').textContent = n.hr ? n.hr + ' bpm' : 'LEAD OFF';
    $('#ecgLbl').textContent = 'ECG (mV)' + curPaced;
    $('#nSpo2').textContent = n.spo2 || '--'; $('#vSpo2').textContent = n.spo2 ? n.spo2 + ' %' : '--';
    $('#nResp').textContent = n.resp || '--'; $('#vResp').textContent = n.resp ? n.resp + ' /min' : '--';
    $('#nTemp').textContent = n.temp ? n.temp.toFixed(2) : '--'; $('#nGl').textContent = n.glucose ? n.glucose.toFixed(0) : '--';
  };
  ws.onclose = () => { setTimeout(() => { if (curRow >= 0 && ($('[data-tab="sig"]').classList.contains('on') || document.getElementById('wDetail'))) connectWs(); }, 1500); };
}
// watchdog: no live message for 3 s while the signal tab is visible -> reconnect (half-open sockets after a server restart)
setInterval(() => {
  const liveTab = $('[data-tab="sig"]').classList.contains('on') || ($('[data-tab="pat"]').classList.contains('on') && document.getElementById('wDetail'));
  if (curRow < 0 || !liveTab || document.visibilityState === 'hidden') return;
  if (!ws || ws.readyState > 1 || performance.now() - wsLastMsg > 3000) {
    wsLastMsg = performance.now(); wsRetries++; connectWs();
    if (wsRetries >= 3) $('#ecgLbl').textContent = 'ECG (mV) · 실시간 스트림 연결 안 됨 (서버 WebSocket 지원 확인: uvicorn[standard])';
  }
}, 1000);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') { Object.values(waves).forEach(resetWave); wsLastMsg = 0; } });
let patList = [];
async function loadPatientList() {
  try { const r = await api('/emr/patients?status=admitted&limit=1000'); patList = r.patients; } catch (e) { return; }
  const q = $('#patSearch').value.trim().toLowerCase();
  const s = $('#selPatient'); const cur = s.value; s.innerHTML = '';
  patList.filter(p => !q || p.name.toLowerCase().includes(q) || (p.bed || '').toLowerCase().includes(q)).slice(0, 500).forEach(p => {
    const o = document.createElement('option'); o.value = p.row; o.textContent = `${p.pacemaker ? '⚡ ' : ''}${p.name} · ${p.sex}/${p.age} · ${p.bed} · ${p.disease}${p.pacemaker ? ' · ' + (p.pacemaker_type === 'icd' ? 'ICD' : p.pacemaker_mode) + ' ' + LEAD_KO[p.pacemaker_lead] : ''}`; s.appendChild(o);
  });
  if (cur && $$('option', s).some(o => o.value === cur)) s.value = cur;
  if (curRow < 0 && s.options.length) { selectRow(Number(s.options[0].value)); }
}
$('#patSearch').addEventListener('input', loadPatientList);
$('#selPatient').addEventListener('change', () => selectRow(Number($('#selPatient').value)));
async function selectRow(row) {
  curRow = row; const p = patList.find(x => x.row === row); curPid = p ? p.id : null; curPaced = (p && p.pacemaker) ? ` · ⚡ ${p.pacemaker_type === 'icd' ? 'ICD (백업 페이싱만)' : p.pacemaker_mode + ' ' + (LEAD_KO[p.pacemaker_lead] || '')}` : '';
  $('#selPatient').value = row; syncDropdowns();
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ row })); else connectWs();
  Object.values(waves).forEach(resetWave);
  loadPatCard(); if (trendOpen) loadTrend();
}
async function loadPatCard() {
  if (!curPid) return;
  try {
    const p = await api('/emr/patients/' + curPid); const r = p.runtime || {}; const a = p.admission || {};
    applySwitches(r);
    const chs = new Set(r.channels || []); if (r.channels) { $('#boxPpg').style.display = chs.has('ppg') ? '' : 'none'; $('#boxResp').style.display = chs.has('resp_wave') ? '' : 'none'; $('#boxAcc').style.display = chs.has('accel') ? '' : 'none'; }
    $('#patInfo').innerHTML = `<div class="patbody"><div class="patient"><img src="/api/v1/emr/patients/${p.id}/avatar.svg" alt=""><div class="info"><b>${p.name}</b> <span class="sub">${p.sex === 'M' ? '남' : '여'} ${p.age}세 · ${p.nationality_label} · ${p.weight_kg}kg</span>
      <div>${p.disease} (${p.icd10})</div><div>환자번호 #${r.patient_no ?? '-'} · ${p.mrn} · ${a.ward_name || ''} ${a.bed || ''}</div></div></div>
      <table class="info" style="margin-top:8px"><tr><td>현재 리듬</td><td>${r.rhythm_now || '-'} ${r.episode ? '<span class="tag warn">에피소드</span>' : ''}</td></tr>
      <tr><td>기저 리듬</td><td>${META && META.rhythms[p.rhythm] ? META.rhythms[p.rhythm].label : p.rhythm}</td></tr>
      <tr><td>페이스메이커</td><td>${pmSummary(p.pacemaker_info)}</td></tr>
      <tr><td>기기 세트</td><td>${devChips(p, r)}</td></tr>
      <tr><td>전송 채널</td><td class="mono" style="font-size:11px">${(r.channels || []).join(', ') || '-'}</td></tr>
      <tr><td>활동/자세</td><td>${r.activity} / ${r.posture} ${r.note ? '· ' + r.note : ''}</td></tr>
      ${r.trip ? `<tr><td>이동 상태</td><td><b>${esc(r.trip.stage)}</b> · 단계 ${mmss(r.trip.stage_remaining)} 남음 · 전체 ${mmss(r.trip.total_remaining)} 남음 · <span class="sub">${esc(r.note || '')}</span></td></tr>
      <tr><td>현재 동선</td><td><ol class="route">${(r.trip.steps || []).map(st => `<li class="${st.state}"><span class="rt">${(st.start || '').slice(11, 19)}</span><span class="rl">${esc(st.label)}</span><span class="rd">${st.state === 'current' ? mmss(st.remaining) + ' 남음' : (st.dur ? mmss(st.dur) : '')}</span></li>`).join('')}</ol></td></tr>` : ''}
      ${r.settling_remaining > 0 ? `<tr><td>전극 상태</td><td><span class="tag warn">부착 후 안정화 중 · ${Math.ceil(r.settling_remaining)}초</span></td></tr>` : ''}
      <tr><td>입원 병실</td><td><b>${r.home_where || ''}</b> · ${a.ward_name || '-'} · ${r.home_room ? `병실 ${r.home_room}` : '-'} · 침상 ${r.bed || a.bed || '-'}</td></tr>
      <tr><td>현재 위치</td><td>${r.trip ? `<b>${r.location_where || ''} ${esc(r.location_name || '')}</b> <span class="sub">${r.location || ''}</span> · ${esc(r.trip.stage)} <span class="tag warn">이동 중</span>` : `${r.location_where || ''} 입원 병실 내 <span class="sub">${r.location || ''}</span>`} ${r.shadow ? '<span class="tag err">음영</span>' : ''}</td></tr>
      <tr><td>게이트웨이</td><td>${r.gateway || '<span class="tag err">연결 없음</span>'} · RSSI ${r.rssi} dBm</td></tr>
      <tr><td>패치</td><td>${r.patch} (id ${r.patch_id}) · ${battIcon(r.battery, !!(r.flags & 0x20))} ${r.lead_off ? '<span class="tag err">LEAD OFF</span>' : '<span class="tag ok">부착</span>'} ${r.spo2_off ? '<span class="tag warn">SpO2 센서 분리</span>' : ''}</td></tr>
      <tr><td>담당</td><td>${r.doctor ? r.doctor.name + ' ' + r.doctor.title : '-'} / ${r.nurse ? r.nurse.name + ' 간호사' : '-'}</td></tr>
      <tr><td>체온/혈당/호흡 프로필</td><td>${p.temp_profile} / ${p.glucose_profile} / ${p.resp_kind}</td></tr>
      <tr><td>검사 일정</td><td>${(a.exams || []).map(e => `${e.time.slice(5, 16)} ${e.type}${e.done ? ' ✓' : ''}`).join('<br>') || '-'}</td></tr></table></div>`;
  } catch (e) { }
}

// ---------------------------------------------------------------- patients tab (sortable, 2-line rows, 10 per page)
let patPage = 0, patSort = { key: 'id', dir: 1 }, patCache = { key: '', items: [] };
try { const ps = JSON.parse(localStorage.getItem('patSort') || 'null'); if (ps && ps.key) patSort = ps; } catch (e) { }
const PAGE = 10;
async function loadPatients(force = false) {
  const st = $('#patStatus').value, q = $('#patQ').value.trim(), ck = st + '|' + q;
  if (force || patCache.key !== ck) {
    const r = await api(`/emr/patients?status=${st}&q=${encodeURIComponent(q)}&offset=0&limit=20000`);
    patCache = { key: ck, items: r.patients.map(p => ({ ...p, devn: (p.devices || []).length, rhythm_label: p.rhythm_label || (META && META.rhythms[p.rhythm] ? META.rhythms[p.rhythm].label : p.rhythm),
      bed: p.bed || (p.admission ? p.admission.bed : '') || '', gateway: p.gateway || '', activity: p.activity || p.status || '', battery: p.battery ?? -1, rssi: p.rssi ?? -999, patch_id: (p.patch_id ?? (p.admission ? p.admission.patch_id : 0)) || 0, patient_no: p.patient_no ?? (p.admission ? p.admission.patient_no : null) ?? -1, ward: p.ward || (p.admission ? p.admission.ward_name : '') || '', specialty: p.specialty || p.ward_specialty || '' })) };
  }
  const items = patCache.items.slice();
  const k = patSort.key, d = patSort.dir;
  items.sort((a, b) => { const x = a[k], y = b[k]; if (x === y) return a.id - b.id; if (typeof x === 'number' && typeof y === 'number') return (x - y) * d; return String(x).localeCompare(String(y), 'ko') * d; });
  const pages = Math.max(1, Math.ceil(items.length / PAGE)); patPage = Math.min(patPage, pages - 1);
  $('#patCount').textContent = `총 ${fmt(items.length)}명 · ${patPage * PAGE + 1}-${Math.min(items.length, (patPage + 1) * PAGE)}`;
  $$('#patHead button').forEach(b => { b.classList.toggle('on', b.dataset.sort === k); b.classList.toggle('desc', b.dataset.sort === k && d < 0); });
  $('#patBody').innerHTML = items.slice(patPage * PAGE, (patPage + 1) * PAGE).map(p => `<div class="prow" data-id="${p.id}" data-row="${p.row ?? -1}">
    <div class="who"><img src="/api/v1/emr/patients/${p.id}/avatar.svg" loading="lazy" alt=""><div><div class="l1">${esc(p.name)} <span class="sub">${p.sex}/${p.age}</span></div><div class="l2">환자번호 #${p.patient_no ?? '-'} · 프로필 #${p.id} · ${esc(p.mrn || '')}</div></div></div>
    <div><div class="l1">${esc(p.disease)}${p.pacemaker ? ` <span class="tag warn">⚡${(p.pacemaker_type || (p.pacemaker_info && p.pacemaker_info.type)) === 'icd' ? 'ICD' : (p.pacemaker_mode || (p.pacemaker_info && p.pacemaker_info.mode) || 'PM')}</span>` : ''}</div><div class="l2">${esc(p.rhythm_label)}</div></div>
    <div><div class="l1">${esc(p.bed || '-')}${p.ward ? ` <span class="sub">${esc(p.ward)}</span>` : ''}</div><div class="l2">${esc(p.specialty || '')}${p.specialty ? ' · ' : ''}${esc(p.gateway || (p.status === 'admitted' || p.status === 'outpatient' ? '연결 없음' : p.status || '-'))}</div></div>
    <div><div class="l1">${p.patch ? `${esc(p.patch)} <span class="sub">#${p.patch_id}</span>` : '-'}</div><div class="l2">${(p.devices || []).filter(x => x !== 'ecg_patch').map(x => DEV && DEV.devices[x] ? DEV.devices[x].short : x).join(' · ') || (p.devices ? 'ECG만' : '-')}</div></div>
    <div><div class="l1">${esc(p.activity || '-')}${p.note ? ` <span class="sub">${esc(p.note)}</span>` : ''}</div><div class="l2">${p.battery >= 0 ? battIcon(p.battery) : '-'} · RSSI ${p.rssi > -999 ? p.rssi : '-'}</div></div>
  </div>`).join('') || '<div class="dd-empty">환자 없음</div>';
  // pagination: 1 … p-2 p-1 [p] p+1 p+2 … N
  // fixed-width pager: always 7 slots (first … p-1 p p+1 … last) so prev/next never move
  const pg = $('#patPager');
  let slots = [];
  if (pages <= 7) { slots = Array.from({ length: pages }, (_, i) => i); while (slots.length < 7) slots.push(null); }
  else {
    const lo = Math.max(1, Math.min(patPage - 1, pages - 4)), hi = lo + 2;   // 3 middle pages
    slots = [0, lo > 1 ? '…' : 1, lo, lo + 1, hi, hi < pages - 2 ? '…' : pages - 2, pages - 1];
    if (lo === 1) slots = [0, 1, 2, 3, 4, '…', pages - 1];
    if (hi >= pages - 2) slots = [0, '…', pages - 5, pages - 4, pages - 3, pages - 2, pages - 1];
  }
  let html = `<button class="small nav" data-pg="${patPage - 1}" ${patPage === 0 ? 'disabled' : ''}>◀ 이전</button><span class="slots">`;
  slots.forEach(i => { html += (i === null) ? '<span class="slot"></span>' : (i === '…') ? '<span class="slot">…</span>' : `<button class="small slot ${i === patPage ? 'on' : ''}" data-pg="${i}">${i + 1}</button>`; });
  html += `</span><button class="small nav" data-pg="${patPage + 1}" ${patPage >= pages - 1 ? 'disabled' : ''}>다음 ▶</button>`;
  pg.innerHTML = html;
}
$('#patStatus').onchange = $('#patQ').oninput = () => { patPage = 0; loadPatients(); };
$('#patPager').addEventListener('click', e => { const b = e.target.closest('button[data-pg]'); if (b && !b.disabled) { patPage = Number(b.dataset.pg); loadPatients(); } });
$('#patHead').addEventListener('click', e => { const b = e.target.closest('button[data-sort]'); if (!b) return; if (patSort.key === b.dataset.sort) patSort.dir *= -1; else patSort = { key: b.dataset.sort, dir: 1 }; try { localStorage.setItem('patSort', JSON.stringify(patSort)); } catch (e) { } patPage = 0; loadPatients(); });
setInterval(() => { if ($('[data-tab="pat"]').classList.contains('on')) loadPatients(true); }, 10000);
$('#patBody').addEventListener('click', e => { const tr = e.target.closest('.prow[data-id]'); if (tr) showPatientDetail(Number(tr.dataset.id)); });
async function showPatientDetail(id) {                          // detail card for one patient (table row click, plan marker, moving list)
  $$('.prow', $('#patBody')).forEach(x => x.classList.toggle('sel', Number(x.dataset.id) === id));
  const p = await api('/emr/patients/' + id); const r = p.runtime; const a = p.admission;
  $('#patDetail').innerHTML = `<h3>환자 상세</h3><div class="patient"><img src="/api/v1/emr/patients/${p.id}/avatar.svg"><div class="info"><b>${p.name}</b> <span class="sub">${p.sex === 'M' ? '남' : '여'} ${p.age}세 (${p.birth_date}) · ${p.nationality_label}</span><div>${a && a.patient_no ? `환자번호 #${a.patient_no} · ` : ''}${p.mrn} · 혈액형 ${p.blood_type} · ${p.height_cm}cm ${p.weight_kg}kg BMI ${p.bmi}</div><div>알레르기: ${p.allergies} · 동반질환: ${p.comorbidities.join(', ') || '없음'}</div></div></div>
    <table style="margin-top:8px"><tr><td>주진단</td><td>${p.disease} (${p.icd10}) · ${p.ward_specialty}</td></tr><tr><td>페이스메이커</td><td>${pmSummary(p.pacemaker_info)}</td></tr>${r ? `<tr><td>기기 세트</td><td>${devChips(p, r)}</td></tr>` : ''}<tr><td>기저 리듬</td><td>${META.rhythms[p.rhythm].label}</td></tr>
    <tr><td>상태</td><td>${p.status}</td></tr>${a ? `<tr><td>입원</td><td>${a.time} · ${a.ward_name} ${a.bed || ''} · 담당의 ${a.doctor} · 간호사 ${a.nurse} · 패치 ${a.patch}</td></tr><tr><td>검사</td><td>${(a.exams || []).map(e => `${e.time} ${e.type} (${e.room}, ${e.duration_min}분${e.patch_policy === 'remove' ? ', 패치 분리' : ''})${e.done ? ' ✓' : ''}`).join('<br>') || '-'}</td></tr>` : ''}
    ${r ? `<tr><td>입원 병실</td><td><b>${r.home_where || ''}</b> · ${a ? a.ward_name : ''} · ${r.home_room ? `병실 ${r.home_room}` : '-'} · 침상 ${r.bed || (a && a.bed) || '-'}</td></tr>
    <tr><td>현재 위치</td><td>${r.trip ? `<b>${r.location_where || ''} ${esc(r.location_name || '')}</b> <span class="sub">${r.location || ''}</span> · ${esc(r.trip.stage)} <span class="tag warn">이동 중</span>` : `${r.location_where || ''} 입원 병실 내 <span class="sub">${r.location || ''}</span>`} ${r.shadow ? '<span class="tag err">음영</span>' : ''} · GW ${r.gateway || '<span class="tag err">없음</span>'} · RSSI ${r.rssi}</td></tr>
    ${r.trip ? `<tr><td>이동 상태</td><td><b>${esc(r.note || '')}</b> · 단계 ${mmss(r.trip.stage_remaining)} 남음 · 전체 ${mmss(r.trip.total_remaining)} 남음</td></tr>
    <tr><td>현재 동선</td><td><ol class="route">${(r.trip.steps || []).map(st => `<li class="${st.state}"><span class="rt">${(st.start || '').slice(11, 19)}</span><span class="rl">${esc(st.label)}</span><span class="rd">${st.state === 'current' ? mmss(st.remaining) + ' 남음' : (st.dur ? mmss(st.dur) : '')}</span></li>`).join('')}</ol></td></tr>` : ''}
    <tr><td>실시간</td><td>${r.activity} ${r.note} · ${battIcon(r.battery)} · ${r.lead_off ? 'LEAD OFF' : '부착'} · 현재 리듬 ${r.rhythm_now}</td></tr>` : ''}
    ${p.patch_history ? `<tr><td>패치 교체 이력</td><td>${p.patch_history.map(h => `${h.t} ${h.old} → ${h.new} (${h.reason})`).join('<br>')}</td></tr>` : ''}
    ${p.history ? `<tr><td>이력</td><td>${p.history.map(h => `${h.discharged} ${h.reason} (패치 ${h.patch_returned} 반납)`).join('<br>')}</td></tr>` : ''}</table>
    ${r ? `<div class="wavebox" style="margin-top:10px"><canvas class="wave" id="wDetail"></canvas><span class="lbl">ECG (mV) · 실시간</span><span class="val" id="vDetailHr" style="color:var(--ecg)"></span></div>
    <div class="nums" style="grid-template-columns:repeat(4,1fr)"><div class="num hr"><div class="v" id="dHr">--</div><div class="l">HR</div></div><div class="num spo2"><div class="v" id="dSpo2">--</div><div class="l">SpO2</div></div><div class="num resp"><div class="v" id="dResp">--</div><div class="l">RR</div></div><div class="num temp"><div class="v" id="dTemp">--</div><div class="l">체온</div></div></div>
    <div class="row tight" style="margin-top:8px"><button class="small" id="goLive">전체 신호 보기 (신호 탭)</button></div>` : ''}`;
  const gl = $('#goLive'); if (gl) gl.onclick = () => { showTab('sig'); loadPatientList().then(() => selectRow(r.row)); };
  if (r) { if (curRow !== r.row) selectRow(r.row); mkWave('wDetail', 'ecg', [-1.5, 2.0], CFG.signals.ecg_fs); }
}

// ---------------------------------------------------------------- hospital / map
let floorDepts = {};                                         // 'building:floor' -> [{side, spec}] (departments on that floor)
function deptText(b, f) { const d = floorDepts[`${b}:${f}`] || []; if (!d.length) return ''; const u = [...new Set(d.map(x => x.spec))]; return u.length === 1 ? `${u[0]} ${f}A·${f}B` : d.map(x => `${f}${x.side} ${x.spec}`).join(' / '); }
async function loadFloors() {
  const fm = $('#floorMap'); if (fm) delete fm.dataset.staticKey;                   // new layout: static plan layer must be rebuilt
  const r = await api('/emr/floors'); floors = r.floors; const s = $('#selFloor'); s.innerHTML = '';
  try { const wr = await api('/emr/wards'); floorDepts = {}; wr.wards.forEach(w => { const k = `${w.building_idx}:${w.floor}`; (floorDepts[k] = floorDepts[k] || []).push({ side: (w.id || '').slice(-1), spec: w.specialty }); }); } catch (e) { floorDepts = {}; }
  floors.forEach((f, i) => { const o = document.createElement('option'); o.value = i; o.textContent = `${f.building} ${f.floor}층 · ${f.kind === 'ward' ? deptText(f.building_idx, f.floor) || '병동' : (f.name || '검사/응급')}`; s.appendChild(o); });
  const firstWard = floors.findIndex(f => f.kind === 'ward'); if (firstWard > 0) s.value = firstWard;
  try {
    const h = await api('/emr/hospital'); const sz = h.sizing || {};
    $('#hospTitle').innerHTML = `${esc(h.name)} · ${esc(h.template_name || h.template)} · ${h.n_beds}병상 · ${h.buildings.length}동 ${h.n_floors}층 · GW ${h.n_gateways}` +
      (sz.size_by_patients ? ` <span class="sub">(환자 ${sz.active_patients}명 · 여유 ${sz.headroom_pct}%)</span>` : '') +
      (sz.stale ? ` <span class="tag warn" title="시나리오 환자 수가 바뀌었습니다. 템플릿 재생성 또는 병원·환자 재구성으로 규모를 맞추세요">규모 불일치: 재생성 필요 (계획 ${sz.planned_beds}병상)</span>` : '');
    const hi = $('#hSizeInfo'); if (hi) hi.textContent = sz.size_by_patients ? `현재 병원 ${sz.beds}병상 · 환자 ${sz.active_patients}명 기준 계획 ${sz.planned_beds}병상${sz.stale ? ' → 병원·환자 재구성 또는 병원 탭 템플릿 재생성 필요' : ' (일치)'}` : `병상 상한 ${sz.beds} 그대로 생성`;
  } catch (e) { }
  syncDropdowns();
}
$('#selFloor').onchange = () => { mapHighlight = null; loadFloor(); drawElevation(); }; $('#mapMode').onchange = loadFloor; $('#gwFilter').onchange = loadGateways;
// ---- building elevation (side view): stacked floor slabs per building, click a floor to open its plan on the left
let elevStats = {};
// elevation drawing box = same height as the (square) plan box; the drawing itself keeps a fixed scale so a 1-building,
// 9-floor hospital is drawn with the same floor height / font size as a 3-tower, 20-floor one (scale capped, never stretched)
let elevDims = null;
function syncElevationSize(W, H) {
  if (W && H) elevDims = [W, H];
  const wrap = $('.elevwrap'), plan = $('.planwrap'), svg = $('#elevation'); if (!wrap || !plan || !svg) return;
  const ph = plan.getBoundingClientRect().height; if (ph > 50) wrap.style.height = ph + 'px';
  if (!elevDims) return;
  const [w, h] = elevDims; const cs = getComputedStyle(wrap); const availW = wrap.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight), availH = wrap.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
  const scale = Math.min(availW / w, availH / h, 2.2);             // fill the box height; cap keeps a 5-floor clinic from ballooning (2.2 px/unit -> floor ~59 px, floor number ~26 px)
  svg.style.width = Math.max(60, Math.floor(w * scale)) + 'px'; svg.style.height = Math.max(40, Math.floor(h * scale)) + 'px';
}
window.addEventListener('resize', () => syncElevationSize());
async function drawElevation() {
  if (!floors.length) return;
  try {
    const [wr, gr] = await Promise.all([api('/emr/wards'), api('/emr/gateways')]);
    elevStats = {};
    wr.wards.forEach(w => { const k = `${w.building_idx}:${w.floor}`; const e = elevStats[k] = elevStats[k] || { beds: 0, occ: 0, down: 0 }; e.beds += w.n_beds; e.occ += w.occupied; });
    gr.gateways.forEach(g => { if (g.status === 2 && g.type !== 'mobile') { const k = `${g.building_idx !== undefined ? g.building_idx : ''}:${g.floor}`; } });
    gr.gateways.forEach(g => { if (g.status === 2 && g.type !== 'mobile') { const b = floors.find(f => f.building === g.building && f.floor === g.floor); if (b) { const k = `${b.building_idx}:${b.floor}`; (elevStats[k] = elevStats[k] || { beds: 0, occ: 0, down: 0 }).down++; } } });
  } catch (e) { }
  const sel = Number($('#selFloor').value);
  const byB = {}; floors.forEach((f, i) => { (byB[f.building_idx] = byB[f.building_idx] || { name: f.building, floors: [] }).floors.push({ ...f, i }); });
  const blds = Object.entries(byB).sort((a, b) => a[0] - b[0]);
  const FH = 27.0, GAP = 6, BW = 52, maxF = Math.max(...blds.map(([, b]) => Math.max(...b.floors.map(f => f.floor))));
  const W = blds.length * (BW + GAP) + GAP, H = (maxF + 1) * FH + 18;
  const svg = $('#elevation'); svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  syncElevationSize(W, H);
  const th = TH();
  let out = `<rect x="0" y="${H - 12}" width="${W}" height="12" fill="#d9d2c5"/><line x1="0" y1="${H - 12}" x2="${W}" y2="${H - 12}" stroke="#7a6a55" stroke-width="0.6"/>`;
  blds.forEach(([bi, b], k) => {
    const x = GAP + k * (BW + GAP); const top = b.floors.reduce((m, f) => Math.max(m, f.floor), 1);
    const groundY = H - 12;
    // building shell (floors 1..top, unlisted floors drawn as plain slabs)
    for (let fno = 1; fno <= top; fno++) {
      const f = b.floors.find(x => x.floor === fno); const y = groundY - fno * FH;
      const st = f ? (elevStats[`${bi}:${fno}`] || { beds: 0, occ: 0, down: 0 }) : null;
      const cls = f ? `fl ${f.kind !== 'ward' ? 'diag' : ''} ${f.i === sel ? 'on' : ''}` : '';
      out += `<g class="${cls}" ${f ? `data-fi="${f.i}"` : ''}><rect x="${x}" y="${y}" width="${BW}" height="${FH}" ${f ? '' : 'fill="#efefef" stroke="#999" stroke-width="0.2"'}/>`;
      if (f) {
        out += `<text x="${x + 1.5}" y="${y + 18.5}" font-size="12" font-weight="700" fill="#223">${fno}F</text>`;
        if (f.kind === 'ward') {
          // right column: department(s) on top, occupied/available beds underneath
          const d = floorDepts[`${bi}:${fno}`] || []; const u = [...new Set(d.map(q => q.spec))];
          if (u.length > 1) u.forEach((t, k) => { out += `<text x="${x + BW - 1.5}" y="${y + 7.5 + k * 5}" font-size="4.2" fill="#556" text-anchor="end">${esc(t)}</text>`; });
          else out += `<text x="${x + BW - 1.5}" y="${y + 11}" font-size="4.8" fill="#556" text-anchor="end">${esc(u[0] || '')}</text>`;
          out += `<text x="${x + BW - 1.5}" y="${y + 21.5}" font-size="6.5" fill="#445" text-anchor="end">${st.occ}/${st.beds}</text>`;
        } else out += `<text x="${x + BW - 1.5}" y="${y + 18.5}" font-size="6" fill="#445" text-anchor="end">${f.kind === 'diagnostic' ? '검사·응급' : '로비'}</text>`;
        if (st.down) out += `<circle cx="${x + BW - 4}" cy="${y + FH - 3.5}" r="2" fill="${th.err}"><title>게이트웨이 장애 ${st.down}대</title></circle>`;
        out += `<title>${b.name} ${fno}층 · ${f.name || ''}${f.kind === 'ward' ? ` · 재원 ${st.occ}/${st.beds}` : ''}</title>`;
      } else out += `<text x="${x + 1.5}" y="${y + 17}" font-size="7.5" fill="#888">${fno}F</text>`;
      out += '</g>';
    }
    out += `<rect x="${x - 0.8}" y="${groundY - top * FH - 2.4}" width="${BW + 1.6}" height="2.4" fill="#556" rx="0.4"/>`;
    out += `<rect x="${x + BW / 2 - 6}" y="${groundY - top * FH - 6.5}" width="12" height="4.2" fill="#778"/>`;
    out += `<text x="${x + BW / 2}" y="${groundY - top * FH - 9}" font-size="7" font-weight="700" text-anchor="middle" fill="#223">${esc(b.name)}</text>`;
  });
  svg.innerHTML = out;
  const cur = floors[sel]; const st = cur ? (elevStats[`${cur.building_idx}:${cur.floor}`] || {}) : {};
  $('#elevInfo').textContent = cur ? `${cur.building} ${cur.floor}층 · ${cur.name || ''}${cur.kind === 'ward' ? ` · 병상 ${st.beds || 0} · 재원 ${st.occ || 0}` : ''}${st.down ? ` · GW 장애 ${st.down}` : ''}` : '';
}
$('#elevation').addEventListener('click', e => { const g = e.target.closest('.fl[data-fi]'); if (!g) return; mapHighlight = null; $('#selFloor').value = g.dataset.fi; syncDropdowns(); loadFloor(); drawElevation(); });
const GWCOL = () => [TH().acc, TH().warn, TH().err];
const KIND_FILL = { room: '#dbe9f6', exam: '#e3f0d8', nurse_station: '#fde3b8', toilet: '#e6def7', shower: '#e6def7', elevator: '#d8d8d8', stairs: '#cfcfcf', lobby: '#f7efd6', er: '#f9d7d7',
  utility: '#ececec', staff: '#f2e6d0', lounge: '#f7efd6', waiting: '#f7efd6', reception: '#fde3b8', control: '#e0e0e0', equipment: '#dadada', prep: '#eee9d8', recovery: '#e3f0d8',
  storage: '#e8e8e8', courtyard: '#dbf1d7', office: '#f0ece0', isolation: '#f9d7d7' };
const KIND_KO = { room: '병실', exam: '검사/진료실', nurse_station: '간호사실', toilet: '화장실', shower: '샤워실', elevator: '엘리베이터', stairs: '계단', lobby: '로비', er: '응급실', utility: '처치/투약', staff: '직원', lounge: '휴게', waiting: '대기', reception: '접수/원무', control: '조정실', equipment: '장비실', prep: '준비/탈의', recovery: '회복실', storage: '창고', courtyard: '중정', office: '사무' };
const KIND_ABBR = { nurse_station: 'NS', toilet: 'WC', shower: 'SH', elevator: 'EV', stairs: 'ST', utility: 'UT', storage: 'ST', control: 'CTRL', equipment: 'EQ', prep: 'PR', staff: 'STF', waiting: 'WAIT', office: 'OFF' };
// fit a label inside a room: try font sizes 0.85 -> 0.5 m and wrap into up to 3 lines (at spaces, else by characters);
// returns {fs, lines} or null when even the smallest size cannot fit
function textWidth(str, fs) { let w = 0; for (const ch of str) w += (/[\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF\u4E00-\u9FFF\u3040-\u30FF]/.test(ch) ? 0.98 : /[A-Z0-9#]/.test(ch) ? 0.66 : ch === ' ' ? 0.32 : 0.56) * fs; return w; }
function wrapText(name, maxW, fs) {
  const words = name.split(' '); const lines = []; let cur = '';
  const pushWord = (wd) => {
    if (textWidth(wd, fs) <= maxW) { lines.push(wd); return; }
    let chunk = ''; for (const ch of wd) { if (textWidth(chunk + ch, fs) > maxW && chunk) { lines.push(chunk); chunk = ch; } else chunk += ch; } if (chunk) lines.push(chunk);
  };
  for (const wd of words) {
    const t = cur ? cur + ' ' + wd : wd;
    if (textWidth(t, fs) <= maxW) cur = t; else { if (cur) lines.push(cur); cur = ''; if (textWidth(wd, fs) <= maxW) cur = wd; else pushWord(wd); }
  }
  if (cur) lines.push(cur);
  return lines;
}
function fitLabel(name, w, h, maxLines = 3, sizes = [0.85, 0.75, 0.66, 0.58, 0.5]) {
  const padW = 0.5, padH = 0.35;
  for (const fs of sizes) {
    const lines = wrapText(name, w - padW, fs);
    if (lines.length <= maxLines && lines.length * fs * 1.15 <= h - padH && lines.every(l => textWidth(l, fs) <= w - padW)) return { fs, lines };
  }
  return null;
}
function labelSvg(cx, cy, fit, cls = 'lbl') {
  const lh = fit.fs * 1.15, y0 = cy - (fit.lines.length - 1) * lh / 2 + fit.fs * 0.35;
  return `<text class="${cls}" x="${cx}" y="${y0}" style="font-size:${fit.fs}px">${fit.lines.map((l, i) => `<tspan x="${cx}" ${i ? `dy="${lh}"` : ''}>${esc(l)}</tspan>`).join('')}</text>`;
}
// short labels for small rooms: '외래 진료실 3' -> '진료실 3', '행정 사무실 2' -> '사무 2', '외래 대기 1' -> '대기 1'
function shortName(r) {
  const n = r.name || '';
  let m = n.match(/^외래 진료실\s*(\d+)/); if (m) return `진료실#${m[1]}`;
  m = n.match(/^행정 사무실\s*(\d+)/); if (m) return `사무실#${m[1]}`;
  m = n.match(/^외래 대기\s*(\d+)/); if (m) return `대기#${m[1]}`;
  m = n.match(/^(X-ray 조정|CT 조정실|CT 준비실|CT 장비실|MRI 조정실|MRI 장비실|탈의실|영상 판독실|조영실 조정)/); if (m) return { 'X-ray 조정': 'XR조정', 'CT 조정실': 'CT조정', 'CT 준비실': 'CT준비', 'CT 장비실': 'CT장비', 'MRI 조정실': 'MRI조정', 'MRI 장비실': 'MRI장비', '탈의실': '탈의', '영상 판독실': '판독', '조영실 조정': '조영조정' }[m[1]];
  if (KIND_ABBR[r.kind] && !/[가-힣]/.test(n.slice(0, 1)) ) return KIND_ABBR[r.kind];
  if (KIND_ABBR[r.kind] && r.kind !== 'exam') return KIND_ABBR[r.kind];
  return n.replace(/\s+/g, '').slice(0, 4);
}
const pts = (poly) => poly.map(p => p[0].toFixed(2) + ',' + p[1].toFixed(2)).join(' ');
function ptIn(x, y, poly) { let inside = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside; } return inside; }
const bbox = (poly) => { const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]); return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]; };
function bedIcon(b, cls, title) {
  return `<g class="bed ${cls}" transform="translate(${b.x},${b.y}) rotate(${b.angle || 0})"><title>${esc(title || b.id)}</title><rect x="-0.45" y="-1.0" width="0.9" height="2.0" rx="0.15"/><rect class="pillow" x="-0.45" y="-1.0" width="0.9" height="0.4"/></g>`;
}
function displayIcon(f) {
  return `<g class="disp ${f.subtype}" transform="translate(${f.x},${f.y}) rotate(${f.angle || 0})"><title>${esc(f.label || '전광판')} (${f.subtype})</title><rect x="-0.8" y="-0.45" width="1.6" height="0.9" rx="0.1"/><rect class="scr" x="-0.68" y="-0.35" width="1.36" height="0.7"/><rect x="-0.12" y="0.45" width="0.24" height="0.3"/><polyline class="wave" points="-0.55,0 -0.4,0 -0.3,-0.22 -0.2,0.22 -0.1,0 0.15,0 0.25,-0.15 0.35,0.15 0.45,0"/></g>`;
}
async function loadFloor() {
  if (!floors.length) await loadFloors();
  const f = floors[Number($('#selFloor').value)]; if (!f) return;
  const m = await api(`/emr/floors/${f.building_idx}/${f.floor}`);
  const mode = $('#mapMode').value, th = TH();
  const W = m.width, D = m.depth;
  const svg = $('#floorMap'); const base = [-0.5, -0.5, W + 1, D + 1];
  const sameFloor = planZoom.base && planZoom.base[2] === base[2] && planZoom.base[3] === base[3] && svg.dataset.floorKey === `${f.building_idx}:${f.floor}`;
  planZoom.base = base; svg.dataset.floorKey = `${f.building_idx}:${f.floor}`;
  if (!(sameFloor && planZoom.on)) { planSetVB(base.slice()); planZoom.on = false; svg.style.cursor = 'zoom-in'; } else planSetVB(planZoom.vb);
  let out = `<defs><pattern id="hatch" width="0.8" height="0.8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="0.8" stroke="#888" stroke-width="0.12"/></pattern>
    <pattern id="grass" width="1.2" height="1.2" patternUnits="userSpaceOnUse"><circle cx="0.6" cy="0.6" r="0.12" fill="#9fcf9a"/></pattern>
    <filter id="covblur" x="-15%" y="-15%" width="130%" height="130%" filterUnits="objectBoundingBox"><feGaussianBlur stdDeviation="0.45"/></filter>
    <pattern id="covhatch" width="0.5" height="0.5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><path d="M0,0.25 H0.5 M0.25,0 V0.5" stroke="#4a5560" stroke-width="0.04" stroke-opacity="0.7"/></pattern></defs>
    <style>
      .wall{fill:none;stroke:#2b3540;stroke-width:0.22;stroke-linejoin:round}
      .corr{fill:#f3f4f6;stroke:#c9ced6;stroke-width:0.06}
      .lbl{font-size:0.85px;fill:#1c2630;text-anchor:middle;pointer-events:none;paint-order:stroke;stroke:#fff;stroke-width:0.22;stroke-linejoin:round}
      .lbl.small{font-size:0.62px;fill:#5a6672}
      .lbl.start{text-anchor:start}
      .bed rect{fill:#fff;stroke:#6a8;stroke-width:0.08}.bed .pillow{fill:#6a8;stroke:none}
      .bed.occ rect{stroke:#2b7fc2}.bed.occ .pillow{fill:#2b7fc2}
      .bed.lead rect{stroke:#d98a00}.bed.lead .pillow{fill:#d98a00}
      .bed.lost rect{stroke:#d33}.bed.lost .pillow{fill:#d33}
      .bed.er rect{stroke:#c66}.bed.er .pillow{fill:#c66}
      .disp rect{fill:#1b2733;stroke:#0aa8e6;stroke-width:0.06}.disp .scr{fill:#0d1a26;stroke:none}.disp .wave{fill:none;stroke:#3ddc97;stroke-width:0.07}
      .desk{fill:#e9b96e;stroke:#a86f1a;stroke-width:0.08}
      .gw.dim{opacity:.35}
      .pat{stroke:#fff;stroke-width:0.1}
      .gwlbl{font-size:0.48px;fill:#3a4650;text-anchor:middle;paint-order:stroke;stroke:#fff;stroke-width:0.16}
      .plbl{font-size:0.52px;fill:#1c2630;text-anchor:start;paint-order:stroke;stroke:#fff;stroke-width:0.16;pointer-events:none}
      .pat{stroke:#fff;stroke-width:0.1;cursor:pointer}
      .pat .movring{fill:none;stroke-width:0.12;stroke-dasharray:0.32 0.18;animation:movspin 4s linear infinite}
      .pat.shadow .movring{stroke-dasharray:0.14 0.14}
      .plbl.stg{font-size:0.4px;fill:#5a6672}
      .plbl.dense{font-size:0.4px}
      .plbl.left{text-anchor:end}
      .plbl.clu{text-anchor:middle;fill:#fff;stroke:none;font-weight:700;font-size:0.46px}
      @keyframes movspin{to{stroke-dashoffset:-1}}
    </style>`;
  out += `<rect x="0" y="0" width="${W}" height="${D}" fill="#fbfcfd" stroke="#2b3540" stroke-width="0.35"/>`;
  m.corridors.forEach(c => { out += `<polygon class="corr" points="${pts(c.poly)}"><title>${esc(c.name || '복도')}</title></polygon>`; });
  // nursing-unit zones: each ward (department) is one tinted zone; its boundary crossing a corridor is a compartment door
  const ZONE_COL = ['#2b7fc2', '#2e9e6b', '#c2702b', '#8a4fc2'];
  let zoneLabels = '';
  const zoneCands = [];                                     // ward tags placed after gateways/fixtures are known
  (m.wards || []).forEach((wd, wi) => {
    const col = ZONE_COL[wi % ZONE_COL.length];
    (wd.zones || []).forEach(z => {
      out += `<polygon class="zone" points="${pts(z)}" fill="${col}" fill-opacity="0.07" stroke="${col}" stroke-opacity="0.55" stroke-width="0.14" stroke-dasharray="0.8 0.45" pointer-events="none"/>`;
    });
    // ward label: on the largest corridor inside the zone; the exact spot along the corridor is chosen later (zoneLabelsAt),
    // once the gateway markers and corridor fixtures (central displays, desks) are known, so the tag never covers them
    // corridor with the most of its long axis inside this ward's zone (a shared corridor may belong half to each ward)
    let best = null;
    m.corridors.forEach(c => { const [cx0, cy0, cx1, cy1] = bbox(c.poly); const horiz = (cx1 - cx0) >= (cy1 - cy0); const a = (cx1 - cx0) * (cy1 - cy0);
      const inside = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].filter(t => { const x = horiz ? cx0 + (cx1 - cx0) * t : (cx0 + cx1) / 2, y = horiz ? (cy0 + cy1) / 2 : cy0 + (cy1 - cy0) * t; return (wd.zones || []).some(z => ptIn(x, y, z)); });
      if (inside.length && (!best || inside.length > best.inside.length || (inside.length === best.inside.length && a > best.a))) best = { a, bb: [cx0, cy0, cx1, cy1], inside }; });
    if (best) zoneCands.push({ bb: best.bb, col, txt: `${wd.name}`, zones: wd.zones || [], inside: best.inside });
  });
  // occupancy lookup: bed id -> patient
  const patByBed = {}; (m.patients || []).forEach(p => { if (p.at_bed && p.bed) patByBed[p.bed] = p; });
  let labels = '';                                          // labels are drawn last so they sit in front of beds
  let bedsOut = '';                                         // beds carry occupancy/link state: dynamic layer
  let dynOut = '';                                          // moving patients + count badges: dynamic layer
  const gwPos = {};                                         // room idx -> gateway marker position chosen to avoid the label
  const gwFinal = [];                                       // every gateway marker actually drawn on this floor (x, y)
  let gwOut = '';                                           // gateway markers are appended after the patients so they stay on top (clickable in crowded rooms)
  let clusters = [];                                        // '+N' overflow markers of crowded rooms
  const roomLbl = {};                                       // room idx -> bbox of the ward-room name/occupancy label (patient markers dodge it)
  const roomGws = {}; (m.gateways || []).forEach(g => { (roomGws[g.room_idx] = roomGws[g.room_idx] || []).push(g); }); Object.values(roomGws).forEach(a => a.sort((p, q) => p.idx - q.idx));
  const prefsOf = (r) => (roomGws[r.idx] && roomGws[r.idx].length > 1) ? roomGws[r.idx].map(g => [g.x, g.y]) : [[r.cx, r.cy]];   // server spreads extra gateways along the long axis
  const bedRoom = {}; m.rooms.forEach(r => (r.beds || []).forEach(b => { bedRoom[b.id] = r.idx; }));
  const hit = (a, b) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
  // gateway marker footprint: outer arc (r 0.92 + stroke) to the left, disc r 0.3, '#no' label (0.48 px font) underneath
  const mkBox = (gx, gy) => [gx - 1.0, gy - 0.55, gx + 0.8, gy + 1.08];
  const rotBox = (x, y, hw, hh, angle, oy = 0) => {                                   // bbox of a rotated rect (+0.1 m clearance)
    const a = ((angle || 0) * Math.PI) / 180, c = Math.abs(Math.cos(a)), sn = Math.abs(Math.sin(a));
    const cx = x - Math.sin(a) * oy, cy = y + Math.cos(a) * oy, HW = hw * c + hh * sn, HH = hw * sn + hh * c;
    return [cx - HW - 0.1, cy - HH - 0.1, cx + HW + 0.1, cy + HH + 0.1];
  };
  const bedBox = (b) => rotBox(b.x, b.y, 0.45, 1.0, b.angle);
  const fixBox = (f) => f.type === 'display' ? rotBox(f.x, f.y, 0.8, 0.6, f.angle, 0.15) : (f.type === 'nurse_desk' || f.type === 'reception') ? rotBox(f.x, f.y, 1.5, 0.4, f.angle) : rotBox(f.x, f.y, 0.6, 0.6, f.angle);
  const allFix = (m.fixtures || []).map(f => ({ ...f, box: fixBox(f) }));
  const roomFixBoxes = (r) => allFix.filter(f => f.room === r.id).map(f => f.box);
  // place a gateway marker inside room bbox [x0,y0,x1,y1] clear of every obstacle box.  Starts at (px,py); when that hits
  // something, a 0.15 m grid search picks the closest free spot (toward the corridor door when `dir` is given).
  const placeGw = (r, px, py, obst, x0, y0, x1, y1, dir) => {
    const free = (gx, gy) => !obst.some(b => hit(mkBox(gx, gy), b));
    if (free(px, py)) return { x: px, y: py };
    let best = Infinity, gx = px, gy = py;
    for (let yy = y0 + 0.6; yy <= y1 - 1.1; yy += 0.15) for (let xx = x0 + 1.05; xx <= x1 - 0.85; xx += 0.15) {
      let cost;
      if (dir) { const ox = xx - r.cx, oy = yy - r.cy, along = ox * dir[0] + oy * dir[1], perp = Math.abs(ox * dir[1] - oy * dir[0]); cost = perp + (along >= 0 ? along * 0.6 : 3 - along * 2); }
      else cost = Math.hypot(xx - px, yy - py);
      if (cost < best && free(xx, yy)) { best = cost; gx = xx; gy = yy; }
    }
    return { x: gx, y: gy };
  };
  const doorDir = (r) => {                                                            // unit vector from the room centre to the nearest corridor
    let dx = 0, dy = 1, best = Infinity;
    (m.corridors || []).forEach(c => { const [cx0, cy0, cx1, cy1] = bbox(c.poly); const nx = Math.min(Math.max(r.cx, cx0), cx1), ny = Math.min(Math.max(r.cy, cy0), cy1); const d = Math.hypot(nx - r.cx, ny - r.cy); if (d < best && d > 0.01) { best = d; dx = (nx - r.cx) / d; dy = (ny - r.cy) / d; } });
    return [dx, dy];
  };
  m.rooms.forEach(r => {
    if (r.ensuite) return;                                   // in-room toilets are not drawn
    const fill = r.kind === 'courtyard' ? 'url(#grass)' : (KIND_FILL[r.kind] || '#eee');
    const [bx0, by0, bx1, by1] = bbox(r.poly); const occN = (r.beds || []).filter(b => patByBed[b.id]).length;
    out += `<polygon points="${pts(r.poly)}" fill="${fill}" stroke="none" data-rid="${esc(r.id)}"><title>${esc(r.name)} · ${KIND_KO[r.kind] || r.kind}${r.ward ? ' · ' + esc(r.ward) : ''} · ${(bx1 - bx0).toFixed(1)} × ${(by1 - by0).toFixed(1)} m (${((bx1 - bx0) * (by1 - by0)).toFixed(0)} m²)${r.beds && r.beds.length ? ` · ${r.beds.length}병상 · 재원 ${occN}` : ''}${r.gateway_idx >= 0 ? ' · 게이트웨이 있음' : ''}</title></polygon>`;
    if (r.kind === 'stairs') out += `<polygon points="${pts(r.poly)}" fill="url(#hatch)" stroke="none"/>`;
    out += `<polygon class="wall" points="${pts(r.poly)}"/>`;
    const [x0, y0, x1, y1] = bbox(r.poly); const w = x1 - x0, h = y1 - y0;
    if (r.kind === 'room' || r.kind === 'isolation') {
      const occ = (r.beds || []).filter(b => patByBed[b.id]).length;
      const txt = `${r.name} ${occ}/${(r.beds || []).length}`; const fit = fitLabel(txt, w, 1.6, 1, [0.85, 0.72, 0.6]);
      let lbl = null;                                                                // label box (for gateway collision)
      if (fit) {
        labels += `<text class="lbl start" x="${x0 + 0.35}" y="${y0 + 0.35 + fit.fs}" style="font-size:${fit.fs}px">${esc(r.name)} <tspan class="small">${occ}/${(r.beds || []).length}</tspan></text>`;
        lbl = [x0 + 0.2, y0 + 0.2, x0 + 0.35 + textWidth(txt, fit.fs) + 0.2, y0 + 0.35 + fit.fs * 1.15 + 0.2];
      } else {
        const fit2 = fitLabel(txt, w, Math.max(1.6, h * 0.45), 2, [0.72, 0.6, 0.5]);       // narrow (isolation) room: two lines along the top wall
        if (fit2) {
          const th2 = fit2.lines.length * fit2.fs * 1.15, lw2 = Math.max(...fit2.lines.map(l => textWidth(l, fit2.fs)));
          labels += labelSvg(r.cx, y0 + 0.35 + th2 / 2, fit2);
          lbl = [r.cx - lw2 / 2 - 0.2, y0 + 0.2, r.cx + lw2 / 2 + 0.2, y0 + 0.35 + th2 + 0.2];
        }
      }
      if (lbl) roomLbl[r.idx] = lbl;
      if (r.gateway_idx >= 0) {
        // ward room: ceiling gateway at the room centre; if a bed, monitor or the name label sits there, slide it toward the corridor door
        const obst = (r.beds || []).map(bedBox).concat(roomFixBoxes(r), lbl ? [lbl] : []);
        gwPos[r.idx] = prefsOf(r).map(([px, py]) => placeGw(r, px, py, obst, x0, y0, x1, y1, doorDir(r)));
      }
      (r.beds || []).forEach(b => { const p = patByBed[b.id]; bedsOut += bedIcon(b, p ? (p.gw < 0 || p.shadow ? 'lost' : p.lead_off ? 'lead' : 'occ') : '', p ? `${p.name} · ${b.id}` : b.id); });
    } else if (r.kind === 'er' || (r.name || '').startsWith('투석')) {
      if (w > 4) labels += `<text class="lbl" x="${r.cx}" y="${y0 + 1.0}">${esc(r.name)}</text>`;
      (r.beds || []).forEach(b => { bedsOut += bedIcon(b, 'er', b.id); });
      if (r.gateway_idx >= 0 && w > 2.2 && h > 2.2) {
        const lw = textWidth(r.name || '', 0.85), obst = (r.beds || []).map(bedBox).concat(roomFixBoxes(r), w > 4 ? [[r.cx - lw / 2 - 0.2, y0 + 0.1, r.cx + lw / 2 + 0.2, y0 + 1.25]] : []);
        gwPos[r.idx] = prefsOf(r).map(([px, py]) => placeGw(r, px, py, obst, x0, y0, x1, y1));
      }
    } else {
      const name = r.name || '';
      const numbered = /^(외래 진료실|행정 사무실|외래 대기)\s*\d+/.test(name);   // numbered filler rooms: '진료실#3' form
      const label = numbered ? shortName(r) : name;
      // icons/fixtures inside the room (displays, desks, elevator arrows, wheelchair) push the label into a free band
      const pts_ = (m.fixtures || []).filter(x => x.room === r.id).map(x => [x.x, x.y]);
      if (r.kind === 'elevator') pts_.push([r.cx, r.cy - 1.2]);
      if (r.kind === 'toilet') pts_.push([r.cx, r.cy + 1.2]);
      let fit = fitLabel(label, w, pts_.length ? Math.max(1.2, h * 0.5) : h);
      if (!fit && !numbered) fit = fitLabel(shortName(r), w, pts_.length ? Math.max(1.2, h * 0.5) : h, 2, [0.58, 0.5]);
      if (fit) {
        let ly = r.cy;
        if (pts_.length) {
          const th_ = fit.lines.length * fit.fs * 1.15;
          const cands = [r.cy, y1 - th_ / 2 - 0.35, y0 + th_ / 2 + 0.35, y0 + h * 0.5 + 0.9, y0 + h * 0.5 - 0.9];
          const clear = (yy) => pts_.every(([px, py]) => Math.abs(py - yy) > th_ / 2 + 0.55);
          ly = cands.find(clear); if (ly === undefined) ly = y1 - th_ / 2 - 0.35;
        }
        // gateway marker(s) at the room centre (best coverage); the name label moves above (or below) the marker.
        // Only when neither fits does the gateway get nudged to the nearest free spot instead.
        if (r.gateway_idx >= 0 && w > 2.2 && h > 2.2) {
          const lw = Math.max(...fit.lines.map(l => textWidth(l, fit.fs))), th_ = fit.lines.length * fit.fs * 1.15;
          const lblBox = (yy) => [r.cx - lw / 2 - 0.2, yy - th_ / 2 - 0.15, r.cx + lw / 2 + 0.2, yy + th_ / 2 + 0.15];
          const fixObst = roomFixBoxes(r);
          if (r.kind === 'elevator') fixObst.push([r.cx - 1.2, r.cy - 1.9, r.cx + 1.2, r.cy - 0.5]);
          if (r.kind === 'toilet') fixObst.push([r.cx - 0.5, r.cy + 0.5, r.cx + 0.5, r.cy + 1.5]);
          const prefs = prefsOf(r);
          const gwFree = (px, py) => !fixObst.some(b => hit(mkBox(px, py), b));
          const lblOk = (yy) => yy - th_ / 2 >= y0 + 0.3 && yy + th_ / 2 <= y1 - 0.3 && pts_.every(([, fy]) => Math.abs(fy - yy) > th_ / 2 + 0.55) && !prefs.some(([px, py]) => hit(mkBox(px, py), lblBox(yy)));
          let placed = null;
          if (prefs.every(([px, py]) => gwFree(px, py))) {
            if (lblOk(ly)) placed = prefs;
            else {
              const above = Math.min(...prefs.map(p => p[1])) - 0.55 - th_ / 2 - 0.2, below = Math.max(...prefs.map(p => p[1])) + 1.08 + th_ / 2 + 0.2;
              const yy = [above, below].find(lblOk);
              if (yy !== undefined) { ly = yy; placed = prefs; }
            }
          }
          if (!placed) placed = prefs.map(([px, py]) => { const q = placeGw(r, px, py, fixObst.concat([lblBox(ly)]), x0, y0, x1, y1); return [q.x, q.y]; });
          gwPos[r.idx] = placed.map(([px, py]) => ({ x: px, y: py }));
        }
        labels += labelSvg(r.cx, ly, fit);
        { const lw2 = Math.max(...fit.lines.map(l => textWidth(l, fit.fs))), th2 = fit.lines.length * fit.fs * 1.15; roomLbl[r.idx] = roomLbl[r.idx] || [r.cx - lw2 / 2 - 0.2, ly - th2 / 2 - 0.15, r.cx + lw2 / 2 + 0.2, ly + th2 / 2 + 0.15]; }   // patient markers dodge this label too
      }
      if (r.kind === 'elevator') out += `<g transform="translate(${r.cx},${r.cy - 1.2})"><rect x="-1.1" y="-0.6" width="2.2" height="1.2" rx="0.1" fill="none" stroke="#666" stroke-width="0.08"/><path d="M-0.5,0.3 L-0.5,-0.3 M-0.75,-0.05 L-0.5,-0.3 L-0.25,-0.05 M0.5,-0.3 L0.5,0.3 M0.25,0.05 L0.5,0.3 L0.75,0.05" fill="none" stroke="#666" stroke-width="0.09"/></g>`;
      if (r.kind === 'toilet') out += `<text class="lbl small" x="${r.cx}" y="${r.cy + 1.2}">♿</text>`;
    }
  });
  out += `<g class="labels">${labels}</g><g class="zones" id="zoneTags"></g><g class="covLayer"></g>`;
  // RF coverage context for gateway hover: every drawn room edge is a wall; corridors are open space
  covCtx = { W, D, segs: [], gws: {} };
  m.rooms.forEach(r => { if (r.ensuite || !r.poly) return; const q = r.poly; for (let i = 0; i < q.length; i++) { const a = q[i], b = q[(i + 1) % q.length]; if (Math.hypot(b[0] - a[0], b[1] - a[1]) > 0.05) covCtx.segs.push([a[0], a[1], b[0], b[1]]); } });
  if (mode !== 'plan') {
    (m.fixtures || []).forEach(x => {
      if (x.type === 'display') out += displayIcon(x);
      else if (x.type === 'nurse_desk' || x.type === 'reception') out += `<g transform="translate(${x.x},${x.y}) rotate(${x.angle || 0})"><rect class="desk" x="-1.5" y="-0.4" width="3.0" height="0.8" rx="0.15"><title>${esc(x.label || '')}</title></rect></g>`;
    });
    const gcol = GWCOL();
    const roomByIdx = {}; m.rooms.forEach(r => { if (r.idx !== undefined) roomByIdx[r.idx] = r; });
    (m.gateways || []).forEach(g => {
      const load = g.capacity ? (g.n_conn || 0) / g.capacity : 0;
      // ceiling gateway: drawn near the room's top-right corner so beds and labels stay readable
      let gx = g.x, gy = g.y; const rr = roomByIdx[g.room_idx];
      const slot = rr && roomGws[rr.idx] ? roomGws[rr.idx].findIndex(q => q.idx === g.idx) : 0;
      if (rr && gwPos[rr.idx]) { const p = gwPos[rr.idx][slot] || gwPos[rr.idx][0]; gx = p.x; gy = p.y; }
      else if (rr && rr.kind !== 'corridor' && rr.poly) {
        const [x0, y0, x1, y1] = bbox(rr.poly);
        if (x1 - x0 > 2.2 && y1 - y0 > 2.2) { const p = placeGw(rr, g.x, g.y, (rr.beds || []).map(bedBox).concat(roomFixBoxes(rr)), x0, y0, x1, y1); gx = p.x; gy = p.y; }
      } else {
        // corridor gateway: slide along the corridor axis until the marker is clear of wall displays
        const obst = allFix.map(f => f.box);
        if (obst.some(b => hit(mkBox(gx, gy), b))) {
          const c = (m.corridors || []).find(c => { const [a, b, c2, d] = bbox(c.poly); return gx >= a && gx <= c2 && gy >= b && gy <= d; });
          const horiz = !c || (bbox(c.poly)[2] - bbox(c.poly)[0]) >= (bbox(c.poly)[3] - bbox(c.poly)[1]);
          for (let k = 1; k <= 20; k++) {
            const d = (k % 2 ? 1 : -1) * Math.ceil(k / 2) * 0.3, px = horiz ? gx + d : gx, py = horiz ? gy : gy + d;
            if (!obst.some(b => hit(mkBox(px, py), b))) { gx = px; gy = py; break; }
          }
        }
      }
      const col = gcol[g.status] || '#888';
      covCtx.gws[g.idx] = { x: gx, y: gy };
      gwFinal.push({ x: gx, y: gy });                                   // final marker position: patient markers keep clear of it
      gwOut += `<g class="gw ${g.status === 2 ? 'down' : ''}" data-gwidx="${g.idx}" style="cursor:pointer"><circle cx="${gx}" cy="${gy}" r="0.3" fill="${load > 0 ? '#e9eef3' : col}" stroke="${col}" stroke-width="0.06"><title>${g.id} #${g.gw_no} · ${g.type} · 연결 ${g.n_conn}/${g.capacity} · CPU ${g.cpu}%${g.connected ? ' · TCP' : ''}</title></circle>
        ${[0.62, 0.92].map(rr => { const k = rr * 0.5, kx = rr * 0.866; return `<path d="M${(gx - kx).toFixed(3)},${(gy + k).toFixed(3)} A${rr},${rr} 0 0 1 ${(gx - kx).toFixed(3)},${(gy - k).toFixed(3)}" fill="none" stroke="${th.acc2}" stroke-width="0.11" stroke-linecap="round" opacity="0.95"/>`; }).join('')}
        ${load > 0 ? (() => { const R = 0.3, a = Math.min(0.9999, load) * 2 * Math.PI, ex = gx + R * Math.sin(a), ey = gy - R * Math.cos(a); return `<path d="M${gx},${gy} L${gx},${(gy - R).toFixed(3)} A${R},${R} 0 ${a > Math.PI ? 1 : 0} 1 ${ex.toFixed(3)},${ey.toFixed(3)} Z" fill="${col}"/>`; })() : ''}
        <text class="gwlbl" x="${gx}" y="${gy + 0.95}">#${g.gw_no}</text></g>`;
    });
    // patients: icon coloured by patch link quality (RSSI), name in small type next to it
    const linkCol = (p) => (p.gw < 0 || p.shadow) ? '#d33' : p.rssi >= -60 ? '#0e9f6e' : p.rssi >= -72 ? '#8bc34a' : p.rssi >= -82 ? '#f0a500' : '#e5602c';
    // moving / out-of-bed patients: several may share one room (corridor, lift hall, waiting area, exam room) -> lay them out
    // in a grid inside that room's polygon instead of stacking every marker on the room centre
    const polys = [...(m.rooms || []).filter(r => r.poly).map(r => r.poly), ...(m.corridors || []).map(c => c.poly)];
    const inBox = (b, x, y) => x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3];
    const groups = {};
    (m.patients || []).forEach(p => { if (!p.at_bed) (groups[p.room_idx] = groups[p.room_idx] || []).push(p); });
    const gwPts = gwFinal;                                            // every gateway marker drawn on this floor (final positions)
    const gwBox = (g) => [g.x - 0.8, g.y - 0.8, g.x + 0.8, g.y + 1.2];                             // pie + signal arc + '#no' label under it
    const labelW = (p) => Math.max(textWidth(p.name || '', p.dense ? 0.4 : 0.52), p.dense ? 0 : textWidth((p.stage || '').replace(/\s*\(.*\)$/, ''), 0.4));
    const nearGw = (x, y, p) => {                                     // footprint = ring box + (narrow) label box; both must clear every gateway and the room's own name label.
      const ring = [x - 0.7, y - 0.7, x + 0.7, y + 0.7], lw = p ? labelW(p) : 2.2, lh = p && p.dense ? 0.3 : 0.6;   // the label may sit right or left of the marker
      const rl = p && roomLbl[p.room_idx];
      const taken = (p && p.__taken) || [];                          // footprints of markers already placed in this room
      const clear = (box) => !gwPts.some(g => hit(box, gwBox(g))) && !(rl && hit(box, rl)) && !taken.some(t => hit(box, t));
      if (!clear(ring)) return true;
      const lr = [x + 0.7, y - 0.35, x + 0.78 + lw, y + lh], ll = [x - 0.78 - lw, y - 0.35, x - 0.7, y + lh];
      if (clear(lr)) { if (p) { p.side = 'r'; p.__foot = [ring, lr]; } return false; }
      if (clear(ll)) { if (p) { p.side = 'l'; p.__foot = [ring, ll]; } return false; }
      return true;
    };
    clusters = [];
    Object.values(groups).forEach(list => {
      const c = list[0]; const poly = polys.find(pl => inBox(bbox(pl), c.x, c.y)); const b = poly ? bbox(poly) : [c.x - 1.5, c.y - 1, c.x + 1.5, c.y + 1];
      // grid over the WHOLE room (1.5 x 1.05 m cells, tightened to 1.2 x 0.9 m only when the room is crowded), used from the centre
      // outwards; a cell is skipped when the marker footprint (ring + name label, right or left) would touch a gateway or the
      // room's name label.  Whatever finds no cell collapses into one "+N" cluster marker, so a crowded lobby never buries a gateway.
      const bw = Math.max(0.6, b[2] - b[0] - 1.2), bh = Math.max(0.6, b[3] - b[1] - 1.0);
      const grid = (sx, sy) => { const cols = Math.floor(bw / sx) + 1, rows = Math.floor(bh / sy) + 1, x0 = c.x - ((cols - 1) * sx) / 2, y0 = c.y - ((rows - 1) * sy) / 2, cells = [];
        for (let i = 0; i < cols * rows; i++) cells.push([x0 + (i % cols) * sx, y0 + Math.floor(i / cols) * sy]);
        return cells.sort((p, q) => Math.hypot(p[0] - c.x, p[1] - c.y) - Math.hypot(q[0] - c.x, q[1] - c.y)); };
      let cells = grid(1.5, 1.05), dense = false;
      if (cells.length < list.length + 1) { cells = grid(1.2, 0.9); dense = true; }
      const used = new Set(); const rest = []; const taken = [];
      if (list.length >= PLAN_CLUSTER_AT) {                            // very crowded spot: one count badge with a hover list instead of a pile of markers
        list.forEach(p => { p.clustered = true; rest.push(p); });
      }
      if (!rest.length) list.forEach(p => {                          // each patient takes the nearest free cell where ITS OWN label stays clear (of gateways, the room label and earlier markers)
        p.__taken = taken;
        for (const d of (dense ? [true] : [false, true])) {          // second pass with the short (name-only) label before giving up on an individual marker
          p.dense = d;
          for (let i = 0; i < cells.length; i++) { if (used.has(i)) continue; const [x, y] = cells[i]; if (nearGw(x, y, p)) continue; used.add(i); p.x = x; p.y = y; taken.push(...p.__foot); return; }
        }
        p.clustered = true; rest.push(p);
      });
      if (rest.length && rest.length < list.length) {                // the room is too tight to show everyone -> number badge for the whole spot, not a half-and-half
        rest.length = 0; used.clear(); taken.length = 0;
        list.forEach(p => { p.clustered = true; p.side = undefined; rest.push(p); });
      }
      if (rest.length) {                                             // overflow -> one '+N' marker (small footprint) in a free cell, else just right of the centre
        let pos = null; const probe = { name: '', stage: '', dense: true, __taken: taken, room_idx: c.room_idx };   // badge: ring only, but it must still dodge gateways and the room's name label
        for (let i = 0; i < cells.length && !pos; i++) { if (!used.has(i)) { const [x, y] = cells[i]; if (!nearGw(x, y, probe)) pos = [x, y]; } }
        if (!pos) { pos = [c.x + 1.7, c.y + 1.3]; for (let t = 0; t < 12 && nearGw(pos[0], pos[1], probe); t++) pos = t % 2 ? [pos[0] + 0.9, pos[1]] : [pos[0], pos[1] + 0.9]; }
        clusters.push({ x: pos[0], y: pos[1], list: rest });
      }
    });
    if (mode === 'patients') (m.patients || []).forEach(p => {
      const bad = p.gw < 0 || p.shadow, col = linkCol(p), q = bad ? '끊김' : p.rssi >= -60 ? '양호' : p.rssi >= -72 ? '보통' : p.rssi >= -82 ? '약함' : '매우 약함';
      if (p.clustered) return;                                        // drawn as part of a "+N" cluster marker below
      if (p.moving) {                                                 // on a trip: dashed ring (orange = exam/visit, red = shadow zone), name + current stage under it
        const ring = p.shadow ? '#d33' : p.mri ? '#d98a00' : '#e5602c'; const stg = (p.stage || '').replace(/\s*\(.*\)$/, '');
        dynOut += `<g class="pat mov ${p.shadow ? 'shadow' : ''}" data-row="${p.row}"><title>${esc(p.name)} · 환자번호 #${p.patient_no} · ${esc(p.note)} · ${esc(p.stage)} (${mmss(p.stage_remaining)} 남음) · 입원 침상 ${esc(p.bed || '-')} · RSSI ${p.rssi} dBm (${q})${p.lead_off ? ' · 리드오프' : ''} · 배터리 ${p.battery}%</title>
          <circle class="movring" cx="${p.x}" cy="${p.y}" r="0.66" stroke="${ring}"/>
          <circle cx="${p.x}" cy="${p.y}" r="0.45" fill="${col}"/><circle cx="${p.x}" cy="${p.y - 0.1}" r="0.15" fill="#fff"/><path d="M${p.x - 0.26},${p.y + 0.32} a0.26,0.26 0 0 1 0.52,0" fill="#fff"/>
          ${p.lead_off ? `<circle cx="${p.x + 0.36}" cy="${p.y - 0.36}" r="0.14" fill="${th.warn}" stroke="#fff" stroke-width="0.05"/>` : ''}
          <text class="plbl${p.dense ? ' dense' : ''}${p.side === 'l' ? ' left' : ''}" x="${p.side === 'l' ? p.x - 0.78 : p.x + 0.78}" y="${p.y + 0.05}">${esc(p.name)}</text>${p.dense ? '' : `<text class="plbl stg${p.side === 'l' ? ' left' : ''}" x="${p.side === 'l' ? p.x - 0.78 : p.x + 0.78}" y="${p.y + 0.5}">${esc(stg)}</text>`}</g>`;
        return;
      }
      const px = p.x - (p.at_bed ? 0.85 : 0); let py = p.y;     // at a bed: icon at the left edge, name over the bed
      // a bed on the top wall puts the marker under the room label ('04 4/4'): nudge icon + name down just enough to clear it
      const rl = p.at_bed && p.bed ? roomLbl[bedRoom[p.bed]] : null;
      if (rl) { const box = [px - 0.45, py - 0.45, px + 0.58 + textWidth(p.name || '', 0.52), py + 0.45]; if (hit(box, rl)) py += rl[3] - box[1] + 0.05; }
      dynOut += `<g class="pat" data-row="${p.row}"><title>${esc(p.name)} · 환자번호 #${p.patient_no} · ${p.activity} · RSSI ${p.rssi} dBm (${q})${p.lead_off ? ' · 리드오프' : ''} · 배터리 ${p.battery}%</title>
        <circle cx="${px}" cy="${py}" r="0.45" fill="${col}"/><circle cx="${px}" cy="${py - 0.1}" r="0.15" fill="#fff"/><path d="M${px - 0.26},${py + 0.32} a0.26,0.26 0 0 1 0.52,0" fill="#fff"/>
        ${p.lead_off ? `<circle cx="${px + 0.36}" cy="${py - 0.36}" r="0.14" fill="${th.warn}" stroke="#fff" stroke-width="0.05"/>` : ''}
        <text class="plbl" x="${px + 0.58}" y="${py + 0.2}">${esc(p.name)}</text></g>`;
    });
  }
  // bed click -> live signal
  planClusters = {};
  if (mode === 'patients') clusters.forEach((cl, ci) => {     // crowded spot: count badge; hovering it opens a list of the patients (click = open patient)
    planClusters[ci] = cl.list;
    const r = cl.list.length >= 10 ? 0.7 : 0.6;
    dynOut += `<g class="pat mov cluster" data-cl="${ci}" style="cursor:pointer">
      <circle class="movring" cx="${cl.x}" cy="${cl.y}" r="${r + 0.18}" stroke="#e5602c"/><circle cx="${cl.x}" cy="${cl.y}" r="${r}" fill="#e5602c"/>
      <text class="plbl clu" x="${cl.x}" y="${cl.y + 0.17}">${cl.list.length}</text></g>`;
  });
  // static layer (rooms, corridors, fixtures, labels) is parsed once per floor/mode/theme; the dynamic layer (beds with occupancy,
  // moving patients, count badges, gateways) is swapped on every refresh -> ~3000 static nodes are not rebuilt every 6 s
  // ward name tags: walk the corridor's long axis (30 %, 50 %, 70 %, 20 %, 80 %, ...) and take the first spot whose pill clears
  // every gateway marker (pie + arc + '#no' label) and every corridor fixture (central display, desk); rotated on vertical corridors
  const gwBoxes = gwFinal.map(g => [g.x - 1.0, g.y - 1.0, g.x + 1.0, g.y + 1.4]);
  const fixBoxes = allFix.map(f => f.box).filter(Boolean);
  zoneCands.forEach(zc => {
    const [cx0, cy0, cx1, cy1] = zc.bb, horiz = (cx1 - cx0) >= (cy1 - cy0);
    const fs = 0.62, tw = textWidth(zc.txt, fs) + 0.9;
    const box = (lx, ly) => horiz ? [lx - tw / 2, ly - 0.55, lx + tw / 2, ly + 0.55] : [lx - 0.55, ly - tw / 2, lx + 0.55, ly + tw / 2];
    const clear = (bx) => !gwBoxes.some(g => hit(bx, g)) && !fixBoxes.some(f => hit(bx, f));
    let lx, ly, ok = false;
    const order = [0.3, 0.5, 0.7, 0.2, 0.8, 0.4, 0.6, 0.15, 0.85, 0.1, 0.9].filter(t => !zc.inside || zc.inside.some(u => Math.abs(u - t) < 0.06)).concat([0.3, 0.5, 0.7]);
    for (const t of order) {                                       // only spots that lie inside the ward's own zone
      lx = horiz ? cx0 + (cx1 - cx0) * t : (cx0 + cx1) / 2; ly = horiz ? (cy0 + cy1) / 2 : cy0 + (cy1 - cy0) * t;
      if (clear(box(lx, ly))) { ok = true; break; }
    }
    if (!ok) {                                                     // nothing free on the axis: slide toward one corridor edge
      for (const t of [0.3, 0.5, 0.7]) for (const off of [-0.7, 0.7]) {
        const x = horiz ? cx0 + (cx1 - cx0) * t : (cx0 + cx1) / 2 + off, y = horiz ? (cy0 + cy1) / 2 + off : cy0 + (cy1 - cy0) * t;
        if (clear(box(x, y))) { lx = x; ly = y; ok = true; break; }
      }
    }
    zoneLabels += `<g class="zlbl" transform="translate(${lx.toFixed(2)},${ly.toFixed(2)})${horiz ? '' : ' rotate(-90)'}"><rect x="${(-tw / 2).toFixed(2)}" y="-0.55" width="${tw.toFixed(2)}" height="1.1" rx="0.55" fill="${zc.col}" fill-opacity="0.9"/><text x="0" y="0.22" font-size="${fs}" fill="#fff" text-anchor="middle" font-weight="700">${esc(zc.txt)}</text></g>`;
  });
  out = out.replace('<g class="zones" id="zoneTags"></g>', `<g class="zones" id="zoneTags">${zoneLabels}</g>`);
  const dyn = `<g id="planDyn">${bedsOut}${dynOut}${gwOut}</g>`;
  const staticKey = `${svg.dataset.floorKey}|${mode}|${document.documentElement.dataset.theme || ''}`;
  const dynEl = svg.dataset.staticKey === staticKey ? svg.querySelector('#planDyn') : null;
  if (dynEl) { const hl = svg.querySelector('.hl'); if (hl) hl.remove(); dynEl.outerHTML = dyn; }
  else { svg.innerHTML = out + dyn; svg.dataset.staticKey = staticKey; }
  if (mode === 'coverage') drawAllCoverage(); else if (covState.gidx !== null) drawCoverage(covState.gidx);
  // custom hover tips: move every <title> into data-tip on its parent (no native tooltips), shown by the tip layer below
  $$('title', svg).forEach(t => { const par = t.parentNode; if (par && par !== svg) { par.setAttribute('data-tip', t.textContent); } t.remove(); });   // new (dynamic) nodes only: static titles were converted at first render
  applyHighlight();
  $$('.bed.occ, .bed.lead, .bed.lost', svg).forEach(g => { const t = g.querySelector('title'); const name = t ? t.textContent.split(' · ')[0] : ''; const p = (m.patients || []).find(x => x.name === name); if (p) { g.style.cursor = 'pointer'; g.dataset.row = p.row; } });
  $('#mapLegend').innerHTML = ['room', 'nurse_station', 'exam', 'er', 'toilet', 'elevator', 'stairs', 'lobby', 'utility', 'courtyard'].map(k => `<span><i style="background:${KIND_FILL[k]}"></i>${KIND_KO[k]}</span>`).join('') +
    `<span><i style="background:#1b2733;border-color:#0aa8e6"></i>전광판(중앙/복도)</span><span><i style="background:#e9b96e"></i>간호사 카운터/접수</span><span style="color:#2b7fc2">▭ 병동 구역(점선)</span><span style="color:${th.acc}">● 정상 GW</span><span style="color:${th.warn}">● 저하</span><span style="color:${th.err}">● 장애</span><span class="sub">(점 = 파이그래프: 상태색 부채꼴이 BLE 연결 수/최대 32, 12시부터 시계방향 · 파란 호 = 무선 신호 · 호버 = 커버리지: 개방 ~10 m, 벽 통과 시 ~5 m · 음영지역 모드: 빗금 = 게이트웨이 음영지역)</span><span style="color:#2b7fc2">▮ 재원 침대</span><span style="color:#d98a00">▮ 리드오프</span><span style="color:#d33">▮ 연결 끊김</span><span style="color:#e5602c">◌ 이동 중 환자 (점선 링 · 이름 아래 현재 단계 · 붉은 점선 = 음영 구간)</span><span>환자 아이콘 색 = 패치 신호 세기: <span style="color:#0e9f6e">● 양호</span> <span style="color:#8bc34a">● 보통</span> <span style="color:#f0a500">● 약함</span> <span style="color:#e5602c">● 매우 약함</span> <span style="color:#d33">● 끊김</span></span>`;
  loadGateways().then(() => { if (mapHighlight && mapHighlight.type === 'gw') highlightGateway(mapHighlight.idx); }); loadWards(); loadTrips(); if (!$('#elevation').innerHTML) drawElevation(); else syncElevationSize();
}
// ---- gateway RF coverage (hover): ray casting against the floor plan walls.  Open line of sight reaches ~10 m; every wall
// crossed halves the remaining range, so a room behind one wall is covered ~5 m out and two walls stop it near ~2.5 m.
let covCtx = null; const covState = { gidx: null };
const COV_OPEN_M = 10.0, COV_WALL_ATT = 0.5, COV_RAYS = 240;
function coveragePolygon(ox, oy) {
  const { W, D, segs } = covCtx; const pts = [];
  for (let k = 0; k < COV_RAYS; k++) {
    const a = (k / COV_RAYS) * 2 * Math.PI, dx = Math.cos(a), dy = Math.sin(a);
    // distance to the floor boundary (absolute stop) along this ray
    let tb = Infinity;
    if (dx > 1e-9) tb = Math.min(tb, (W - ox) / dx); else if (dx < -1e-9) tb = Math.min(tb, -ox / dx);
    if (dy > 1e-9) tb = Math.min(tb, (D - oy) / dy); else if (dy < -1e-9) tb = Math.min(tb, -oy / dy);
    // wall crossings along the ray (ray/segment intersection), coincident shared walls merged into one
    const hits = [];
    for (const [x1, y1, x2, y2] of segs) {
      const ex = x2 - x1, ey = y2 - y1, den = dx * ey - dy * ex; if (Math.abs(den) < 1e-9) continue;
      const fx = x1 - ox, fy = y1 - oy, t = (fx * ey - fy * ex) / den, u = (fx * dy - fy * dx) / den;
      if (t > 0.05 && t < COV_OPEN_M && u >= 0 && u <= 1) hits.push(t);
    }
    hits.sort((p, q) => p - q);
    let budget = COV_OPEN_M, last = -1;
    for (const t of hits) { if (t - last < 0.12) continue; last = t; if (t >= budget) break; budget = t + (budget - t) * COV_WALL_ATT; }
    const r = Math.min(budget, tb);
    pts.push([ox + dx * r, oy + dy * r]);
  }
  return pts;
}
function drawCoverage(gidx) {
  const layer = document.querySelector('#floorMap .covLayer'); if (!layer || !covCtx) return;
  const g = covCtx.gws[gidx]; if (!g) { layer.innerHTML = ''; return; }
  const th = TH(), pts = coveragePolygon(g.x, g.y);
  layer.innerHTML = ($('#mapMode').value === 'coverage' ? layer.dataset.all || '' : '') + `<polygon points="${pts.map(p => `${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(' ')}" fill="${th.acc2}" fill-opacity="0.28" filter="url(#covblur)" pointer-events="none"/>
    <circle cx="${g.x}" cy="${g.y}" r="${COV_OPEN_M}" fill="none" stroke="${th.acc2}" stroke-opacity="0.35" stroke-width="0.05" stroke-dasharray="0.4 0.3" pointer-events="none"/>`;
  covState.gidx = gidx;
}
function clearCoverage() { covState.gidx = null; const layer = document.querySelector('#floorMap .covLayer'); if (layer) layer.innerHTML = $('#mapMode').value === 'coverage' ? layer.dataset.all || '' : ''; }
// '게이트웨이 커버리지' display mode: every fixed gateway's footprint at once (union tint; overlaps read darker = redundancy)
function drawAllCoverage() {
  const layer = document.querySelector('#floorMap .covLayer'); if (!layer || !covCtx) return;
  // shadow map: the whole floor is greyed out and every gateway footprint is cut out of the grey through a (blurred) mask,
  // so what remains grey is the RF dead zone
  const cut = Object.values(covCtx.gws).map(g => `<polygon points="${coveragePolygon(g.x, g.y).map(p => `${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(' ')}" fill="#000"/>`).join('');
  const html = `<mask id="covmask" maskUnits="userSpaceOnUse" x="-1" y="-1" width="${covCtx.W + 2}" height="${covCtx.D + 2}"><rect x="-1" y="-1" width="${covCtx.W + 2}" height="${covCtx.D + 2}" fill="#fff"/><g filter="url(#covblur)">${cut}</g></mask>
    <g mask="url(#covmask)" pointer-events="none"><rect x="0" y="0" width="${covCtx.W}" height="${covCtx.D}" fill="#4a5560" fill-opacity="0.16"/><rect x="0" y="0" width="${covCtx.W}" height="${covCtx.D}" fill="url(#covhatch)"/></g>`;
  layer.dataset.all = html; layer.innerHTML = html;
}
// ---- plan zoom & pan: click to zoom in (2.5x) at the pointer, drag to pan, click again to zoom out
const planZoom = { base: null, on: false, drag: null, moved: false, vb: null };
const PLAN_CLUSTER_AT = 8;                                   // >= this many moving patients in one spot -> count badge + hover list regardless of space
let planClusters = {};                                       // badge id -> patients (set by loadFloor)
function planSetVB(vb) { planZoom.vb = vb; $('#floorMap').setAttribute('viewBox', vb.map(v => v.toFixed(3)).join(' ')); }
function planSvgPoint(e) { const svg = $('#floorMap'); const r = svg.getBoundingClientRect(); const vb = planZoom.vb || planZoom.base; const cx = e.touches ? e.touches[0].clientX : e.clientX, cy = e.touches ? e.touches[0].clientY : e.clientY;
  // meet: uniform scale, centred
  const sc = Math.min(r.width / vb[2], r.height / vb[3]); const ox = (r.width - vb[2] * sc) / 2, oy = (r.height - vb[3] * sc) / 2;
  return { x: vb[0] + (cx - r.left - ox) / sc, y: vb[1] + (cy - r.top - oy) / sc, sc }; }
(function () {
  const svg = $('#floorMap');
  svg.addEventListener('pointerdown', e => { if (e.button !== 0 && e.pointerType === 'mouse') return; e.preventDefault(); planZoom.drag = { x: e.clientX, y: e.clientY, vb: planZoom.vb ? planZoom.vb.slice() : null, target: e.target.closest('[data-row], [data-gwidx]') }; planZoom.moved = false; svg.setPointerCapture(e.pointerId); });
  svg.addEventListener('pointermove', e => {
    if (!planZoom.drag || !planZoom.on) return;
    const dx = e.clientX - planZoom.drag.x, dy = e.clientY - planZoom.drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) planZoom.moved = true;
    if (!planZoom.moved) return;
    const r = svg.getBoundingClientRect(); const vb = planZoom.drag.vb; const sc = Math.min(r.width / vb[2], r.height / vb[3]);
    let nx = vb[0] - dx / sc, ny = vb[1] - dy / sc; const b = planZoom.base;
    nx = Math.max(b[0] - vb[2] * 0.2, Math.min(b[0] + b[2] - vb[2] * 0.8, nx)); ny = Math.max(b[1] - vb[3] * 0.2, Math.min(b[1] + b[3] - vb[3] * 0.8, ny));
    planSetVB([nx, ny, vb[2], vb[3]]);
  });
  svg.addEventListener('pointerup', e => {
    const wasDrag = planZoom.moved; const t = planZoom.drag && planZoom.drag.target; planZoom.drag = null;
    if (wasDrag) return;
    if (t && t.dataset.row !== undefined) { openPatientFromMap(Number(t.dataset.row)); return; }
    if (t && t.dataset.gwidx !== undefined) { highlightGateway(Number(t.dataset.gwidx)); openGatewayMonitor(Number(t.dataset.gwidx)); return; }
    if (!planZoom.base) return;
    if (!planZoom.on) {                       // zoom in around the pointer
      const p = planSvgPoint(e); const b = planZoom.base; const f = 2.5; const w = b[2] / f, h = b[3] / f;
      planSetVB([Math.max(b[0], Math.min(b[0] + b[2] - w, p.x - w / 2)), Math.max(b[1], Math.min(b[1] + b[3] - h, p.y - h / 2)), w, h]);
      planZoom.on = true; svg.style.cursor = 'grab';
    } else { planSetVB(planZoom.base.slice()); planZoom.on = false; svg.style.cursor = 'zoom-in'; }
  });
  svg.addEventListener('pointercancel', () => { planZoom.drag = null; });
  svg.addEventListener('wheel', e => {          // wheel zoom (desktop)
    if (!planZoom.base) return; e.preventDefault();
    const p = planSvgPoint(e); const vb = planZoom.vb.slice(); const f = e.deltaY < 0 ? 0.8 : 1.25; const b = planZoom.base;
    const w = Math.min(b[2], Math.max(b[2] / 8, vb[2] * f)), h = w * b[3] / b[2];
    const nx = p.x - (p.x - vb[0]) * (w / vb[2]), ny = p.y - (p.y - vb[1]) * (h / vb[3]);
    planSetVB([Math.max(b[0], Math.min(b[0] + b[2] - w, nx)), Math.max(b[1], Math.min(b[1] + b[3] - h, ny)), w, h]);
    planZoom.on = w < b[2] - 0.01; svg.style.cursor = planZoom.on ? 'grab' : 'zoom-in';
  }, { passive: false });
})();
// ---- hover tips for rooms / gateways / patients / beds / fixtures on the plan (bigger, readable font)
(function () {
  const svg = $('#floorMap'); const tip = document.createElement('div'); tip.className = 'maptip'; document.body.appendChild(tip);
  let cur = null;
  const show = (el, x, y) => {
    const txt = el.getAttribute('data-tip'); if (!txt) return hide();
    if (cur !== el) { tip.innerHTML = txt.split(' · ').map(esc).join('<br>'); cur = el; }
    tip.style.display = 'block';
    const r = tip.getBoundingClientRect(); const px = Math.min(window.innerWidth - r.width - 8, x + 14), py = (y + r.height + 18 > window.innerHeight) ? y - r.height - 12 : y + 14;
    tip.style.left = px + 'px'; tip.style.top = py + 'px';
  };
  const hide = () => { tip.style.display = 'none'; cur = null; };
  // count badge -> hover list of the patients in that spot (stays open while the pointer is on the badge or the list)
  const dd = document.createElement('div'); dd.className = 'plan-dd'; document.body.appendChild(dd); let ddCl = null, ddTimer = null;
  const ddHide = () => { clearTimeout(ddTimer); ddTimer = setTimeout(() => { dd.style.display = 'none'; ddCl = null; }, 180); };
  const ddShow = (g) => {
    const id = g.dataset.cl; clearTimeout(ddTimer);
    if (ddCl !== id) {
      const list = planClusters[id] || [];
      dd.innerHTML = `<div class="plan-dd-h">이 장소 ${list.length}명 · 클릭하면 환자 열기</div>` + list.map(p => `<div class="dd-item" data-row="${p.row}"><b>${esc(p.name)}</b><span class="sub">${esc((p.stage || '').replace(/\s*\(.*\)$/, ''))}${p.shadow ? ' · 음영' : ''}</span></div>`).join('');
      ddCl = id;
    }
    const c = g.querySelector('circle:nth-of-type(2)').getBoundingClientRect(); dd.style.display = 'block';
    const r = dd.getBoundingClientRect(); const px = Math.min(window.innerWidth - r.width - 8, c.right + 6), py = (c.top + r.height + 8 > window.innerHeight) ? Math.max(4, c.bottom - r.height) : c.top - 4;
    dd.style.left = px + 'px'; dd.style.top = py + 'px';
  };
  dd.addEventListener('pointerenter', () => clearTimeout(ddTimer)); dd.addEventListener('pointerleave', ddHide);
  dd.addEventListener('click', e => { const it = e.target.closest('.dd-item[data-row]'); if (it) { dd.style.display = 'none'; ddCl = null; openPatientFromMap(Number(it.dataset.row)); } });
  svg.addEventListener('click', e => { const g = e.target.closest('[data-cl]'); if (g) { e.stopPropagation(); ddShow(g); } }, true);
  svg.addEventListener('pointermove', e => {
    if (planZoom.drag && planZoom.moved) return hide();
    const cg = e.target.closest('[data-cl]'); if (cg) ddShow(cg); else if (ddCl !== null) ddHide();
    const el = e.target.closest('[data-tip]'); el ? show(el, e.clientX, e.clientY) : hide();
    const gw = e.target.closest('[data-gwidx]');                                    // gateway hover -> translucent RF coverage
    if (gw) { const gi = Number(gw.dataset.gwidx); if (covState.gidx !== gi) drawCoverage(gi); } else if (covState.gidx !== null) clearCoverage();
  });
  svg.addEventListener('pointerleave', () => { hide(); clearCoverage(); });
  svg.addEventListener('pointerdown', e => { if (e.pointerType !== 'mouse') { const el = e.target.closest('[data-tip]'); el ? show(el, e.clientX, e.clientY) : hide(); } });
  document.addEventListener('scroll', hide, true);
})();
$('#btnLayoutExport').onclick = async () => {
  const d = await api('/emr/layout'); const blob = new Blob([JSON.stringify(d, null, 1)], { type: 'application/json' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `hospital_layout_${(d.hospital && d.hospital.template) || 'plan'}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
};
$('#btnLayoutImport').onclick = () => $('#layoutFile').click();
$('#layoutFile').addEventListener('change', async e => {
  const file = e.target.files[0]; if (!file) return;
  try { const data = JSON.parse(await file.text()); toast('도면 가져오기 · 재구성 중...'); const r = await post('/emr/layout/import', { layout: data }); toast(`가져오기 완료: ${r.beds}병상 · GW ${r.gateways}`); floors = []; await loadFloors(); loadFloor(); loadConfig(); loadPatientList(); }
  catch (err) { toast('가져오기 실패: ' + err.message); }
  e.target.value = '';
});
// in-app confirm dialog (Promise<boolean>); Esc / 취소 = false
function askConfirm(title, msg, yesLabel = '실행') {
  return new Promise(res => {
    const dlg = $('#confirmDlg'); $('#confirmTitle').textContent = title; $('#confirmMsg').textContent = msg; $('#confirmYes').textContent = yesLabel; dlg.hidden = false;
    const done = (v) => { dlg.hidden = true; $('#confirmYes').onclick = $('#confirmNo').onclick = null; document.removeEventListener('keydown', onKey); res(v); };
    const onKey = (e) => { if (e.key === 'Escape') done(false); if (e.key === 'Enter') done(true); };
    $('#confirmYes').onclick = () => done(true); $('#confirmNo').onclick = () => done(false); dlg.onclick = (e) => { if (e.target === dlg) done(false); };
    document.addEventListener('keydown', onKey); $('#confirmYes').focus();
  });
}
$('#btnLayoutReset').onclick = async () => {
  const ok = await askConfirm('템플릿 재생성', '현재 병원 도면을 버리고 템플릿에서 다시 생성합니다.\n병실·게이트웨이 배치와 환자 위치가 모두 재배정되며 가져온 도면 JSON은 사라집니다.\n계속할까요?', '재생성');
  if (!ok) return;
  toast('템플릿으로 재생성 중...'); await post('/emr/layout/reset'); toast('재생성 완료 · 새로고침'); setTimeout(() => location.reload(), 400);   // the whole page state (floors, lists, monitor) is rebuilt: reload
};
// sortable table headers: th[data-key] click toggles asc/desc, state remembered per table, `onSort` re-renders
function makeSortable(tableId, storeKey, initial, onSort) {
  let st = initial; try { st = JSON.parse(localStorage.getItem(storeKey)) || st; } catch (e) { }
  const mark = () => $$(`#${tableId} th[data-key]`).forEach(th => { th.classList.toggle('asc', th.dataset.key === st.key && st.dir === 1); th.classList.toggle('desc', th.dataset.key === st.key && st.dir === -1); });
  $(`#${tableId} thead`).addEventListener('click', e => { const th = e.target.closest('th[data-key]'); if (!th) return; if (st.key === th.dataset.key) st.dir *= -1; else st = { key: th.dataset.key, dir: 1 }; try { localStorage.setItem(storeKey, JSON.stringify(st)); } catch (e2) { } mark(); onSort(st); });
  return { get: () => st, mark };
}
let gwSort = { key: 'gw_no', dir: 1 };
try { gwSort = JSON.parse(localStorage.getItem('gwSort')) || gwSort; } catch (e) { }
$('#gwTable thead').addEventListener('click', e => { const th = e.target.closest('th[data-key]'); if (!th) return; if (gwSort.key === th.dataset.key) gwSort.dir *= -1; else gwSort = { key: th.dataset.key, dir: 1 }; try { localStorage.setItem('gwSort', JSON.stringify(gwSort)); } catch (e) { } loadGateways(); });
async function loadGateways() {
  const r = await api('/emr/gateways'); const f = $('#gwFilter').value;
  const kf = (g) => gwSort.key === 'loc' ? `${g.building} ${String(g.floor).padStart(2, '0')} ${g.room || ''}` : gwSort.key === 'connected' ? (g.connected ? 1 : 0) : g[gwSort.key];
  const cmp = (a, b) => { const x = kf(a), y = kf(b); const r_ = typeof x === 'number' && typeof y === 'number' ? x - y : String(x ?? '').localeCompare(String(y ?? ''), 'ko'); return (r_ || a.idx - b.idx) * gwSort.dir; };
  $$('#gwTable th[data-key]').forEach(th => { th.classList.toggle('asc', th.dataset.key === gwSort.key && gwSort.dir === 1); th.classList.toggle('desc', th.dataset.key === gwSort.key && gwSort.dir === -1); });
  gwSummary(r.gateways);
  const list = r.gateways.filter(g => f === 'all' || (f === 'down' && g.status === 2) || (f === 'degraded' && g.status === 1) || (f === 'busy' && g.n_conn > 0) || (f === 'mobile' && g.type === 'mobile')).sort(cmp).slice(0, 400);
  $('#gwTable tbody').innerHTML = list.map(g => `<tr data-gwrow="${g.idx}"><td><span class="gwlink" data-gwopen="${g.idx}" title="중앙 모니터 열기">${g.id} <span class="sub">#${g.gw_no}</span></span></td><td>${g.type}</td><td>${g.building} ${g.floor}F ${g.room}</td><td><span class="tag ${['ok', 'warn', 'err'][g.status]}">${['정상', '저하', '장애'][g.status]}</span> ${g.reason || ''}</td><td>${g.n_conn}/${g.capacity}</td><td>${g.cpu}%</td><td>${g.mem}%</td><td>${g.net}%</td><td>${g.wan_rssi}</td><td>${(g.loss * 100).toFixed(0)}%</td><td>${g.latency_ms.toFixed(0)}ms</td><td>${fmt(g.pkts)}</td><td>${g.connected ? '<span class="tag ok">on</span>' : '-'}</td><td><button class="small" data-gw="${g.idx}">장애</button> <button class="small" data-gwrep="${g.idx}">교체</button></td></tr>`).join('');
}
$('#gwTable').addEventListener('click', async e => { const o = e.target.closest('[data-gwopen]'); if (o) { openGatewayMonitor(Number(o.dataset.gwopen)); return; } const b = e.target.closest('button[data-gw]'), c = e.target.closest('button[data-gwrep]'); if (b) { const r = await post('/control/trigger', { what: 'gateway_fault', target: Number(b.dataset.gw) }); toast(r.result); } else if (c) { const r = await post('/control/trigger', { what: 'gateway_replace', target: Number(c.dataset.gwrep) }); toast(r.result); } });
// ---- map search (patients by name, gateways by number/id) with highlight on the plan
let mapHighlight = null;
async function mapSearch(q) {
  const box = $('#mapRes'); q = q.trim().toLowerCase(); if (!q) { box.classList.remove('on'); box.innerHTML = ''; return; }
  const qn = q.replace(/^#/, '');                                   // '#123' -> gateway number 123
  const [pr, gr] = await Promise.all([api('/emr/patients?limit=20000'), api('/emr/gateways')]);
  const pats = q.startsWith('#') ? [] : pr.patients.filter(p => p.name.toLowerCase().includes(q) || String(p.patient_no) === q || (p.patch || '').toLowerCase().includes(q)).slice(0, 6);
  const gws = gr.gateways.filter(g => g.type !== 'mobile' && (String(g.gw_no) === qn || g.id.toLowerCase().includes(q) || (g.room || '').toLowerCase().includes(q))).slice(0, 6);
  box.innerHTML = '<div class="dd-list">' + pats.map(p => `<div class="dd-item" data-kind="patient" data-row="${p.row}" data-b="${p.building_idx}" data-f="${p.floor}">🧑‍⚕️ ${esc(p.name)} <small>${esc(p.bed || '')} · ${esc(p.ward || '')}</small></div>`).join('') +
    gws.map(g => `<div class="dd-item" data-kind="gw" data-idx="${g.idx}" data-bname="${esc(g.building)}" data-f="${g.floor}">📡 ${esc(g.id)} #${g.gw_no} <small>${esc(g.building)} ${g.floor}F ${esc(g.room || '')}</small></div>`).join('') +
    (pats.length + gws.length ? '' : '<div class="dd-empty">결과 없음</div>') + '</div>';
  box.classList.add('on');
}
$('#mapQ').addEventListener('input', () => {
  mapSearch($('#mapQ').value);
  if (!$('#mapQ').value.trim() && mapHighlight) {           // cleared -> drop the highlight, zoom out, unselect the gateway row
    mapHighlight = null; const svg = $('#floorMap'); $$('g.hl', svg).forEach(g => g.remove());
    if (planZoom.base) { planSetVB(planZoom.base.slice()); planZoom.on = false; svg.style.cursor = 'zoom-in'; }
    $$('#gwTable tr.sel, #gwTable tr.hit').forEach(x => x.classList.remove('sel', 'hit'));
  }
});
$('#mapQ').addEventListener('focus', () => { if ($('#mapRes').innerHTML) $('#mapRes').classList.add('on'); });
document.addEventListener('click', e => { if (!e.target.closest('#mapRes') && !e.target.closest('#mapQ')) $('#mapRes').classList.remove('on'); });
$('#mapRes').addEventListener('click', async e => {
  const it = e.target.closest('.dd-item[data-kind]'); if (!it) return;
  $('#mapRes').classList.remove('on');
  let fi = -1;
  if (it.dataset.kind === 'patient') { mapHighlight = { type: 'patient', row: Number(it.dataset.row) }; fi = floors.findIndex(f => String(f.building_idx) === it.dataset.b && String(f.floor) === it.dataset.f); }
  else { mapHighlight = { type: 'gw', idx: Number(it.dataset.idx) }; fi = floors.findIndex(f => f.building === it.dataset.bname && String(f.floor) === it.dataset.f); highlightGateway(mapHighlight.idx); }
  await gotoPlanFloor(fi);
});
async function gotoPlanFloor(fi) {                                  // show a floor with the current mapHighlight and bring the plan into view
  if (fi < 0) { toast('현재 도면에 없음 (원외/이동 중)'); return; }
  $('#selFloor').value = fi; syncDropdowns(); await loadFloor(); drawElevation();
  const pm = $('.planmain'); if (pm) pm.scrollIntoView({ block: 'start', behavior: 'smooth' });
}
function applyHighlight() {
  if (!mapHighlight) return;
  const svg = $('#floorMap');
  const el = mapHighlight.type === 'patient' ? svg.querySelector(`g.pat[data-row="${mapHighlight.row}"] circle, .bed[data-row="${mapHighlight.row}"]`)
    : mapHighlight.type === 'room' ? svg.querySelector(`polygon[data-rid="${mapHighlight.id}"]`) : svg.querySelector(`g.gw[data-gwidx="${mapHighlight.idx}"] circle`);
  if (!el) return;
  let cx, cy, rr = 1.6;
  if (el.tagName === 'polygon') {                                          // room: pulse a ring sized to the room, centred on it
    const p = el.getAttribute('points').split(' ').map(t => t.split(',').map(Number)); const b = bbox(p);
    cx = (b[0] + b[2]) / 2; cy = (b[1] + b[3]) / 2; rr = Math.max(1.6, Math.min(b[2] - b[0], b[3] - b[1]) / 2 + 0.4);
  }
  else if (el.tagName === 'circle') { cx = Number(el.getAttribute('cx')); cy = Number(el.getAttribute('cy')); }
  else { const tr = el.getAttribute('transform') || ''; const m = tr.match(/translate\(([-\d.]+),([-\d.]+)\)/); if (!m) return; cx = Number(m[1]); cy = Number(m[2]); }
  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g'); g.setAttribute('class', 'hl');
  g.innerHTML = `<circle cx="${cx}" cy="${cy}" r="${rr}" fill="none" stroke="#ff3b3b" stroke-width="0.22"><animate attributeName="r" values="${(rr * 0.75).toFixed(2)};${(rr * 1.35).toFixed(2)};${(rr * 0.75).toFixed(2)}" dur="1.4s" repeatCount="indefinite"/><animate attributeName="opacity" values="1;0.35;1" dur="1.4s" repeatCount="indefinite"/></circle><circle cx="${cx}" cy="${cy}" r="0.9" fill="none" stroke="#ff3b3b" stroke-width="0.14"/>`;
  svg.appendChild(g);
  // zoom in on the highlighted item
  const b = planZoom.base; if (b) { const w = b[2] / 2.5, h = b[3] / 2.5; planSetVB([Math.max(b[0], Math.min(b[0] + b[2] - w, cx - w / 2)), Math.max(b[1], Math.min(b[1] + b[3] - h, cy - h / 2)), w, h]); planZoom.on = true; svg.style.cursor = 'grab'; }
}
async function highlightGateway(idx) {
  let tr = document.querySelector(`#gwTable tr[data-gwrow="${idx}"]`);
  if (!tr) { $('#gwFilter').value = 'all'; syncDropdowns(); await loadGateways(); tr = document.querySelector(`#gwTable tr[data-gwrow="${idx}"]`); }
  if (!tr) { toast('게이트웨이 목록에 없음'); return; }
  $$('#gwTable tr.sel, #gwTable tr.hit').forEach(x => x.classList.remove('sel', 'hit')); tr.classList.add('hit');
  tr.scrollIntoView({ block: 'center', behavior: 'smooth' });
  tr.classList.add('flash'); setTimeout(() => tr.classList.remove('flash'), 1600);
}

// ---- Central Station + Vitals Monitor Viewer (design: ui_mockups/*.dc.html, Modernist design system)
// Click a gateway on the floor plan -> Central Station with every bed linked to it; click a bed tile -> single-bed viewer.
const CS_C = { hr: '#3ddc84', spo2: '#38c8f0', rr: '#f5d33d', nibp: '#ff8a3d', temp: '#e8e8e8', gl: '#d9a5ff' };
const LIMITS = { hr: [50, 120], spo2: [90, 100], rr: [8, 30], nibp: [90, 160], temp: [35.5, 38.5], gl: [70, 250] };
const BAT_LOW = 15;
const CS_TH = { waveBg: '#000', gridMinor: 'rgba(0,0,0,0)', gridMajor: 'rgba(243,242,242,.10)', ecg: CS_C.hr, ppg: CS_C.spo2, resp: CS_C.rr, warn: '#ff9783', err: '#ff563c', gl: CS_C.gl, ax: ['#ff9783', '#3ddc84', '#38c8f0'], paceLine: ['#ffe34d', '#ffffff', '#ff9783'] };   // pacer pulses: A yellow, V white, LV salmon
const VM_TH = { light: { waveBg: '#f3f2f2', gridMinor: 'rgba(0,0,0,0)', gridMajor: 'rgba(32,30,29,.16)', ecg: '#201e1d', ppg: '#7d7979', resp: '#7d7979', warn: '#dd2b0f', err: '#ec3013', gl: '#7d7979', ax: ['#ec3013', '#605d5d', '#9b9797'], paceLine: ['#dd2b0f', '#ec3013', '#ff9783'] },
  night: { waveBg: '#141313', gridMinor: 'rgba(0,0,0,0)', gridMajor: 'rgba(243,242,242,.18)', ecg: '#f3f2f2', ppg: '#9b9797', resp: '#9b9797', warn: '#ff9783', err: '#ff563c', gl: '#9b9797', ax: ['#ff9783', '#bab6b6', '#9b9797'], paceLine: ['#ffe34d', '#ff9783', '#ffc4b8'] } };
const RH_ALARM = { vfib: ['red', 'VFIB'], vt: ['red', 'VTACH'], asystole: ['red', 'ASYSTOLE'], avb3: ['red', 'AV BLOCK III'], nsvt: ['yellow', 'NSVT'], paced_malfunction: ['yellow', 'PACER FAIL'], svt: ['yellow', 'SVT'], afib: ['yellow', 'AFIB'], aflutter: ['yellow', 'AFL'], avb2: ['yellow', 'AV BLOCK II'] };
const mon = { open: false, idx: -1, gw: null, ws: null, states: {}, pats: [], rows: [], last: {}, timer: null, clock: null, lastMsg: 0, silenced: false, preset: null, page: 0, grid: { c: 1, r: 1 } };
// n-up presets (cols × rows); 'auto' picks the grid whose tile aspect is closest to the mockup tile (≈1.55 : 1) for the current window
const CS_PRESETS = [{ id: 'auto', label: 'Auto' }, { id: '2x1', c: 2, r: 1 }, { id: '2x2', c: 2, r: 2 }, { id: '2x3', c: 2, r: 3 }, { id: '3x3', c: 3, r: 3 }, { id: '3x4', c: 3, r: 4 }, { id: '4x5', c: 4, r: 5 }, { id: '4x6', c: 4, r: 6 }, { id: '6x8', c: 6, r: 8 },
  // numeric-only boards (no waveforms) for whole-ward / whole-floor views, up to 200 beds
  { id: '8x8', c: 8, r: 8, numeric: true }, { id: '12x8', c: 12, r: 8, numeric: true }, { id: '12x10', c: 12, r: 10, numeric: true }, { id: '16x10', c: 16, r: 10, numeric: true }];
const csNumeric = () => !!(mon.preset ? mon.preset.numeric : mon.pats.length > 48);
try { const pid = localStorage.getItem('cs:preset'); mon.preset = CS_PRESETS.find(x => x.id === pid && x.c) || null; } catch (e) { }
const _mctx = document.createElement('canvas').getContext('2d');
function textW(txt, px, weight = 800) { _mctx.font = `${weight} ${px}px Archivo, system-ui, sans-serif`; return _mctx.measureText(txt).width; }
function fitPx(txt, maxW, px, weight = 800) { const w = textW(txt, px, weight); return w > maxW ? px * maxW / w : px; }   // largest size at which txt fits maxW
function csAutoGrid(n, W, H, maxCols = 8) {
  let best = null;
  for (let c = 1; c <= maxCols; c++) {
    const r = Math.max(1, Math.ceil(n / c)); const asp = (W / c) / (H / r);
    const cost = Math.abs(Math.log(asp / 1.55)) + 0.25 * (c * r - n) / Math.max(1, n);   // aspect fit + penalty for empty slots
    if (!best || cost < best.cost) best = { c, r, cost };
  }
  return best;
}
function csPagePats() {                                          // the beds shown on the current page (fixed presets page; auto shows all)
  if (!mon.preset) return mon.pats;
  const cap = mon.grid.c * mon.grid.r; return mon.pats.slice(mon.page * cap, mon.page * cap + cap);
}
function csRenderPresets() {
  // Auto button + one dropdown for the fixed n-up grids (waveform grids up to 48, numeric-only boards above)
  const box = $('#csPresets'); if (!box) return;
  const prev = $('#csPresetSel'); if (prev && dropdowns.has(prev)) dropdowns.delete(prev);
  const cur = mon.preset ? mon.preset.id : 'auto';
  const opt = (x) => `<option value="${x.id}" ${cur === x.id ? 'selected' : ''}>${x.c * x.r}-up · ${x.c}×${x.r}${x.numeric ? ' · 숫자만' : ''}</option>`;
  box.innerHTML = `<button class="btn ${cur === 'auto' ? 'on' : ''}" data-preset="auto" title="자동 배치 (48명 초과 시 숫자만)">Auto</button>
    <select class="cs-sel" id="csPresetSel" title="n-up 격자 (열×행)"><option value="" ${cur === 'auto' ? 'selected' : ''} disabled hidden>n-up…</option>
      <optgroup label="파형 + 수치">${CS_PRESETS.filter(x => x.c && !x.numeric).map(opt).join('')}</optgroup>
      <optgroup label="숫자 전용 보드">${CS_PRESETS.filter(x => x.numeric).map(opt).join('')}</optgroup></select>`;
  const apply = (id) => { const x = CS_PRESETS.find(y => y.id === id); mon.preset = x && x.c ? x : null; mon.page = 0; try { localStorage.setItem('cs:preset', mon.preset ? mon.preset.id : 'auto'); } catch (e) { } monBuild(); csRenderPresets(); };
  $('button[data-preset="auto"]', box).onclick = () => apply('auto');
  const sel = $('#csPresetSel');
  sel.onchange = (e) => { if (e.target.value) apply(e.target.value); };
  makeDropdown(sel, { compact: true, sheet: false, cls: 'csdd', placeholder: 'n-up…' });     // the app's own dropdown, dark-styled for the monitor
}
function csRenderPager() {
  const pg = $('#csPager'); if (!pg) return;
  const cap = mon.grid.c * mon.grid.r, pages = mon.preset ? Math.max(1, Math.ceil(mon.pats.length / cap)) : 1;
  pg.hidden = pages <= 1;
  $('#csPage').textContent = `${mon.page + 1} / ${pages}`;
}
const vm = { open: false, row: -1, pid: null, p: null, detail: null, states: {}, night: false, clock: null, silenced: false, trend: null };
try { vm.night = localStorage.getItem('vm:night') === '1'; } catch (e) { }
const BELL_OFF = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/><path d="M18.63 13A17.89 17.89 0 0 1 18 8"/><path d="M6.26 6.26A5.86 5.86 0 0 0 6 8c0 7-3 9-3 9h14"/><path d="M18 8a6 6 0 0 0-9.33-5"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
const BELL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/></svg>';
const batIcon = (pct) => { const lvl = Math.max(0, Math.min(100, pct || 0)), col = lvl <= BAT_LOW ? 'var(--color-accent)' : 'currentColor'; return `<svg width="18" height="10" viewBox="0 0 18 10" style="display:inline-block;vertical-align:-1px"><rect x="0.5" y="0.5" width="14" height="9" fill="none" stroke="${col}" stroke-width="1"/><rect x="15" y="3" width="2" height="4" fill="${col}"/><rect x="2" y="2" width="${(11 * lvl / 100).toFixed(1)}" height="6" fill="${col}"/></svg>`; };
function rhAlarm(p) { const rk = (p.rhythm || '').toLowerCase(); if (RH_ALARM[rk]) return RH_ALARM[rk]; const e = Object.entries(RH_ALARM).find(([k]) => rk.startsWith(k)); return e ? e[1] : null; }
function monAlarm(p, n) {
  // priority like a central station: red (life-threatening) before yellow (limit / technical), first match wins
  if (p.shadow) return ['red', 'NO SIGNAL'];
  const ra = rhAlarm(p);
  if (ra && ra[0] === 'red') return ra;
  if (n) {
    if (n.spo2 && n.spo2 < 88) return ['red', `SpO₂ LOW ${n.spo2}`];
    if (n.hr && n.hr > 150) return ['red', `HR HIGH ${n.hr}`];
    if (n.hr && n.hr < 40) return ['red', `HR LOW ${n.hr}`];
  }
  const bp = p.nibp;
  if (bp && (bp.sys >= 190 || bp.sys < 80)) return ['red', `NIBP ${bp.sys >= 190 ? 'HIGH' : 'LOW'} ${bp.sys}/${bp.dia}`];
  if (p.lead_off) return ['yellow', 'ECG LEAD OFF'];
  if (ra) return ra;
  if (n) {
    if (n.spo2 && n.spo2 < LIMITS.spo2[0]) return ['yellow', `SpO₂ LOW ${n.spo2}`];
    if (n.hr && n.hr > LIMITS.hr[1]) return ['yellow', `HR HIGH ${n.hr}`];
    if (n.hr && n.hr < LIMITS.hr[0]) return ['yellow', `HR LOW ${n.hr}`];
    if (n.resp && n.resp > LIMITS.rr[1]) return ['yellow', `RR HIGH ${n.resp}`];
    if (n.resp && n.resp < LIMITS.rr[0]) return ['yellow', `RR LOW ${n.resp}`];
    if (bp && (bp.sys > LIMITS.nibp[1] || bp.sys < LIMITS.nibp[0])) return ['yellow', `NIBP ${bp.sys > LIMITS.nibp[1] ? 'HIGH' : 'LOW'} ${bp.sys}/${bp.dia}`];
    if (n.temp && n.temp >= LIMITS.temp[1]) return ['yellow', `TEMP HIGH ${n.temp.toFixed(1)}`];
    if (n.glucose && (n.glucose < LIMITS.gl[0] || n.glucose > LIMITS.gl[1])) return ['yellow', `GLU ${n.glucose.toFixed(0)}`];
  }
  if (p.battery !== undefined && p.battery <= BAT_LOW) return ['yellow', `BATTERY LOW ${p.battery}%`];
  return ['', 'NORMAL'];
}
const decHtml = (v, cls) => { const t = String(v); const i = t.indexOf('.'); return i < 0 ? esc(t) : `${esc(t.slice(0, i))}<span class="${cls}">${esc(t.slice(i))}</span>`; };   // fraction digits smaller than the integer part
// short status labels for small tiles ("SpO₂ LOW 87" -> "SpO₂↓87", "AV BLOCK III" -> "AVB III", "NIBP HIGH 165/90" -> "NIBP↑165")
const shortAlarm = (t) => t.replace('ECG LEAD OFF', 'LEAD OFF').replace('BATTERY LOW', 'BAT↓').replace('NO SIGNAL', 'NO SIG').replace('AV BLOCK', 'AVB').replace('PACER FAIL', 'PACER!')
  .replace(/ HIGH (\d+)\/\d+/, '↑$1').replace(/ LOW (\d+)\/\d+/, '↓$1').replace(' HIGH ', '↑').replace(' LOW ', '↓').replace(/ HIGH$/, '↑').replace(/ LOW$/, '↓');
const rhythmCode = (p) => { const m = (p.rhythm_label || '').match(/\(([^)]+)\)/); return m ? m[1] : (p.rhythm_label || '').slice(0, 12) || 'NSR'; };   // '심실조기수축 (PVC)' -> 'PVC'
const alarmKey = (txt) => /HR/.test(txt) ? 'hr' : /SpO/.test(txt) ? 'spo2' : /RR/.test(txt) ? 'rr' : /NIBP/.test(txt) ? 'nibp' : /TEMP/.test(txt) ? 'temp' : /GLU/.test(txt) ? 'gl' : '';
function csTile(p) {
  const ch = p.channels || [], hasEcg = ch.includes('ecg'), hasPpg = ch.includes('ppg'), hasResp = ch.includes('resp_wave'), hasAcc = ch.includes('accel');
  const sec = hasPpg ? 'ppg' : hasAcc && !hasResp ? 'accel' : '';
  // three fixed rows (ECG 3 : Pleth 1 : Resp 1); a waveform the patient's device set does not provide leaves its row empty
  const rows = [`<div class="cs-wv cs-ecg"><span class="cs-lbl" style="color:${CS_C.hr}">II</span><canvas id="cs_e_${p.row}"></canvas><div class="ds-off">LEAD OFF</div></div>`];
  rows.push(sec ? `<div class="cs-wv"><span class="cs-lbl" style="color:${sec === 'ppg' ? CS_C.spo2 : CS_C.temp}">${sec === 'ppg' ? 'Pleth' : 'Accel'}</span><canvas id="cs_s_${p.row}"></canvas></div>` : `<div class="cs-wv cs-nosensor"><span class="cs-lbl">Pleth · no sensor</span></div>`);
  rows.push(hasResp ? `<div class="cs-wv"><span class="cs-lbl" style="color:${CS_C.rr}">Resp</span><canvas id="cs_r_${p.row}"></canvas></div>` : `<div class="cs-wv cs-nosensor"><span class="cs-lbl">Resp · —</span></div>`);
  // tight 2×2 cell: label | limits on the first line, big value (left, from the label's x) | unit under the limits
  const v = (k, cls, label, lim, unit, span) => `<div class="cs-v ${span ? 'cs-span' : ''}" style="color:${CS_C[k]}"><span class="cs-lab">${label}</span><span class="cs-lim">${lim}</span><b class="cs-val ${cls} ds-none" id="cs_${k}_${p.row}">--</b><u class="cs-unit">${unit}</u></div>`;
  return `<article class="cs-tile" id="cst_${p.row}" data-row="${p.row}" title="${esc(p.name)} 단일 침상 모니터 열기">
    <div class="cs-head"><span class="cs-bed">${esc(p.bed)}</span><span class="ds-nm">${esc(p.name)}</span><span class="ds-meta">${p.sex === 'M' ? 'M' : 'F'} ${p.age} · ${esc(p.disease || '')}${p.paced ? ' · ' + esc(p.pacemaker_mode || 'PACED') : ''}</span><span class="cs-bat" id="cs_bat_${p.row}">${batIcon(p.battery)} ${p.battery}%</span><span class="cs-st" id="cs_st_${p.row}">NORMAL</span></div>
    <div class="cs-waves">${rows.join('')}</div>
    <div class="cs-vit">${v('hr', '', 'HR', `${LIMITS.hr[0]}–${LIMITS.hr[1]}`, 'bpm', true)}${v('spo2', '', 'SpO₂', `${LIMITS.spo2[0]}–`, '%', true)}${v('rr', 'cs-small', 'RR', `${LIMITS.rr[0]}–${LIMITS.rr[1]}`, '/min', true)}${v('nibp', 'cs-small', 'NIBP', `${LIMITS.nibp[0]}–${LIMITS.nibp[1]}`, 'mmHg', true)}${v('temp', 'cs-small', 'Temp <i>°C</i>', '', '', false)}${v('gl', 'cs-small', 'GLU <i>mg/dL</i>', '', '', false)}</div>
  </article>`;
}
function csNumTile(p) {
  // numeric-only tile: name (+bed), then HR · SpO₂ / RR · NIBP as big numbers; Temp · GLU as a small third line when tall enough
  const v = (k, label) => `<div class="cn-v" style="color:${CS_C[k]}"><span class="cn-l">${label}</span><b class="ds-none" id="cs_${k}_${p.row}">--</b></div>`;
  return `<article class="cs-tile cs-num${p.lead_off ? ' leadoff' : ''}" id="cst_${p.row}" data-row="${p.row}" title="${esc(p.name)} 단일 침상 모니터 열기">
    <div class="cs-head"><span class="cs-bed">${esc(p.bed)}</span><span class="ds-nm">${esc(p.name)}</span><span class="cs-bat" id="cs_bat_${p.row}">${batIcon(p.battery)}</span></div>
    <div class="cn-grid">${v('hr', 'HR')}${v('spo2', 'SpO₂')}${v('rr', 'RR')}${v('nibp', 'NIBP')}<div class="cn-st" id="cs_st_${p.row}" title="">${esc(rhythmCode(p))}</div><div class="cn-v cn-minor"><span class="cn-l" style="color:${CS_C.temp}">Temp</span><b class="ds-none" id="cs_temp_${p.row}" style="color:${CS_C.temp}">--</b><span class="cn-l" style="color:${CS_C.gl}">GLU</span><b class="ds-none" id="cs_gl_${p.row}" style="color:${CS_C.gl}">--</b></div></div>
  </article>`;
}
function monBuild() {
  const grid = $('#monGrid');
  grid.className = 'cs-grid';
  csLayout();
  const cap = mon.grid.c * mon.grid.r;
  if (mon.preset) mon.page = Math.min(mon.page, Math.max(0, Math.ceil(mon.pats.length / cap) - 1));
  const shown = csPagePats(); const n = mon.pats.length;
  const blanks = mon.preset ? Math.max(0, cap - shown.length) : 0;
  const numeric = csNumeric(); grid.classList.toggle('numeric', numeric);
  grid.innerHTML = (n ? shown.map(numeric ? csNumTile : csTile).join('') : '<div class="cs-empty">이 게이트웨이에 연결된 환자가 없습니다.</div>') + Array.from({ length: blanks }, () => '<article class="cs-tile cs-blank"></article>').join('');
  csRenderPager();
  mon.states = {};
  const fs = (CFG && CFG.signals) || { ecg_fs: 250, ppg_fs: 100, resp_fs: 25, accel_fs: 50 };
  if (!numeric) shown.forEach(p => {
    const ch = p.channels || [];
    const reg = (id, key, color, range, n_) => { if (!document.getElementById(id)) return null; const st = mkWave(id, color, range, n_); st.fixedTheme = CS_TH; delete waves[id]; mon.states[key] = st; return st; };
    reg(`cs_e_${p.row}`, `e${p.row}`, CS_C.hr, [-1.5, 2.0], fs.ecg_fs);
    if (ch.includes('ppg')) reg(`cs_s_${p.row}`, `s${p.row}`, CS_C.spo2, [-1.2, 1.5], fs.ppg_fs);
    else if (ch.includes('accel') && !ch.includes('resp_wave')) { const st = reg(`cs_s_${p.row}`, `s${p.row}`, CS_C.temp, [-1.6, 1.6], fs.accel_fs); if (st) { st.kind = 'accel'; st.colors = CS_TH.ax; } }
    if (ch.includes('resp_wave')) reg(`cs_r_${p.row}`, `r${p.row}`, CS_C.rr, [-1.5, 1.5], fs.resp_fs);
  });
  mon.rows = shown.map(p => p.row);                              // only the visible beds are streamed
  if (mon.ws && mon.ws.readyState === 1) mon.ws.send(JSON.stringify({ rows: mon.rows, numeric }));
  $$('.cs-tile[data-row]', grid).forEach(t => { t.onclick = () => vmOpen(Number(t.dataset.row)); });
  setTimeout(csLayout, 0);                                    // re-measure once the grid has its real height (sizes are then fixed - no later re-fitting)
}
// 12-up style layout for any count: equal-size tiles (same waveform space each), no scrolling up to 24 beds,
// type scaled from the tile height so 4 beds read big and 24 beds still fit
function csLayout() {
  const grid = $('#monGrid'); if (!grid) return;
  const n = Math.max(1, mon.pats.length);
  const H = grid.clientHeight || (window.innerHeight - 52), W = grid.clientWidth || window.innerWidth;
  let cols, rows;
  const numeric = csNumeric();
  if (mon.preset) { cols = mon.preset.c; rows = mon.preset.r; }                 // fixed n-up grid, paged
  else if (numeric) { const g = csAutoGrid(Math.min(n, 160), W, H, 16); cols = g.c; rows = g.r; }
  else { const g = csAutoGrid(Math.min(n, 24), W, H); cols = g.c; rows = n <= 24 ? g.r : Math.ceil(n / g.c); }
  mon.grid = { c: cols, r: rows };
  grid.style.gridTemplateColumns = `repeat(${cols},minmax(0,1fr))`;
  const fitAll = mon.preset || numeric || n <= 24;
  if (fitAll) { grid.style.gridTemplateRows = `repeat(${rows},minmax(0,1fr))`; grid.style.gridAutoRows = ''; }
  else { grid.style.gridTemplateRows = ''; grid.style.gridAutoRows = 'minmax(140px,1fr)'; }
  const th = fitAll ? H / rows : 140, tw = W / cols;
  if (numeric) {                                                // numeric board: 2×2 big numbers (+ small Temp/GLU line when tall)
    const headH = Math.max(18, Math.min(30, th * 0.2)); const minor = th > 110;
    const labPx = Math.max(8, Math.min(13, th * 0.075));
    const cellH = (th - headH - 6 - labPx * 1.5) / (minor ? 2.6 : 2), cellW = tw / 2 - 11;   // minus the status line
    let val = Math.max(9, Math.min(64, cellH * 0.62)); val = Math.max(9, fitPx('188/88', cellW - 10, val));    // widest value (NIBP) must fit its cell
    grid.style.setProperty('--cn-val', `${val.toFixed(1)}px`);
    mon.fontBase = { '--cn-val': val };
    grid.style.setProperty('--cn-lab', `${labPx.toFixed(1)}px`);
    grid.style.setProperty('--head', `${Math.max(9, Math.min(14, headH * 0.42)).toFixed(1)}px`); grid.style.setProperty('--headbig', `${Math.max(10, Math.min(17, headH * 0.5)).toFixed(1)}px`);
    grid.classList.toggle('minor', minor); grid.classList.toggle('compact', true); grid.classList.toggle('tiny', tw < 105);   // numeric boards: name only (no bed number)
    mon.grid = { c: cols, r: rows };
    return;
  }
  const k = Math.min(th / 300, tw / 460);                       // 1.0 = the 6-up mockup tile (≈300 px tall, 460 px wide)
  const vitw = Math.max(114, Math.min(280, tw * 0.34 + 10));    // same vitals column width on every tile -> same waveform width; wider on big tiles so the digits can grow
  const headH = 22 + Math.max(10, Math.min(16, 15 * k)) * 1.2;   // head bar height (padding + heading line)
  const VPAD = 24;                                              // vitals column padding (6+4) + row gaps (4 × 3) – not available to the rows
  const rowH5 = (th - headH - VPAD) / 5;
  // three vitals layouts by the height one of five stacked rows would get:
  //  full  (2-up … 6-up)  : label | limits over value | unit, Temp and GLU share the fifth row
  //  midv  (9-up … 12-up) : limits dropped, small label + unit, digits take almost the whole row
  //  grid2 (denser)       : 2 columns × 3 rows (HR SpO₂ / RR NIBP / Temp GLU), name above the digits, no limits / units
  //                         -> each digit cell is twice as tall as a stacked row, so the numbers stay big and readable
  const full = rowH5 >= 48, midv = !full && rowH5 >= 40, grid2 = rowH5 < 40;   // 2-up … 9-up keep the full form on a 1080p screen
  const narrow = !grid2 && vitw < 140;                           // narrow column in full/midv: Temp and GLU stack (6 rows)
  const rowH = Math.max(12, grid2 ? (th - headH - 14) / 3 : (th - headH - VPAD) / (narrow ? 6 : 5));
  // the CSS row weights (1.2 1.2 .9 .9 .9 | narrow: 1.2 1.2 .9 .9 .8 .8) give HR/SpO₂ taller rows than the small ones
  const rowBig = grid2 ? rowH : rowH * (narrow ? 1.24 : 1.18), rowSmall = grid2 ? rowH : rowH * (narrow ? 0.93 : 0.88), rowPair = grid2 ? rowH : rowH * (narrow ? 0.83 : 0.88);
  const lab = full ? Math.max(9, Math.min(13.5, 12 * k, rowH * 0.26)) : midv ? Math.max(9, Math.min(12, rowH * 0.22)) : Math.max(8, Math.min(11.5, rowH * 0.26));   // names / units: bold, a step larger
  // full / midv: the digits span the whole row (name, limits and unit stack in a right-hand column) -> height = row height
  const fit = grid2 ? Math.max(11, rowH - lab * 1.15 - 3) : Math.max(12, rowBig - 9);     // a few px of slack: the head bar estimate is approximate
  const fitSmall = grid2 ? fit : Math.max(10, rowSmall - 8);
  const inline = false, dense = grid2 && rowH < 26;
  grid.classList.toggle('midv', midv); grid.classList.toggle('grid2', grid2); grid.classList.toggle('inline', false); grid.classList.toggle('dense', dense);
  grid.style.setProperty('--lab', `${lab.toFixed(1)}px`);
  grid.style.setProperty('--stack', `${(lab * 1.25 + (full ? lab * 1.2 : 0) + 3).toFixed(1)}px`);   // height of the name (+ limits) lines: the unit sits below them
  const availW = vitw - 24;
  // name and unit share the unit size; the right-hand stack (name / unit / range) must fit the smallest row
  const stackBudget = grid2 ? 1e9 : (rowSmall - (full ? lab * 1.05 : 0) - 4) / 2.1;
  const sub0 = Math.max(7, Math.min(18, 16 * k, stackBudget));
  const px = (v, lo, hi, cap) => `${Math.max(lo, Math.min(hi, v * k, cap || 1e9)).toFixed(1)}px`;
  // right column of a cell holds the limits (top) and the unit (bottom): the digits get the rest of the width
  const colR = grid2 ? 0 : Math.max(textW('SpO₂', sub0, 800), textW('NIBP', sub0, 800), full ? textW('90–160', lab, 400) : 0, textW('mmHg', sub0, 400)) + 8;   // right-hand column: name (bold, unit-sized) / unit / limits
  const cellW = grid2 ? (vitw - 24 - 8) / 2 : availW;                          // grid2: two half-width columns
  let big = Math.max(9, Math.min(96, 64 * k, fit)), small = Math.max(8, Math.min(52, 34 * k, grid2 ? fit * 0.9 : fitSmall));
  big = fitPx('188', cellW - colR - 4, big);                                   // HR / SpO₂ (3 digits)
  small = fitPx('188/88', cellW - colR - 4, small);                            // NIBP is the widest small value
  // Temp | GLU share one row (half-width cells): their own size so "107 mg/dL" fits without shrinking the other values
  const pairW = grid2 ? cellW : narrow ? availW : (vitw - 24 - 16) / 2;
  // the unit sits in the label line ("Temp °C", "GLU mg/dL"), so the digits get the whole cell width
  let pair = Math.min(small, fitPx('38.8', pairW - 4, small), fitPx('188', pairW - 4, small));
  pair = Math.min(pair, grid2 ? fit : Math.max(9, rowPair - lab * 1.15 - 4));   // Temp/GLU keep the name above the digits: height budget = row minus the name line
  // grid2: "NIBP mmHg" on the name line must fit the half cell, otherwise the units are dropped
  grid.classList.toggle('nounit', grid2 && (textW('NIBP', lab, 600) + 5 + textW('mmHg', lab, 600) > cellW - 2));
  pair = Math.max(8, pair);
  grid.style.setProperty('--big', `${big.toFixed(1)}px`); grid.style.setProperty('--small', `${small.toFixed(1)}px`); grid.style.setProperty('--pair', `${pair.toFixed(1)}px`);
  mon.fontBase = { '--big': big, '--small': small, '--sub': Math.min(sub0, big * 0.34), '--pair': pair };
  grid.style.setProperty('--sub', `${Math.min(sub0, big * 0.34).toFixed(1)}px`);
  grid.style.setProperty('--head', px(14, 11, 16)); grid.style.setProperty('--headbig', px(18, 13, 20));
  grid.style.setProperty('--vitw', `${vitw.toFixed(0)}px`);
  grid.classList.toggle('narrow', narrow);
  grid.classList.toggle('compact', tw < 380 || cols * rows >= 48);   // small tiles / 48-up and above: drop the bed number, show the full patient name
  grid.classList.toggle('tiny', tw < 250);                      // very small: name + status only
}
// measured fit pass: if any vitals line is still wider than its cell, scale the value fonts down by the worst ratio
function csFitPass() {
  // idempotent: measure against the layout's base sizes and set base / ratio, so repeated passes never compound
  const grid = $('#monGrid'); if (!grid || !mon.fontBase) return;
  for (const [k, v] of Object.entries(mon.fontBase)) grid.style.setProperty(k, `${v.toFixed(1)}px`);
  let ratio = 1;
  $$('.cn-v', grid).forEach(c => { const b = c.querySelector('b'); if (b && c.clientWidth > 20 && b.scrollWidth > c.clientWidth + 1) ratio = Math.max(ratio, b.scrollWidth / c.clientWidth); });
  if (grid.classList.contains('numeric')) { if (ratio > 1.02) grid.style.setProperty('--cn-val', `${(mon.fontBase['--cn-val'] / ratio * 0.98).toFixed(1)}px`); return; }
  $$('.cs-vit', grid).forEach(v => {
    $$('.cs-v', v).forEach(c => { const l = c.querySelector('.cs-l'), n = c.querySelector('.cs-n'); if (!l || !n) return; const avail = c.clientWidth; const need = (grid.classList.contains('inline') ? l.scrollWidth + 6 : 0) + n.scrollWidth; if (avail > 20 && need > avail + 1) ratio = Math.max(ratio, need / avail); });
  });
  if (ratio > 1.02) for (const k of ['--big', '--small', '--sub']) { const base = mon.fontBase[k]; if (base) grid.style.setProperty(k, `${(base / ratio * 0.98).toFixed(1)}px`); }
}
window.addEventListener('resize', () => { if (mon.open) { const before = `${mon.grid.c}x${mon.grid.r}`; csLayout(); if (!mon.preset && `${mon.grid.c}x${mon.grid.r}` !== before) monBuild(); } });
window.__csState = () => ({ preset: mon.preset, numeric: csNumeric(), n: mon.pats.length, demo: mon.demo });
window.__csDemo = (n) => { const base = (mon.demoBase || mon.pats).slice(); if (!base.length) return; mon.demoBase = base; mon.demo = n > 0; if (!mon.demo) { mon.pats = base; monBuild(); return; } mon.pats = Array.from({ length: n }, (_, i) => ({ ...base[i % base.length], row: i < base.length ? base[i].row : 100000 + i, bed: i < base.length ? base[i].bed : `${base[i % base.length].bed.slice(0, -1)}${String.fromCharCode(65 + (i % 8))}` })); monBuild(); };   // layout check hook (fake extra beds)
function monHeader(g) {
  mon.gw = g;
  const GT = { room: '병실', corridor: '복도', exam: '검사실', lobby: '로비', support: '지원', toilet: '화장실', nurse: '간호사실', er: '응급실', mobile: 'MCOT' };
  $('#csUnit').textContent = `${g.building} ${g.floor}F · ${g.type === 'room' ? '병실 ' : ''}${g.room || GT[g.type] || g.type} · ${g.id} #${g.gw_no}${g.status === 2 ? ' · FAULT' : g.status === 1 ? ' · DEGRADED' : ''}`;
}
function monAlarmCount() {
  const n = Object.values(mon.last).filter(a => a && a[0]).length;
  const el = $('#csAlarms'); const muted = mon.silenced;
  el.textContent = `${n} alarm${n === 1 ? '' : 's'}${muted ? ' · silenced' : ''}`;
  el.className = 'cs-alarms' + (n ? (muted ? ' is-muted' : '') : ' ds-none');
  $('#monModal').classList.toggle('silenced', muted);
}
async function monRefresh() {
  if (!mon.open || mon.demo) return;
  let d; try { d = await api(`/emr/gateways/${mon.idx}/patients`); } catch (e) { return; }
  const same = d.patients.length === mon.pats.length && d.patients.every((p, i) => p.row === mon.pats[i].row && p.id === mon.pats[i].id);
  mon.pats = d.patients; monHeader(d.gateway);
  if (!same) { monBuild(); return; }
  d.patients.forEach(p => { const b = $(`#cs_bat_${p.row}`); if (b) b.innerHTML = `${batIcon(p.battery)} ${p.battery}%`; });
  if (vm.open) { const p = d.patients.find(x => x.row === vm.row); if (p) { vm.p = p; vmHeaderMeta(); } }
}
function monApply(row, v) {
  const p = mon.pats.find(x => x.row === row); if (!p) return;
  const tile = $(`#cst_${row}`); if (!tile) return;
  const n = v.error ? null : (v.num || {});
  const set = (k, val, fmt) => { const el = $(`#cs_${k}_${row}`); if (!el) return; if (val) { el.innerHTML = decHtml(fmt ? fmt(val) : val, 'cs-dec'); el.classList.remove('ds-none'); } else { el.textContent = '--'; el.classList.add('ds-none'); } };
  if (n) {
    const e = mon.states[`e${row}`]; if (e && v.ecg) feedWave(e, v.ecg, 0.001, v.pace, v.pace_type);
    const s = mon.states[`s${row}`]; if (s) { const arr = s.kind === 'accel' ? v.accel : v.ppg; if (arr) feedWave(s, arr, 0.001); }
    const r = mon.states[`r${row}`]; if (r && v.resp_wave) feedWave(r, v.resp_wave, 0.001);
  }
  set('hr', n && n.hr); set('spo2', n && n.spo2); set('rr', n && n.resp); set('temp', n && n.temp, x => x.toFixed(1)); set('gl', n && n.glucose, x => x.toFixed(0));
  set('nibp', p.nibp && `${p.nibp.sys}/${p.nibp.dia}`);
  const a = v.error ? (v.error === 'stopped' ? ['', 'STOPPED'] : ['red', 'NO DATA']) : monAlarm(p, n); mon.last[row] = a;
  tile.className = (tile.classList.contains('cs-num') ? 'cs-tile cs-num' : 'cs-tile') + (a[0] ? ' a-' + a[0] : '') + (p.lead_off ? ' leadoff' : '');
  const st = $(`#cs_st_${row}`);
  if (st) {
    const gr = $('#monGrid'); const small = gr && (gr.classList.contains('compact') || gr.classList.contains('inline'));
    // numeric boards (64-up+): the status line sits in the numbers area and shows the rhythm code while nothing alarms
    st.textContent = (!a[0] && tile.classList.contains('cs-num')) ? rhythmCode(p) : (small ? shortAlarm(a[1]) : a[1]); st.title = a[1];
  }
  const flag = alarmKey(a[1]); ['hr', 'spo2', 'rr', 'nibp', 'temp', 'gl'].forEach(k => { const el = $(`#cs_${k}_${row}`); if (el) el.classList.toggle('cs-flag', k === flag); });
  if (vm.open && vm.row === row) vmApply(p, v, n, a);
}
function monConnect() {
  if (mon.ws) { try { mon.ws.onclose = null; mon.ws.close(); } catch (e) { } }
  const w = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/live'); mon.ws = w; mon.lastMsg = performance.now();
  w.onopen = () => { mon.lastMsg = performance.now(); w.send(JSON.stringify({ rows: mon.rows, numeric: csNumeric() })); };
  w.onmessage = (m) => {
    mon.lastMsg = performance.now();
    const d = JSON.parse(m.data); if (!d.multi) return;
    for (const [rs, v] of Object.entries(d.multi)) monApply(Number(rs), v);
    monAlarmCount();

  };
  w.onclose = () => { if (mon.open) setTimeout(() => { if (mon.open) monConnect(); }, 1500); };
}
async function openGatewayMonitor(idx) {
  mon.idx = idx; mon.open = true; mon.last = {}; $('#monModal').hidden = false; document.body.style.overflow = 'hidden';
  $('#csLegend').innerHTML = [['ECG / HR', CS_C.hr], ['SpO₂', CS_C.spo2], ['RR', CS_C.rr], ['NIBP', CS_C.nibp], ['Temp', CS_C.temp], ['GLU', CS_C.gl]].map(([l, c]) => `<span style="color:${c}"><i style="background:${c}"></i>${l}</span>`).join('');
  $('#monGrid').innerHTML = '<div class="cs-empty">불러오는 중…</div>';
  let d; try { d = await api(`/emr/gateways/${idx}/patients`); } catch (e) { $('#monGrid').innerHTML = '<div class="cs-empty">게이트웨이 정보를 불러오지 못했습니다.</div>'; return; }
  mon.pats = d.patients; mon.page = 0; monHeader(d.gateway); csRenderPresets(); monBuild(); monConnect(); monAlarmCount();
  clearInterval(mon.timer); mon.timer = setInterval(() => { monRefresh(); monAlarmCount(); if (mon.ws && (mon.ws.readyState > 1 || performance.now() - mon.lastMsg > 4000)) monConnect(); }, 3000);
  clearInterval(mon.clock); const tick = () => { const t = new Date().toTimeString().slice(0, 8); $('#csClock').textContent = t; if (vm.open) $('#vmClock').textContent = t; }; tick(); mon.clock = setInterval(tick, 1000);
}
function closeMonitor() {
  if (vm.open) vmClose(); mon.demo = false; mon.demoBase = null;
  mon.open = false; $('#monModal').hidden = true; document.body.style.overflow = '';
  clearInterval(mon.timer); clearInterval(mon.clock);
  if (mon.ws) { try { mon.ws.onclose = null; mon.ws.close(); } catch (e) { } mon.ws = null; }
  mon.states = {}; mon.pats = []; mon.rows = []; mon.last = {}; $('#monGrid').innerHTML = '';
  mon.silenced = false; bellBtn($('#csSilence'), false); $('#monModal').classList.remove('silenced');
}
$('#monClose').onclick = closeMonitor;
const bellBtn = (el, on) => { el.classList.toggle('on', on); el.innerHTML = on ? BELL_OFF : BELL; el.setAttribute('aria-pressed', String(on)); };   // bell = alarms audible, bell-off = silenced
$('#csSilence').onclick = () => { mon.silenced = !mon.silenced; bellBtn($('#csSilence'), mon.silenced); monAlarmCount(); };   // toggle: 해제 ↔ 설정
$('#csReload').onclick = () => { if (mon.open) { const idx = mon.idx; closeMonitor(); openGatewayMonitor(idx); } };   // full reload of the monitor: patients, layout, stream
$('#csPrev').onclick = () => { if (mon.page > 0) { mon.page--; monBuild(); } };
$('#csNext').onclick = () => { const cap = mon.grid.c * mon.grid.r; if ((mon.page + 1) * cap < mon.pats.length) { mon.page++; monBuild(); } };
document.addEventListener('keydown', e => { if (e.key === 'Escape') { if (vm.open) vmClose(); else if (mon.open) closeMonitor(); } });

// ---- Vitals Monitor Viewer: one bed (Modernist light ground by default, night mode toggle)
const DEV_LABEL = { ecg_patch: 'Chest patch', spo2_ring: 'Ring', spo2_finger: 'Finger clip', spo2_wrist: 'Wrist', wrist: 'Wrist', cgm: 'CGM', temp_patch: 'Temp patch' };
function vmTile(k, label, unit, hi, lo, note) {
  return `<div class="vm-tile" id="vmt_${k}"><div class="vm-lab"><b>${label}</b><span class="ds-dim">${unit}</span></div><div class="vm-lim"><span>${hi}</span><span>${lo}</span></div>
    <div class="vm-val"><b class="ds-none" id="vmv_${k}">--</b><u id="vmu_${k}"></u></div>
    <div class="vm-foot"><svg width="96" height="18" viewBox="0 0 96 18" fill="none" stroke="currentColor" stroke-width="1.5"><polyline id="vms_${k}" points=""></polyline></svg><span id="vmn_${k}">${esc(note)}</span></div></div>`;
}
function vmHeaderMeta() {
  const p = vm.p, d = vm.detail || {};
  $('#vmBed').textContent = `BED ${p.bed}`;
  $('#vmUnit').textContent = `${p.ward || ''} · ${mon.gw ? mon.gw.building + ' ' + mon.gw.floor + 'F' : ''}`;
  $('#vmName').textContent = p.name;
  $('#vmMeta').textContent = `${p.sex === 'M' ? 'M' : 'F'} · ${p.age}y${d.weight_kg ? ` · ${d.weight_kg} kg` : ''}`;
  $('#vmMrn').textContent = d.mrn ? (String(d.mrn).startsWith('MRN') ? d.mrn : `MRN ${d.mrn}`) : `#${p.patient_no || ''}`;
  const ra = rhAlarm(p);
  $('#vmTags').innerHTML = `<span class="tag ${ra ? 'tag-accent' : 'tag-neutral'}">${esc(p.rhythm_label || '')}</span>` +
    `<span class="tag tag-outline">${p.paced ? `Pacer ${esc(p.pacemaker_mode || '')}` : 'Pacer: No'}</span>` +
    `<span class="tag ${p.battery <= BAT_LOW ? 'tag-accent' : 'tag-neutral'}">${batIcon(p.battery)}&nbsp;Patch ${p.battery}% · ${esc(p.patch || '')} · ${p.rssi} dBm</span>` +
    (d.disease ? `<span class="tag tag-neutral">${esc(d.disease)}</span>` : '');
  $('#vmPacer').textContent = p.paced ? `Pacer ${p.pacemaker_mode || 'on'} · pulse marks A/V` : 'Pacer off';
  const devs = (d.devices || []).map(x => DEV_LABEL[x] || x);
  $('#vmSpo2Src').textContent = devs.find(x => /Ring|Finger|Wrist/.test(x)) || (p.channels || []).includes('spo2') ? (devs.find(x => /Ring|Finger|Wrist/.test(x)) || 'Patch') : 'No SpO₂ sensor';
  $('#vmRespSrc').textContent = (p.channels || []).includes('resp_wave') ? 'Capacitive · Apnea limit 20 s' : 'Rate only';
  $('#vmModal').classList.toggle('leadoff', !!p.lead_off);
}
function vmApply(p, v, n, a) {
  const set = (k, val, fmt, unit) => { const el = $(`#vmv_${k}`), u = $(`#vmu_${k}`); if (!el) return; if (val) { el.innerHTML = decHtml(fmt ? fmt(val) : val, 'vm-dec'); el.classList.remove('ds-none'); u.textContent = unit || ''; } else { el.textContent = '--'; el.classList.add('ds-none'); u.textContent = ''; } };
  if (n) {
    const e = vm.states.e; if (e && v.ecg) feedWave(e, v.ecg, 0.001, v.pace, v.pace_type);
    const s = vm.states.p; if (s && v.ppg) feedWave(s, v.ppg, 0.001);
    const r = vm.states.r; if (r && v.resp_wave) feedWave(r, v.resp_wave, 0.001);
  }
  set('hr', n && n.hr); set('spo2', n && n.spo2); set('rr', n && n.resp); set('temp', n && n.temp, x => x.toFixed(1)); set('gl', n && n.glucose, x => x.toFixed(0));
  if (p.nibp) { set('nibp', p.nibp.sys, null, `/ ${p.nibp.dia}`); const d = new Date(p.nibp.t * 1000); $('#vmn_nibp').textContent = `MAP ${p.nibp.map} · ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')} · q1h`; }
  const flag = alarmKey(a[1]);
  ['hr', 'spo2', 'rr', 'nibp', 'temp', 'gl'].forEach(k => { const t = $(`#vmt_${k}`); if (t) t.className = 'vm-tile' + (k === flag && a[0] ? ' is-alarm is-' + a[0] : ''); });
  $('#vmn_hr').textContent = a[0] && flag === 'hr' ? (a[1].includes('HIGH') ? 'Above high limit' : 'Below low limit') : (p.rhythm_label || '');
  const ab = $('#vmAlarm'), q = $('#vmQuiet');
  if (a[0]) { ab.hidden = false; q.hidden = true; ab.className = 'vm-alarm is-' + a[0]; ab.innerHTML = `${BELL} ${esc(a[1])}`; } else { ab.hidden = true; q.hidden = false; }
  $('#vmModal').classList.toggle('leadoff', !!p.lead_off);
  $('#vmModal').classList.toggle('silenced', vm.silenced);
}
function vmTheme() { const el = $('#vmModal'); el.classList.toggle('night', vm.night); $('#vmNight').classList.toggle('on', vm.night); const th = vm.night ? VM_TH.night : VM_TH.light; Object.values(vm.states).forEach(st => { st.fixedTheme = th; st.color = st.kind === 'ecg' ? th.ecg : th.ppg; st.w = 0; }); }
async function vmOpen(row) {
  const p = mon.pats.find(x => x.row === row); if (!p) return;
  vm.row = row; vm.pid = p.id; vm.p = p; vm.open = true; vm.detail = null; vm.trend = null;
  const el = $('#vmModal'); el.hidden = false;
  const fs = (CFG && CFG.signals) || { ecg_fs: 250, ppg_fs: 100, resp_fs: 25 };
  const th = vm.night ? VM_TH.night : VM_TH.light;
  vm.states = {};
  const reg = (id, key, color, range, n_, kind) => { const st = mkWave(id, color, range, n_); st.fixedTheme = th; st.kind = kind; delete waves[id]; vm.states[key] = st; };
  reg('vm_e', 'e', th.ecg, [-1.5, 2.0], fs.ecg_fs, 'ecg'); reg('vm_p', 'p', th.ppg, [-1.2, 1.5], fs.ppg_fs, 'ppg'); reg('vm_r', 'r', th.resp, [-1.5, 1.5], fs.resp_fs, 'resp');
  $('#vmAside').innerHTML = vmTile('hr', 'HR', 'bpm', LIMITS.hr[1], LIMITS.hr[0], p.rhythm_label || '') + vmTile('spo2', 'SpO₂', '%', LIMITS.spo2[1], LIMITS.spo2[0], '') + vmTile('rr', 'RR', '/min', LIMITS.rr[1], LIMITS.rr[0], '') + vmTile('nibp', 'NIBP', 'mmHg', '160/95', '90/50', 'Cuff · q1h') + vmTile('temp', 'Temp', '°C', LIMITS.temp[1], LIMITS.temp[0], '') + vmTile('gl', 'GLU', 'mg/dL', LIMITS.gl[1], LIMITS.gl[0], '');
  $('#vmSweep').textContent = `${CFG && CFG.signals ? '25 mm/s' : '25 mm/s'} · 10 mm/mV`;
  vmHeaderMeta(); vmTheme(); el.scrollTop = 0;
  $('#vmClock').textContent = new Date().toTimeString().slice(0, 8);
  try { vm.detail = await api(`/emr/patients/${p.id}`); if (vm.open && vm.row === row) vmHeaderMeta(); } catch (e) { }
  vmLoadTrend(); vmLoadEvents();
}
function vmClose() { vm.open = false; $('#vmModal').hidden = true; vm.states = {}; vm.row = -1; vm.silenced = false; bellBtn($('#vmSilence'), false); $('#vmModal').classList.remove('silenced'); }
async function vmLoadTrend() {
  let t; try { t = await api(`/signals/trend/${vm.pid}?days=1`); } catch (e) { $('#vmTrendBody').innerHTML = '<tr><td colspan="8" class="ds-dim">추세 없음</td></tr>'; return; }
  if (!vm.open) return;
  vm.trend = t; const n = t.t.length, step = t.step_s || 600, pts6h = Math.min(n, Math.round(6 * 3600 / step));
  const spark = (arr) => { const a = arr.slice(n - pts6h).filter(x => x > 0); if (a.length < 2) return ''; const lo = Math.min(...a), hi = Math.max(...a), rng = hi - lo || 1; return a.map((v, i) => `${(i / (a.length - 1) * 96).toFixed(1)},${(16 - (v - lo) / rng * 14).toFixed(1)}`).join(' '); };
  const map = { hr: 'hr', spo2: 'spo2', rr: 'resp', nibp: 'bp_sys', temp: 'temp', gl: 'glucose' };
  for (const [k, src] of Object.entries(map)) { const pl = $(`#vms_${k}`); if (pl && t[src]) pl.setAttribute('points', spark(t[src])); }
  const first = (arr) => { const a = arr.slice(n - pts6h).filter(x => x > 0); return a.length ? a[0] : 0; }, last = (arr) => { const a = arr.filter(x => x > 0); return a.length ? a[a.length - 1] : 0; };
  if (t.temp) { const d = last(t.temp) - first(t.temp); $('#vmn_temp').textContent = `Patch · ${d >= 0 ? '+' : ''}${d.toFixed(1)} / 6 h`; }
  if (t.spo2) { const s = vm.detail && vm.detail.devices ? (vm.detail.devices.map(x => DEV_LABEL[x] || x).find(x => /Ring|Finger|Wrist/.test(x)) || 'Patch') : ''; $('#vmn_spo2').textContent = s; }
  $('#vmn_rr').textContent = 'Capacitive'; $('#vmn_gl').textContent = (vm.detail && (vm.detail.devices || []).includes('cgm')) ? 'CGM' : 'Spot';
  // hourly spot-check rows, newest first (6 rows)
  const perHour = Math.max(1, Math.round(3600 / step)); const rows = [];
  for (let k = 0; k < 6; k++) { const i = n - 1 - k * perHour; if (i < 0) break; const d = new Date(t.t[i] * 1000); rows.push(`<tr><td class="ds-dim">${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}</td><td>${t.hr[i] ? t.hr[i].toFixed(0) : '--'}</td><td>${t.spo2[i] ? t.spo2[i].toFixed(0) : '--'}</td><td>${t.resp[i] ? t.resp[i].toFixed(0) : '--'}</td><td>${t.bp_sys ? `${t.bp_sys[i]}/${t.bp_dia[i]}` : '--'}</td><td>${t.bp_map ? t.bp_map[i] : '--'}</td><td>${t.temp[i] ? t.temp[i].toFixed(1) : '--'}</td><td>${t.glucose[i] ? t.glucose[i].toFixed(0) : '--'}</td></tr>`); }
  $('#vmTrendBody').innerHTML = rows.join('');
}
async function vmLoadEvents() {
  let r; try { r = await api('/events?since=0&limit=500'); } catch (e) { return; }
  if (!vm.open) return;
  const ev = r.events.filter(e => e.patient_id === vm.pid).slice(-6).reverse();
  const tagOf = (k) => /vfib|alarm|fault|lead|battery/.test(k) ? 'tag-accent' : /rhythm|episode/.test(k) ? 'tag-neutral' : 'tag-outline';
  $('#vmEvents').innerHTML = ev.length ? ev.map(e => { const d = new Date(e.t * 1000); return `<div class="vm-ev"><span class="vm-t">${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}</span><span>${esc(e.msg)}</span><span class="tag ${tagOf(e.kind)}">${esc(e.kind)}</span></div>`; }).join('') : '<div class="vm-ev"><span class="vm-t">—</span><span class="ds-dim">기록된 이벤트 없음</span><span></span></div>';
}
$('#vmBack').onclick = vmClose;
$('#vmReload').onclick = () => { if (vm.open) { const row = vm.row; vmClose(); vmOpen(row); } };
$('#vmNight').onclick = () => { vm.night = !vm.night; try { localStorage.setItem('vm:night', vm.night ? '1' : '0'); } catch (e) { } vmTheme(); };
$('#vmSilence').onclick = () => { vm.silenced = !vm.silenced; bellBtn($('#vmSilence'), vm.silenced); $('#vmModal').classList.toggle('silenced', vm.silenced); };
$('#vmTrends').onclick = () => { $('#vmBottom').scrollIntoView({ behavior: 'smooth', block: 'start' }); };

// ---- manual: table of contents, search, "apply this setup" buttons
(function guideInit() {
  const toc = document.getElementById('gToc'), q = document.getElementById('gSearch'); if (!toc) return;
  const secs = [...document.querySelectorAll('.gsec')];
  toc.innerHTML = secs.map(sec => `<a class="sec" href="#${sec.dataset.id}" data-target="${sec.dataset.id}">${sec.dataset.title}</a>` +
    [...sec.querySelectorAll('.gitem')].map(it => `<a class="item" href="#${it.id}" data-target="${it.id}">${it.querySelector('h4').firstChild.textContent.trim()}</a>`).join('')).join('');
  toc.querySelectorAll('a').forEach(a => a.onclick = (e) => { e.preventDefault(); const el = document.getElementById(a.dataset.target); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); toc.querySelectorAll('a').forEach(x => x.classList.toggle('on', x === a)); });
  const unmark = (root) => root.querySelectorAll('mark.ghl').forEach(m => m.replaceWith(document.createTextNode(m.textContent)));
  const mark = (root, term) => { const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); const nodes = []; while (w.nextNode()) nodes.push(w.currentNode);
    nodes.forEach(n => { const i = n.nodeValue.toLowerCase().indexOf(term); if (i < 0 || n.parentNode.closest('button')) return; const m = document.createElement('mark'); m.className = 'ghl'; m.textContent = n.nodeValue.slice(i, i + term.length); const after = document.createTextNode(n.nodeValue.slice(i + term.length)); n.nodeValue = n.nodeValue.slice(0, i); n.parentNode.insertBefore(m, n.nextSibling); m.parentNode.insertBefore(after, m.nextSibling); }); };
  q.addEventListener('input', () => {
    const term = q.value.trim().toLowerCase();
    secs.forEach(sec => { unmark(sec); let any = false; sec.querySelectorAll('.gitem').forEach(it => { const hit = !term || it.textContent.toLowerCase().includes(term); it.classList.toggle('hide', !hit); if (hit) any = true; if (hit && term) mark(it, term); }); sec.style.display = any ? '' : 'none'; });
    toc.querySelectorAll('a.item').forEach(a => { const it = document.getElementById(a.dataset.target); a.classList.toggle('hide', !!(it && it.classList.contains('hide'))); });
    toc.querySelectorAll('a.sec').forEach(a => { const sec = document.getElementById(a.dataset.target); a.classList.toggle('hide', !!(sec && sec.style.display === 'none')); });
  });
  makeClearable(q);
  document.querySelectorAll('.gitem button.apply').forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      const body = JSON.parse(b.dataset.apply || '{}');
      const r = await patch(body); CFG = r.config; fillForm();
      let msg = '설정 적용' + (r.needs_rebuild ? ' (구조 변경: 데이터 탭 [병원·환자 재구성] 필요)' : '');
      if (b.dataset.trig) { const t = await post('/control/trigger', JSON.parse(b.dataset.trig)); msg += ' · ' + t.result; }
      if (b.dataset.script) { await post('/control/script', { file: b.dataset.script }); msg += ' · 스크립트 ' + b.dataset.script + ' 시작'; }
      toast(msg);
    } catch (e) { toast('적용 실패: ' + e.message); }
    b.disabled = false;
  });
})();
$('#btnResetRuntime').onclick = async () => {
  const ok = await askConfirm('전체 파라미터 초기화', '환자 수, 시나리오, 기기 세트, 전송·드릴 설정을 모두 기본값으로 되돌립니다.\n병원 도면(템플릿)과 루프 파일 은행은 그대로 둡니다.\n계속할까요?', '초기화');
  if (!ok) return;
  try { const r = await post('/config/reset', { scope: 'runtime' }); CFG = r.config; fillForm(); toast('파라미터 초기화 완료'); } catch (e) { toast('실패: ' + e.message); }
};
function openPatientFromMap(row) {
  showTab('pat');
  loadPatientList().then(async () => {
    let p = patList.find(x => x.row === row);
    if (!p) { try { const r = await api('/emr/patients?row=' + row); p = r.patients[0]; } catch (e) { } }   // the dropdown list is capped at 1000: fetch just this row
    if (!p) { toast('환자를 찾을 수 없습니다'); return; }
    selectRow(row);                                           // live stream for the detail card
    $('#patStatus').value = 'admitted'; $('#patQ').value = ''; syncDropdowns(); patPage = 0; await loadPatients(true);
    const items = patCache.items.slice().sort((a, b) => { const k = patSort.key, d = patSort.dir; const x = a[k], y = b[k]; if (x === y) return a.id - b.id; return (typeof x === 'number' && typeof y === 'number') ? (x - y) * d : String(x).localeCompare(String(y), 'ko') * d; });
    const i = items.findIndex(x => x.id === p.id); if (i >= 0) { patPage = Math.floor(i / PAGE); await loadPatients(); }
    const tr = document.querySelector(`#patBody .prow[data-id="${p.id}"]`); if (tr) tr.scrollIntoView({ block: 'center' });
    showPatientDetail(p.id);                                  // always render the card, even when the row is not on the visible page
    const d = $('#patDetail'); if (d) d.scrollIntoView({ block: 'start', behavior: 'smooth' });
  });
}
let lastGws = [], lastWards = [], lastTrips = null;
function gwSummary(gws) {
  lastGws = gws; const el = $('#gwSummary'); if (!el) return;
  const fixed = gws.filter(g => g.type !== 'mobile'), mob = gws.length - fixed.length;
  const st = [0, 0, 0]; gws.forEach(g => st[g.status]++);
  const conn = gws.reduce((a, g) => a + g.n_conn, 0), tcp = gws.filter(g => g.connected).length, cap = gws.reduce((a, g) => a + g.capacity, 0);
  const n = gws.length || 1, avg = (k) => gws.reduce((a, g) => a + g[k], 0) / n, mx = (k) => gws.reduce((a, g) => Math.max(a, g[k]), 0);
  el.innerHTML = isFolded('gwBody')
    ? `<span class="fsum"><span>총 <b>${gws.length}</b> (고정 ${fixed.length} · 모바일 ${mob})</span><span class="ok">정상 <b>${st[0]}</b></span><span class="warn">저하 <b>${st[1]}</b></span><span class="err">장애 <b>${st[2]}</b></span><span>BLE 연결 <b>${fmt(conn)}</b> <span class="sub">(GW당 최대 ${Math.max(...gws.map(g => g.capacity))} · 총 용량 ${fmt(cap)})</span></span><span>TCP on <b>${tcp}</b></span><span>평균 지연 <b>${avg('latency_ms').toFixed(1)} ms</b></span><span>평균 pkts <b>${fmt(Math.round(avg('pkts')))}</b></span><span>CPU max <b>${mx('cpu')}%</b></span><span>MEM max <b>${mx('mem')}%</b></span><span>NET max <b>${mx('net')}%</b></span></span>`
    : `<span class="fsum"><span><b>${gws.length}</b>대</span>${st[2] ? `<span class="err">장애 <b>${st[2]}</b></span>` : ''}${st[1] ? `<span class="warn">저하 <b>${st[1]}</b></span>` : ''}</span>`;
}
function wardSummary(wards) {
  lastWards = wards; const wc = $('#wardCount'); if (!wc) return;
  const beds = wards.reduce((a, w) => a + w.n_beds, 0), occ = wards.reduce((a, w) => a + w.occupied, 0), depts = new Set(wards.map(w => w.specialty)).size, full = wards.filter(w => w.occupied >= w.n_beds).length;
  wc.innerHTML = isFolded('wardBody')
    ? `<span class="fsum"><span>병동 <b>${wards.length}</b> · 진료과 <b>${depts}</b></span><span>병상 <b>${beds}</b></span><span>재원 <b>${occ}</b> (${beds ? Math.round(occ / beds * 100) : 0}%)</span><span>빈 병상 <b>${beds - occ}</b></span>${full ? `<span class="warn">만실 <b>${full}</b></span>` : ''}</span>`
    : `<span class="fsum"><span><b>${wards.length}</b>개 병동</span></span>`;
}
const TRIP_KO = { exam: '검사', visit: '방문', transfer: '병실 이동', shadowtrip: '음영 진입', toilet: '화장실', walk: '보행', shower: '샤워', rehab: '재활', other: '기타' };
const mmssL = (s) => { s = Math.max(0, Math.round(s)); return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m` : mmss(s); };
const hhmmss = (iso) => (iso || '').slice(11, 19);
const roomLoad = (d) => { const rs = d.stats.rooms || []; return `${rs.reduce((a, r) => a + r.load, 0)}/${rs.reduce((a, r) => a + r.capacity, 0)}`; };
function tripSummary(d) {
  const el = $('#tripSummary'); if (!el) return;
  if (!d) { el.textContent = ''; return; }
  const k = d.stats.kinds;
  el.innerHTML = isFolded('tripBody')
    ? `<span class="fsum"><span>이동 중 <b>${d.stats.moving}</b> / 재원 ${d.stats.admitted}</span><span>검사 <b>${k.exam}</b></span><span>방문 <b>${k.visit || 0}</b></span><span>병실 이동 <b>${k.transfer}</b></span><span>음영 진입 <b>${k.shadowtrip || 0}</b></span><span>화장실 <b>${k.toilet}</b></span><span>보행 <b>${k.walk}</b></span><span>샤워 <b>${k.shower}</b></span><span>재활 <b>${k.rehab}</b></span><span class="warn">음영 구간 <b>${d.stats.shadow}</b></span><span class="warn">MRI 패치 분리 <b>${d.stats.mri_patch_off}</b></span><span>1시간 내 검사 예정 <b>${d.stats.exams_soon}</b></span><span>검사실 사용 <b>${roomLoad(d)}</b></span></span>`
    : `<span class="fsum"><span>이동 중 <b>${d.stats.moving}</b></span><span>검사실 사용 <b>${roomLoad(d)}</b></span>${d.stats.shadow ? `<span class="warn">음영 <b>${d.stats.shadow}</b></span>` : ''}${d.stats.mri_patch_off ? `<span class="warn">MRI 패치 분리 <b>${d.stats.mri_patch_off}</b></span>` : ''}<span>1시간 내 검사 예정 <b>${d.stats.exams_soon}</b></span><span class="sub">시뮬 ${hhmmss(d.sim_time)}</span></span>`;
}
async function loadTrips() {
  let d; try { d = await api('/emr/trips'); } catch (e) { return; }
  lastTrips = d; tripSummary(d);
  const body = $('#tripTable tbody'); if (!body || isFolded('tripBody')) return;
  const stepCls = (st) => (st.state === 'done' ? 'done' : st.state === 'current' ? 'cur' : '') + (st.label.includes('MRI') ? ' mri' : '') + (st.label.includes('음영') ? ' shadow' : '');
  const stepIcon = (st) => st.state === 'done' ? '✓' : st.state === 'current' ? '▶' : '○';
  const cap = $('#tripRooms'); if (cap) cap.innerHTML = (d.stats.rooms || []).filter(r => r.capacity).map(r => `<span class="${r.load >= r.capacity ? 'full' : ''}" data-rid="${esc(r.room_id)}" data-b="${r.building_idx}" data-f="${r.floor}" title="클릭: 도면에서 ${esc(r.room)} 보기">${esc(r.room)} <b>${r.load}</b>/${r.capacity}</span>`).join('');
  const selRow = body.querySelector('tr.sel')?.dataset.row;
  const ts = tripSort.get(); tripSort.mark();
  const tk = (t) => ts.key === 'location_name' ? `${t.location_name} ${t.location}` : ts.key === 'gateway' ? (t.shadow ? '~음영' : (t.gateway || '~없음')) : t[ts.key];
  const rows = d.trips.slice().sort((a, b) => { const x = tk(a), y = tk(b); const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x ?? '').localeCompare(String(y ?? ''), 'ko'); return (c || a.row - b.row) * ts.dir; });
  body.innerHTML = rows.slice(0, 300).map(t => `<tr data-row="${t.row}" data-b="${t.building_idx}" data-f="${t.floor}" class="${String(t.row) === selRow ? 'sel' : ''}" title="클릭: 현재 위치 층 도면 열기">
    <td data-l="환자"><span class="gwlink" data-patopen="${t.row}" title="환자 열기">${esc(t.name)}</span> <span class="sub">${t.sex}/${t.age}</span><div class="sub">${esc(t.bed)} · ${esc(t.ward)}</div></td>
    <td data-l="이동"><span class="tag ${t.kind === 'exam' ? 'warn' : ''}">${TRIP_KO[t.kind] || t.kind}</span><div class="sub">${esc(t.note)}</div><div class="sub">${hhmmss(t.started)} → ${hhmmss(t.ends)}</div></td>
    <td data-l="현재 단계"><b>${esc(t.stage)}</b><div class="sub">남은 ${mmssL(t.stage_remaining)} · 단계 ${t.step_no}/${t.n_steps}</div>${t.patch_removed ? '<div class="sub" style="color:var(--warn)">패치 분리 중 (리드오프)</div>' : (t.lead_off ? '<div class="sub" style="color:var(--warn)">리드오프</div>' : '')}</td>
    <td data-l="위치">${esc(t.location_name)} <span class="sub">${esc(t.location)}</span><div class="sub">${t.building_idx !== null && t.building_idx !== undefined ? `${esc((floors.find(f => f.building_idx === t.building_idx) || {}).building || '')} ${t.floor}F` : ''}</div></td>
    <td data-l="게이트웨이">${t.shadow ? '<span class="tag warn">음영 · 연결 없음</span>' : (t.gateway ? `<span class="gwlink" data-gwopen="${t.gateway_idx}">${esc(t.gateway)}</span>` : '<span class="tag err">연결 없음</span>')}</td>
    <td data-l="진행"><span class="tprog ${t.shadow ? 'shadow' : ''}"><i style="width:${Math.round(t.progress * 100)}%"></i></span><div class="sub">경과 ${mmssL(t.elapsed)} · 남은 ${mmssL(t.total_remaining)}</div></td>
    <td data-l="타임테이블"><div class="tt">${t.steps.map(st => `<span class="${stepCls(st)}" title="${esc(st.label)} · ${hhmmss(st.start)} 시작 · ${mmssL(st.dur)}">${stepIcon(st)} ${esc(st.label)} <i>${hhmmss(st.start)}</i>${st.state === 'current' ? `<i>(${mmssL(st.remaining)} 남음)</i>` : (st.dur ? `<i>${mmssL(st.dur)}</i>` : '')}</span>`).join('')}</div></td>
    <td data-l="다음 예정 검사"><div class="tnext">${t.next_exams.length ? t.next_exams.map(e => `<span><b>${esc(e.type)}</b> ${hhmmss(e.time).slice(0, 5)} <span class="sub">(${mmssL(e.in_s)} 후 · ${esc(e.room)} · ${e.duration_min}분${e.patch_policy === 'remove' ? ' · 패치 분리' : ''})</span></span>`).join('') : '<span class="sub">예정 없음</span>'}</div></td>
  </tr>`).join('') || '<tr><td colspan="8" class="sub">현재 이동 중인 환자가 없습니다.</td></tr>';
}
$('#tripRooms').addEventListener('click', e => {                     // room-load chip: open that floor and pulse the room on the plan
  const ch = e.target.closest('span[data-rid]'); if (!ch) return;
  $$('#tripRooms span.sel').forEach(x => x.classList.remove('sel')); ch.classList.add('sel');
  mapHighlight = { type: 'room', id: ch.dataset.rid };
  gotoPlanFloor(floors.findIndex(x => String(x.building_idx) === ch.dataset.b && String(x.floor) === ch.dataset.f));
  clearTimeout(tripHlTimer);
  tripHlTimer = setTimeout(() => { ch.classList.remove('sel'); if (mapHighlight && mapHighlight.type === 'room' && mapHighlight.id === ch.dataset.rid) { mapHighlight = null; const hl = $('#floorMap .hl'); if (hl) hl.remove(); } }, 8000);
});
const tripSort = makeSortable('tripTable', 'tripSort', { key: 'name', dir: 1 }, () => { if (lastTrips) loadTrips(); });
$('#tripTable').addEventListener('click', e => {
  const o = e.target.closest('[data-gwopen]'); if (o) { openGatewayMonitor(Number(o.dataset.gwopen)); return; }
  const p = e.target.closest('[data-patopen]'); if (p) { openPatientFromMap(Number(p.dataset.patopen)); return; }
  const tr = e.target.closest('tr[data-row]'); if (!tr) return;          // row click: open the floor the patient is on right now and highlight the marker
  $$('#tripTable tr.sel').forEach(x => x.classList.remove('sel')); tr.classList.add('sel');
  const b = tr.dataset.b, f = tr.dataset.f; if (b === undefined || b === '' || b === 'null') { toast('현재 도면에 없음'); return; }
  const row = Number(tr.dataset.row);
  mapHighlight = { type: 'patient', row };
  gotoPlanFloor(floors.findIndex(x => String(x.building_idx) === b && String(x.floor) === f));
  clearTimeout(tripHlTimer);
  tripHlTimer = setTimeout(() => {                                          // 5 s later: drop the pulsing ring and the row tint
    tr.classList.remove('sel');
    if (mapHighlight && mapHighlight.type === 'patient' && mapHighlight.row === row) { mapHighlight = null; const hl = $('#floorMap .hl'); if (hl) hl.remove(); }
  }, 5000);
});
let tripHlTimer = null;
let hospTick = 0;
setInterval(() => {
  if (document.visibilityState === 'hidden' || !$('[data-tab="hosp"]').classList.contains('on') || document.getElementById('monModal') && !document.getElementById('monModal').hidden) return;
  hospTick++;
  if (hospTick % 2 === 0 && !planZoom.drag && covState.gidx === null && !ddOpen) loadFloor();   // plan (moving patients) + gateway/ward/trip lists every 6 s, never mid-drag or while a coverage hover is up
  else loadTrips();
}, 3000);
document.addEventListener('fold', e => { if (e.detail.id === 'gwBody') gwSummary(lastGws); else if (e.detail.id === 'wardBody') wardSummary(lastWards); else if (e.detail.id === 'tripBody') { tripSummary(lastTrips); if (e.detail.open) loadTrips(); } });
async function loadWards() { const r = await api('/emr/wards'); wardSummary(r.wards); $('#wardTable tbody').innerHTML = r.wards.map(w => `<tr><td>${w.name}</td><td>${w.specialty}</td><td>${w.building} ${w.floor}F</td><td>${w.n_beds}</td><td>${w.occupied}</td></tr>`).join(''); }

// ---------------------------------------------------------------- devices
let DEV = null;
function mixNote() {
  const m = (CFG.scenario.devices || {}).spo2_mix || {}; const t = (m.fingertip || 0) + (m.ring || 0) + (m.wrist_ptt || 0) || 1;
  const pct = k => Math.round((m[k] || 0) / t * 100);
  $('#d_mixNote').textContent = `병원 내: 손끝 ${pct('fingertip')}% · 반지형 ${pct('ring')}% · 손목 ${pct('wrist_ptt')}%  |  원외: 반지형 ${Math.round(((m.ring || 0) + (m.fingertip || 0)) / t * 100)}% · 손목 ${pct('wrist_ptt')}%`;
}
['d_mf', 'd_mr', 'd_mw'].forEach(id => document.getElementById(id).addEventListener('input', () => { const m = CFG.scenario.devices.spo2_mix; m[{ d_mf: 'fingertip', d_mr: 'ring', d_mw: 'wrist_ptt' }[id]] = Number(document.getElementById(id).value); mixNote(); }));
async function loadDevices() {
  try { DEV = await api('/emr/devices'); } catch (e) { return; }
  const st = DEV.stats; const n = st.patients || 1;
  $('#devStats').innerHTML = `모니터링 환자 ${fmt(st.patients)}명 · ` + Object.entries(st.counts).map(([k, v]) => `${DEV.devices[k].short} ${fmt(v)} (${Math.round(v / n * 100)}%)`).join(' · ');
}
$('#btnDevApply').onclick = async () => { const r = await post('/control/devices/apply', { policy: $('#s_devpol').value }); toast(`기기 세트 일괄 적용: ${r.patients}명`); loadDevices(); loadPatCard(); loadPatientList(); };
function devChips(p, r) {
  if (!DEV) return '';
  const have = new Set(r.devices || []);
  return `<div class="chips devchips" data-pid="${p.id}">` + Object.entries(DEV.devices).map(([k, d]) => `<label title="${esc(d.desc)}" class="${d.fixed ? 'fixed' : ''}"><input type="checkbox" data-dev="${k}" ${have.has(k) ? 'checked' : ''} ${d.fixed ? 'disabled' : ''}>${d.short}</label>`).join('') + `</div>`;
}
document.addEventListener('change', async e => {
  const cb = e.target; if (!cb.matches || !cb.matches('.devchips input[data-dev]')) return;
  const box = cb.closest('.devchips'); const pid = Number(box.dataset.pid);
  let devs = $$('input[data-dev]', box).filter(x => x.checked).map(x => x.dataset.dev);
  if (cb.checked && ['spo2_fingertip', 'spo2_ring', 'bp_wrist'].includes(cb.dataset.dev)) devs = devs.filter(d => d === cb.dataset.dev || !['spo2_fingertip', 'spo2_ring', 'bp_wrist'].includes(d));
  const r = await post(`/emr/patients/${pid}/devices`, { devices: devs }); toast('기기 세트: ' + r.devices.map(d => DEV.devices[d].short).join(', '));
  setTimeout(() => { loadPatCard(); loadDevices(); }, 300);
});

// ---------------------------------------------------------------- trends (multi-day)
let trendDays = 1, trendOpen = false, trendData = null;
const TREND_META = { hr: ['tHr', 'ecg'], spo2: ['tSpo2', 'ppg'], resp: ['tResp', 'resp'], temp: ['tTemp', 'temp'], glucose: ['tGl', 'gl'] };
$('#btnTrend').onclick = () => { trendOpen = !trendOpen; $('#trendPanel').hidden = !trendOpen; $('#btnTrend').classList.toggle('on', trendOpen); if (trendOpen) loadTrend(); };
$$('#trendDays button').forEach(b => b.onclick = () => { trendDays = Number(b.dataset.days); $$('#trendDays button').forEach(x => x.classList.toggle('on', x === b)); loadTrend(); });
async function loadTrend() {
  if (!curPid || !trendOpen) return;
  try { trendData = await api(`/signals/trend/${curPid}?days=${trendDays}`); } catch (e) { $('#trendNote').textContent = '추세 없음 (모니터링 중인 환자만)'; return; }
  $('#trendNote').textContent = trendData.note || '';
  const chs = new Set(trendData.channels || []);
  const need = { hr: 'hr', spo2: 'spo2', resp: 'resp', temp: 'temp', glucose: 'glucose' };
  for (const [k, [cid, col]] of Object.entries(TREND_META)) {
    const cv = document.getElementById(cid); const box = cv.closest('.trend');
    box.classList.toggle('off', !chs.has(need[k]));
    drawTrend(cv, trendData.t, trendData[k], TH()[col] || TH().acc, k);
    const arr = trendData[k]; const mn = Math.min(...arr), mx = Math.max(...arr);
    document.getElementById(cid + 'Range').textContent = `${fmt(mn, k === 'temp' ? 1 : 0)} ~ ${fmt(mx, k === 'temp' ? 1 : 0)}`;
  }
}
const TREND_UNIT = { hr: 'bpm', spo2: '%', resp: '/min', temp: '°C', glucose: 'mg/dL' };
function drawTrend(cv, ts, ys, color, key, hoverIdx = -1) {
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight; if (!w) return;
  if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  cv._trend = { ts, ys, color, key };
  const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); const th = TH();
  g.fillStyle = th.waveBg; g.fillRect(0, 0, w, h);
  const padL = 34, padB = 16, padT = 6, iw = w - padL - 4, ih = h - padT - padB;
  let mn = Math.min(...ys), mx = Math.max(...ys); const span = Math.max(mx - mn, { temp: 1, spo2: 4, resp: 6, hr: 20, glucose: 60 }[key] || 1);
  const mid = (mn + mx) / 2; mn = mid - span / 2 * 1.15; mx = mid + span / 2 * 1.15;
  const X = i => padL + i / (ys.length - 1) * iw, Y = v => padT + (1 - (v - mn) / (mx - mn)) * ih;
  // day boundaries + midnight labels
  g.strokeStyle = th.gridMinor; g.lineWidth = 1; g.font = '10px sans-serif'; g.fillStyle = th.muted; g.textAlign = 'center';
  let lastDay = -1;
  ts.forEach((t, i) => { const d = new Date(t * 1000); if (d.getHours() === 0 && d.getMinutes() < 10 && d.getDate() !== lastDay) { lastDay = d.getDate(); const x = Math.round(X(i)) + 0.5; g.strokeStyle = th.gridMajor; g.beginPath(); g.moveTo(x, padT); g.lineTo(x, padT + ih); g.stroke(); g.fillText(`${d.getMonth() + 1}/${d.getDate()}`, x, h - 3); }
    else if (d.getHours() % 6 === 0 && d.getMinutes() < 10) { const x = Math.round(X(i)) + 0.5; g.strokeStyle = th.gridMinor; g.beginPath(); g.moveTo(x, padT); g.lineTo(x, padT + ih); g.stroke(); if (ys.length <= 300) g.fillText(`${d.getHours()}h`, x, h - 3); } });
  // y labels
  g.textAlign = 'right'; g.fillStyle = th.muted;
  [mn + (mx - mn) * 0.1, mid, mx - (mx - mn) * 0.1].forEach(v => { const y = Y(v); g.strokeStyle = th.gridMinor; g.beginPath(); g.moveTo(padL, Math.round(y) + 0.5); g.lineTo(w - 4, Math.round(y) + 0.5); g.stroke(); g.fillText(fmt(v, key === 'temp' ? 1 : 0), padL - 4, y + 3); });
  // area + line
  g.beginPath(); g.moveTo(X(0), Y(ys[0])); ys.forEach((v, i) => g.lineTo(X(i), Y(v)));
  g.lineTo(X(ys.length - 1), padT + ih); g.lineTo(X(0), padT + ih); g.closePath(); g.fillStyle = color; g.globalAlpha = 0.12; g.fill(); g.globalAlpha = 1;
  g.strokeStyle = color; g.lineWidth = 1.5; g.lineJoin = 'round'; g.beginPath(); ys.forEach((v, i) => i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))); g.stroke();
  const lx = X(ys.length - 1), ly = Y(ys[ys.length - 1]); g.fillStyle = color; g.beginPath(); g.arc(lx, ly, 3, 0, Math.PI * 2); g.fill();
  cv._geom = { padL, iw, X, Y };
  if (hoverIdx >= 0) {                                   // hover cursor + marker
    const hx = Math.round(X(hoverIdx)) + 0.5, hy = Y(ys[hoverIdx]);
    g.strokeStyle = th.muted; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(hx, padT); g.lineTo(hx, padT + ih); g.stroke(); g.setLineDash([]);
    g.fillStyle = th.waveBg; g.beginPath(); g.arc(hx, hy, 5, 0, Math.PI * 2); g.fill();
    g.strokeStyle = color; g.lineWidth = 2; g.beginPath(); g.arc(hx, hy, 4, 0, Math.PI * 2); g.stroke();
  }
}
function trendHover(e) {
  const cv = e.currentTarget, d = cv._trend, gm = cv._geom; if (!d || !gm) return;
  const box = cv.closest('.trend'); let tip = box.querySelector('.tip'); if (!tip) { tip = document.createElement('div'); tip.className = 'tip'; box.appendChild(tip); }
  if (e.type === 'mouseleave' || e.type === 'touchend') { tip.style.display = 'none'; drawTrend(cv, d.ts, d.ys, d.color, d.key, -1); return; }
  const r = cv.getBoundingClientRect(); const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
  const i = Math.max(0, Math.min(d.ys.length - 1, Math.round((x - gm.padL) / gm.iw * (d.ys.length - 1))));
  drawTrend(cv, d.ts, d.ys, d.color, d.key, i);
  const t = new Date(d.ts[i] * 1000);
  tip.innerHTML = `${t.getMonth() + 1}/${t.getDate()} ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')} · <b>${fmt(d.ys[i], d.key === 'temp' ? 1 : 0)} ${TREND_UNIT[d.key]}</b>`;
  tip.style.display = 'block'; tip.style.right = 'auto';
  // place the tooltip at the hovered point (above the marker), flipping near the edges
  const px = gm.X(i), py = gm.Y(d.ys[i]);
  const bw = box.clientWidth, tw = tip.offsetWidth, th_ = tip.offsetHeight, ox = cv.offsetLeft, oy = cv.offsetTop;
  let left = ox + px - tw / 2; left = Math.max(4, Math.min(bw - tw - 4, left));
  const top = 2;                                        // pinned to the very top of the trend card; only x follows the cursor
  tip.style.left = left + 'px'; tip.style.top = top + 'px';
}
$$('.trend canvas').forEach(cv => { cv.addEventListener('mousemove', trendHover); cv.addEventListener('mouseleave', trendHover); cv.addEventListener('touchstart', trendHover, { passive: true }); cv.addEventListener('touchmove', trendHover, { passive: true }); cv.addEventListener('touchend', trendHover); });
window.addEventListener('resize', () => { if (trendOpen && trendData) loadTrend(); });

// ---------------------------------------------------------------- data tab
// ---- config history / run snapshots (data tab)
const tsK = (t) => t ? new Date(t * 1000).toLocaleString('ko-KR', { hour12: false }) : '-';
const jshort = (v) => { const t = JSON.stringify(v); return t === undefined ? '-' : (t.length > 60 ? t.slice(0, 57) + '…' : t); };
async function loadConfigHistory() {
  const q = ($('#cfgHistQ') || {}).value || '';
  try {
    const [h, r] = await Promise.all([api('/config/history?limit=300&path=' + encodeURIComponent(q)), api('/runs?limit=50')]);
    const sm = $('#cfgHistSummary'); if (sm) sm.textContent = `변경 ${h.total}건 · 실행 ${r.runs.length}회${r.current ? ' · 현재 실행 #' + r.current : ''}`;
    $('#runsTable tbody').innerHTML = r.runs.map(x => `<tr class="${x.id === r.current ? 'sel' : ''}"><td>${x.id}</td><td>${tsK(x.started_at)}</td><td>${x.stopped_at ? tsK(x.stopped_at) : '<span class="tag ok">진행 중</span>'}</td><td class="mono">${esc(x.target || '-')}</td><td>${x.workers ?? '-'}</td><td>${x.patients ?? '-'}</td><td>${x.gateways ?? '-'}</td><td>${fmt(x.pkts)}</td><td>${fmt(x.drops)}</td><td>${x.has_config ? `<button class="small" data-restore-run="${x.id}">이 설정으로 복원</button> <button class="small" data-show-run="${x.id}">보기</button>` : '<span class="sub">스냅샷 없음</span>'}</td></tr>`).join('') || '<tr><td colspan="10" class="sub">실행 기록 없음</td></tr>';
    $('#cfgHistTable tbody').innerHTML = h.items.map(x => `<tr><td>${tsK(x.t)}</td><td><span class="tag">${esc(x.source)}</span></td><td>${x.run_id ?? '-'}</td><td class="mono">${esc(x.path)}</td><td class="mono sub">${esc(jshort(x.old))}</td><td class="mono">${esc(jshort(x.new))}</td><td><button class="small" data-restore-h="${x.id}" title="이 항목을 이전값 ${esc(jshort(x.old))} 으로">되돌리기</button></td></tr>`).join('') || '<tr><td colspan="7" class="sub">변경 이력 없음</td></tr>';
  } catch (e) { }
}
document.addEventListener('click', async e => {
  const rr = e.target.closest('[data-restore-run]'), rh = e.target.closest('[data-restore-h]'), sr = e.target.closest('[data-show-run]');
  if (sr) { try { const r = await api('/runs/' + sr.dataset.showRun + '/config'); const g = r.config.general, sc = r.config.scenario; toast(`실행 #${r.run_id}: 환자 ${g.active_patients}명 · 원외 ${g.outpatient_count} · 병상 ${g.bed_capacity} · ECG ${r.config.signals.ecg_fs} Hz · 네트워크 ${sc.network.enabled ? sc.network.intensity + '%' : 'OFF'} · 아티팩트 ${sc.artifacts.enabled ? sc.artifacts.intensity + '%' : 'OFF'} · 대상 ${r.config.transport.target_ip || '-'}:${r.config.transport.target_port}`); } catch (er) { toast('스냅샷을 읽지 못했습니다'); } return; }
  if (!rr && !rh) return;
  const what = rr ? `실행 #${rr.dataset.restoreRun}의 설정 전체로 되돌립니다` : `변경 #${rh.dataset.restoreH} 항목을 이전값으로 되돌립니다`;
  if (!await askConfirm('설정 복원', what + '. 계속할까요?', '복원')) return;
  try {
    const r = await post('/config/restore', rr ? { run_id: Number(rr.dataset.restoreRun) } : { history_id: Number(rh.dataset.restoreH) });
    toast('복원 완료' + (r.needs_rebuild ? ' · 구조 설정 변경: [병원·환자 재구성] 필요' : '') + (r.needs_generate ? ' · 루프 은행 재생성 필요' : ''));
    await loadConfig(); loadConfigHistory();
  } catch (er) { toast('복원 실패: ' + er.message); }
});
$('#cfgHistReload').onclick = loadConfigHistory; $('#cfgHistQ').addEventListener('input', () => { clearTimeout(window.__cfgHistT); window.__cfgHistT = setTimeout(loadConfigHistory, 300); });
setInterval(() => { if ($('[data-tab="data"]').classList.contains('on') && !isFolded('cfgHistBody')) loadConfigHistory(); }, 15000);
async function loadBankFiles() {
  try {
    const c = await api('/signals/catalog'); const b = c.bank;
    $('#bankFiles').innerHTML = `<div>${esc(b.dir)}</div>` + Object.entries(b.files).map(([k, v]) => `<div>${k}: ${esc(v.split('/').pop())}</div>`).join('') + `<div style="margin-top:6px">변형 ${c.variants.length}개 · 리듬 ${Object.keys(c.rhythms).length}종 · 활동 템플릿 ${c.activities.length}종</div>`;
  } catch (e) { }
}

// ---------------------------------------------------------------- patch registry (data tab)
let regOff = 0;
async function loadRegistry() {
  const st = $('#regStatus').value, q = $('#regQ').value.trim();
  const so = regSort.get(); regSort.mark();
  const r = await api(`/emr/patches?status=${st}&q=${encodeURIComponent(q)}&offset=${regOff}&limit=60&sort=${so.key}&dir=${so.dir === 1 ? 'asc' : 'desc'}`);
  $('#regCount').textContent = `사용 중 ${fmt(r.active)} · 사용 완료 ${fmt(r.retired)} · 다음 발급 번호 ${r.next_serial} · 표시 ${regOff + 1}-${Math.min(r.total, regOff + 60)} / ${fmt(r.total)}`;
  $('#regTable tbody').innerHTML = r.patches.map(p => `<tr><td class="mono">${p.serial}</td><td>${p.patch_id}</td><td>${p.status === 'active' ? '<span class="tag ok">사용 중</span>' : '<span class="tag">사용 완료</span>'}</td><td>${p.patient_name || '-'} <span class="sub">#${p.patient_id || '-'}</span></td><td>${p.issued_at ? p.issued_at.slice(5, 16) : '-'}</td><td>${esc(p.issued_reason || '')}</td><td>${p.retired_at ? p.retired_at.slice(5, 16) : '-'}</td><td>${esc(p.retired_reason || '')}</td><td>${p.battery == null ? '-' : battIcon(p.battery)}</td></tr>`).join('');
}
const regSort = makeSortable('regTable', 'regSort', { key: 'patch_id', dir: -1 }, () => { regOff = 0; loadRegistry(); });
$('#regStatus').onchange = $('#regQ').oninput = () => { regOff = 0; loadRegistry(); };
$('#regPrev').onclick = () => { regOff = Math.max(0, regOff - 60); loadRegistry(); };
$('#regNext').onclick = () => { regOff += 60; loadRegistry(); };

// ---------------------------------------------------------------- SQLite console (data tab)
async function loadDb() {
  try { const d = await api('/db/stats'); $('#dbPath').textContent = `${d.path} · ${d.size_mb} MB`; $('#dbKpis').innerHTML = Object.entries(d.tables).map(([k, v]) => kpi(k, fmt(v))).join(''); } catch (e) { }
}
let dbRes = null, dbSort = { key: '', dir: 1 };
function renderDb() {
  if (!dbRes) return;
  const ci = dbRes.columns.indexOf(dbSort.key);
  const rows = dbRes.rows.slice();
  if (ci >= 0) rows.sort((a, b) => { const x = a[ci], y = b[ci]; if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; const r_ = (typeof x === 'number' && typeof y === 'number') ? x - y : String(x).localeCompare(String(y), 'ko', { numeric: true }); return r_ * dbSort.dir; });
  $('#dbTable thead').innerHTML = '<tr>' + dbRes.columns.map(c => `<th data-key="${esc(c)}" class="${c === dbSort.key ? (dbSort.dir === 1 ? 'asc' : 'desc') : ''}">${esc(c)}</th>`).join('') + '</tr>';
  $('#dbTable tbody').innerHTML = rows.map(row => '<tr>' + row.map(v => `<td>${v == null ? '' : esc(typeof v === 'number' && v > 1e9 && v < 2e9 ? new Date(v * 1000).toLocaleString('ko-KR', { hour12: false }) : v)}</td>`).join('') + '</tr>').join('');
}
$('#dbTable thead').addEventListener('click', e => { const th = e.target.closest('th[data-key]'); if (!th) return; if (dbSort.key === th.dataset.key) dbSort.dir *= -1; else dbSort = { key: th.dataset.key, dir: 1 }; renderDb(); if (dbRes) $('#dbMsg').textContent = `${dbRes.rows.length}행${dbRes.truncated ? ' (200행 제한)' : ''} · 정렬 ${dbSort.key} ${dbSort.dir === 1 ? '▲' : '▼'} (조회된 행 안에서)`; });
$('#dbRun').onclick = async () => {
  try {
    const r = await post('/db/query', { sql: $('#dbSql').value, limit: 200 });
    dbRes = r; if (!r.columns.includes(dbSort.key)) dbSort = { key: '', dir: 1 };
    renderDb();
    $('#dbMsg').textContent = `${r.rows.length}행${r.truncated ? ' (200행 제한)' : ''}${dbSort.key ? ` · 정렬 ${dbSort.key}` : ' · 열 머리글을 누르면 정렬'}`;
  } catch (e) { $('#dbMsg').textContent = '오류: ' + e.message; }
};
$('#dbSql').addEventListener('keydown', e => { if (e.key === 'Enter') $('#dbRun').click(); });

// ---------------------------------------------------------------- protocol
const esc = (x) => String(x).replace(/&/g, '&amp;').replace(/</g, '&lt;');
const kvCard = (title, rows, extra = '') => `<div class="card"><h3>${title}</h3><div class="kv">${rows.map(([k, v]) => `<b>${esc(k)}</b><span>${v}</span>`).join('')}</div>${extra}</div>`;
const ul = (items) => `<ul>${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>`;
const flagList = (o) => ul(Object.entries(o).map(([k, v]) => `${k} = ${v}`));
async function loadProto() {
  const d = await api('/'); $('#protoRaw').textContent = JSON.stringify(d, null, 2);
  const sp = d.stream_protocol, tr = sp.transport;
  $('#protoSteps').innerHTML = d.how_to_start.map(s => `<li>${esc(s.replace(/^\d+\.\s*/, ''))}</li>`).join('');
  $('#protoCards').innerHTML =
    kvCard('전송', [['프로토콜', `${tr.type.toUpperCase()} · ${d.transport.socket_mode}`], ['대상', `${d.transport.target_ip || '(미지정: 생성만)'}:${d.transport.target_port}`],
      ['번들 주기', `${tr.bundle_ms} ms`], ['프레이밍', esc(tr.framing)], ['keepalive', esc(tr.keepalive)], ['META 주기', `${d.transport.meta_every_n_frames} 프레임`], ['GW 상태 주기', `${d.transport.gw_status_every_n_frames} 프레임`],
      ['최대 규모', `게이트웨이 ${d.transport.max_gateways} · 패치 ${d.transport.max_patches} · GW당 BLE ${d.transport.gateway_capacity}`]]) +
    kvCard('프레임 헤더', [['구조', `<span class="mono">${esc(sp.frame_header.struct)}</span> (${sp.frame_header.size} B, little-endian)`], ['필드', ul(sp.frame_header.fields)], ['flags', flagList(sp.frame_header.flags)], ['페이로드 순서', ul(sp.payload_order)]]) +
    kvCard('GW 상태 블록', [['구조', `<span class="mono">${esc(sp.gw_status_block.struct)}</span> (${sp.gw_status_block.size} B)`], ['필드', ul(sp.gw_status_block.fields)], ['주기', `${sp.gw_status_block.every_n_frames} 프레임마다 (게이트웨이별 시차)`]]) +
    kvCard('META 블록', [['구조', esc(sp.meta_block.struct)], ['주기', `${sp.meta_block.every_n_frames} 프레임 + 재연결 직후`], ['설명', esc(sp.meta_block.note)], ['JSON 스키마', `<pre class="mono" style="max-height:220px;margin:0">${esc(JSON.stringify(sp.meta_block.json_schema, null, 1))}</pre>`]]) +
    kvCard('레코드', [['헤더', ul(sp.record.header)], ['채널 블록', ul(sp.record.channel_block)], ['flags', flagList(sp.record.flags)], ['비고', esc(sp.record.note)], ['정렬', esc(sp.sample_alignment)]]) +
    kvCard('식별자', Object.entries(d.ids).map(([k, v]) => [k, esc(v)])) +
    kvCard('현재 신호 설정', [['활성 채널', Object.entries(d.signals.enabled).filter(([, v]) => v).map(([k]) => k).join(', ')], ['샘플링', Object.entries(d.signals.sampling).map(([k, v]) => `${k} ${v} Hz`).join(' · ')],
      ['호흡수 소스', d.signals.resp_source], ['SpO2 소스', d.signals.spo2_source], ['루프 길이', `${d.signals.loop_seconds} s`]]);
  $('#protoChan tbody').innerHTML = sp.channels.map(c => `<tr><td>${c.id}</td><td class="mono">${c.key}</td><td>${c.name}</td><td>${c.unit}</td><td>${c.dtype}(${c.dtype_code})${c.axes > 1 ? '×' + c.axes : ''}</td><td>${c.scale}</td><td>${c.fs ? c.fs + ' Hz' : (c.period_ms ? '1/s' : '-')}</td><td>${c.samples_per_frame || '-'}</td><td>${esc(c.note || '')}</td></tr>`).join('');
  const rows = [];
  for (const [g, v] of Object.entries(d.endpoints)) {
    if (typeof v === 'string') rows.push(['', g, v]); else for (const [k, path] of Object.entries(v)) rows.push([g, k, path]);
  }
  $('#protoEp tbody').innerHTML = rows.map(([g, k, v]) => `<tr><td>${esc(g)}</td><td>${esc(k)}</td><td class="mono">${esc(v)}</td></tr>`).join('');
}

// ---------------------------------------------------------------- clearable search inputs (X button)
function makeClearable(input) {
  if (!input || input.closest('.clr')) return;
  const wrap = document.createElement('div'); wrap.className = 'clr';
  input.parentNode.insertBefore(wrap, input); wrap.appendChild(input);
  const b = document.createElement('button'); b.type = 'button'; b.textContent = '×'; b.title = '지우기'; b.setAttribute('aria-label', '검색어 지우기'); wrap.appendChild(b);
  const m = document.createElement('button'); m.type = 'button'; m.className = 'mag'; m.title = '검색'; m.setAttribute('aria-label', '검색');
  m.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><line x1="15.5" y1="15.5" x2="21" y2="21"/></svg>';
  m.addEventListener('click', () => { input.focus(); if (input.id === 'dbSql') $('#dbRun').click(); else input.dispatchEvent(new Event('input', { bubbles: true })); });
  wrap.appendChild(m);
  const upd = () => wrap.classList.toggle('has', input.value.length > 0);
  input.addEventListener('input', upd);
  b.addEventListener('click', () => { input.value = ''; upd(); input.dispatchEvent(new Event('input', { bubbles: true })); input.focus(); });
  upd();
}
['patQ', 'patSearch', 'regQ', 'dbSql', 'mapQ'].forEach(id => makeClearable(document.getElementById(id)));

// ---------------------------------------------------------------- init
async function loadTemplates() {
  try { const t = await api('/emr/layout/templates'); const s = $('#h_tpl'); const cur = CFG.hospital ? CFG.hospital.template : 'auto';
    s.innerHTML = `<option value="auto">자동 (환자 수에 맞춰 선택 → ${t.selected_by_capacity})</option>` + t.templates.map(x => `<option value="${x.key}">${x.name} · ≤${x.max_beds}병상 · 최대 ${x.floors_max}층</option>`).join(''); s.value = cur; syncDropdowns(); } catch (e) { }
}
async function loadConfig() { META = await api('/config'); CFG = META.config; loadTemplates();
  const rs = $('#selRespSrc'); rs.innerHTML = META.resp_sources.map(x => `<option value="${x}">${{ capacitive: '용량성 호흡 센서', edr: 'ECG 유도 호흡 (EDR)', spo2: 'SpO2/PPG 유도 (SpO2 기기 있는 환자만)' }[x]}</option>`).join('');
  fillForm(); setupWaves(); }
(async () => {
  await loadConfig();
  $$('select').forEach(sel => makeDropdown(sel, { cls: sel.id === 'tabSelect' ? 'tabdd' : '', compact: sel.id === 'gwFilter' || sel.id === 'logFilter', placeholder: sel.id === 'selPatient' ? '환자를 선택하세요' : '선택' }));
  $('#btnTheme').onclick = () => setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
  $('#btnTheme').textContent = document.documentElement.dataset.theme === 'light' ? '☀️' : '🌙';
  // deep links for docs / screenshots: ?tab=hosp  ?tab=hosp&gw=88[&preset=2x2]  ?tab=sig
  const qs = new URLSearchParams(location.search); const qTab = qs.get('tab');
  showTab(qTab && tabs.some(t => t.dataset.tab === qTab) ? qTab : initTab);
  if (qs.get('gw') !== null) setTimeout(() => { if (qs.get('preset')) { const x = CS_PRESETS.find(y => y.id === qs.get('preset')); mon.preset = x && x.c ? x : null; } openGatewayMonitor(Number(qs.get('gw'))); if (qs.get('bed')) setTimeout(() => vmOpen(Number(qs.get('bed'))), 2500); }, 2500);
  await loadDevices(); await refresh(); loadPatientList(); loadFloors();
  setInterval(refresh, 1000); setInterval(() => { if ($('[data-tab="sig"]').classList.contains('on')) loadPatCard(); }, 5000);
  window.addEventListener('resize', () => Object.values(waves).forEach(st => { st.w = 0; }));
  requestAnimationFrame(animate);
  window.__animate = animate;   // debug hook (rAF is paused in hidden tabs)
})();
})();

// ---- collapsible cards: ▾ buttons fold the table under the heading (state kept per browser)
document.querySelectorAll('[data-goto]').forEach(b => { b.onclick = () => { try { document.querySelector(`#tabs button[data-tab="${b.dataset.goto}"]`).click(); window.scrollTo({ top: 0 }); } catch (e) { } }; });   // manual: jump to a tab
document.querySelectorAll('.fold[data-fold]').forEach(b => {
  const key = 'fold:' + b.dataset.fold; let open = true;
  try { open = localStorage.getItem(key) !== '0'; } catch (e) { }
  const apply = () => { const body = document.getElementById(b.dataset.fold); if (body) body.hidden = !open; b.setAttribute('aria-expanded', String(open)); document.dispatchEvent(new CustomEvent('fold', { detail: { id: b.dataset.fold, open } })); };
  const toggle = () => { open = !open; try { localStorage.setItem(key, open ? '1' : '0'); } catch (e) { } apply(); };
  b.onclick = (e) => { e.stopPropagation(); toggle(); };
  const h = b.closest('h3');                                        // the whole heading row is the click target, not just the ▾ icon
  if (h) { h.classList.add('foldrow'); h.addEventListener('click', e => { if (e.target.closest('select, .dd, input, a, button:not(.fold), [data-nofold]')) return; toggle(); }); }
  apply();
});
function isFolded(id) { const b = document.querySelector(`.fold[data-fold="${id}"]`); return !!b && b.getAttribute('aria-expanded') === 'false'; }   // hoisted: fold events fire before this line runs
