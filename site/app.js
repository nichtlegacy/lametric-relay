// Renders the 37x8 display and drives the scenario picker.
//
// Nothing here decides what the clock shows. The frames come from preview.json,
// which scripts/build-preview.py generates by running the actual add-ins, so this
// file only has to draw them and reproduce the hub's timing.

import { raster, measure, GLYPH_ROWS } from './font.js';

const REPO = 'https://github.com/nichtlegacy/lametric-relay';

const W = 37;              // display width in pixels
const H = 8;               // display height in pixels
const ICON = 8;            // the left colour zone
const TEXT_X = 9;          // text starts one pixel clear of the icon zone
const GAP = 0.16;          // share of a cell left dark, so the LEDs read as dots

// Undocumented on the device, so these are tuned by eye rather than measured.
const HOLD_MS = 2200;      // a frame that fits stays this long
const LEAD_MS = 900;       // pause before a long frame starts moving
const TAIL_MS = 1250;      // pause at the end, so the jump back is not a snap
const SCROLL_PPS = 17;     // scroll speed in pixels per second
const STEP_MS = 650;       // one press of the manual transport

const ROUTES = {
  notification: {
    label: 'Notification',
    hint: 'Something happened and is worth interrupting for.',
  },
  widget: {
    label: 'DIY widget',
    hint: 'Something is true right now, one swipe from the clock face.',
  },
  clockface: {
    label: 'Clock face',
    hint: 'Ambient context. No text, no interruption.',
  },
  custom: {
    label: 'Custom',
    hint: 'Your frame, rendered the way the device would render it.',
  },
};

// A scenario the build did not generate. It carries the same shape as the others
// so the readout and the detail panel do not need a special case.
const CUSTOM = {
  id: '__custom',
  route: 'custom',
  addin: null,
  title: 'Compose a frame',
  source: 'Live in the browser, nothing is sent anywhere',
  note: 'Type, pick an icon, choose a frame type. The width readout counts real '
    + 'pixels: anything past the text zone scrolls on the device, exactly as it '
    + 'does here. Icons past the project\u2019s own set are the most popular ones in '
    + 'the LaMetric community library, animations included.',
  priority: 'info',
  cycles: 1,
  frames: [],
};

const $ = (id) => document.getElementById(id);
const canvas = $('screen');
const ctx = canvas.getContext('2d', { alpha: false });

const state = {
  data: null,
  route: 'notification',
  scenario: null,
  quiet: false,
  allowCritical: false,
  cycling: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  frame: 0,
  phase: 0,          // ms spent on the current frame
  last: 0,
};

/* --- Icons --------------------------------------------------------------- */

// Icons arrive decoded: one string of palette indices per frame, plus delays.
// Browsers only advance a GIF they are actually painting, so drawing one into a
// canvas gives you frame 0 forever. Owning the frames means owning the clock.
const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
const icons = new Map();

function prepareIcons(list) {
  for (const icon of list) {
    icon.total = icon.delays.reduce((a, b) => a + b, 0);
    icon.rgb = icon.palette.map((hex) => [
      parseInt(hex.slice(1, 3), 16),
      parseInt(hex.slice(3, 5), 16),
      parseInt(hex.slice(5, 7), 16),
    ]);
    icons.set(icon.ref, icon);
  }
}

/** Which frame of an icon is showing at time `t`, looping on its own delays. */
function iconFrame(icon, t) {
  if (icon.frames.length < 2) return 0;
  let left = t % icon.total;
  let i = 0;
  while (left >= icon.delays[i] && i < icon.frames.length - 1) {
    left -= icon.delays[i];
    i += 1;
  }
  return i;
}

/** One LED. Lit pixels get a second, larger pass at low alpha as bloom. */
function led(x, y, size, color, lit) {
  const pad = size * GAP;
  const s = size - pad * 2;
  const px = x * size + pad;
  const py = y * size + pad;
  const r = Math.max(1, s * 0.22);

  if (lit) {
    ctx.globalAlpha = 0.18;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(px - pad, py - pad, s + pad * 2, s + pad * 2, r * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  ctx.fillStyle = lit ? color : theme.off;
  ctx.beginPath();
  ctx.roundRect(px, py, s, s, r);
  ctx.fill();
}

// Read once per frame rather than per pixel: 296 getComputedStyle calls a frame
// would be the most expensive thing on the page by a wide margin.
const theme = { screen: '#0b0b0c', off: '#17171a' };

function readTheme() {
  const css = getComputedStyle(document.documentElement);
  theme.screen = css.getPropertyValue('--screen').trim() || theme.screen;
  theme.off = css.getPropertyValue('--screen-off').trim() || theme.off;
}

/* --- Drawing a frame ----------------------------------------------------- */

function clear(size) {
  ctx.fillStyle = theme.screen;
  ctx.fillRect(0, 0, W * size, H * size);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) led(x, y, size, '#000', false);
  }
}

function drawIcon(ref, size, t) {
  const icon = icons.get(ref);
  if (!icon) return;
  const frame = icon.frames[iconFrame(icon, t)];
  for (let i = 0; i < frame.length; i++) {
    const ch = frame[i];
    if (ch === '.') continue;
    led(i % ICON, (i / ICON) | 0, size, icon.palette[ALPHABET.indexOf(ch)], true);
  }
}

function drawText(text, size, offset, zoneX, zoneW) {
  const { width, grid } = raster(text);
  if (width <= zoneW) offset = -Math.floor((zoneW - width) / 2);
  for (let gy = 0; gy < GLYPH_ROWS; gy++) {
    for (let gx = 0; gx < width; gx++) {
      if (!grid[gy][gx]) continue;
      const x = zoneX + gx - offset;
      if (x < zoneX || x >= zoneX + zoneW) continue;   // clipped by the zone
      led(x, gy + 1, size, '#ffffff', true);
    }
  }
}

/** The device draws goalData along the bottom row when it also has text. */
function drawBar(goal, size, zoneX, zoneW) {
  const span = Math.max(0, Math.min(1, (goal.current - goal.start) / (goal.end - goal.start)));
  const lit = Math.round(span * zoneW);
  for (let i = 0; i < lit; i++) led(zoneX + i, H - 1, size, '#ffffff', true);
}

/* --- The loop ------------------------------------------------------------ */

function currentFrames() {
  const s = state.scenario;
  if (!s) return [];
  if (s.route === 'custom') return s.frames;
  if (s.route === 'clockface') {
    const now = new Date();
    const time = `${now.getHours()}:${String(now.getMinutes()).padStart(2, '0')}`;
    return [{ icon: s.face, text: time }];
  }
  return s.frames || [];
}

function timing(frame, zoneW) {
  const { width } = raster(String(frame.text ?? ''));
  const overflow = Math.max(0, width - zoneW);
  if (!overflow) return { overflow: 0, duration: HOLD_MS };
  return { overflow, duration: LEAD_MS + (overflow / SCROLL_PPS) * 1000 + TAIL_MS };
}

function zone(frame) {
  const x = frame.icon ? TEXT_X : 1;
  return { x, w: W - x };
}

function isMuted() {
  const s = state.scenario;
  if (!s || !state.quiet) return false;
  if (s.route === 'custom') {
    // The verdict table is produced by calling hub.muted() at build time, so the
    // composer applies the hub's rule rather than a copy of it.
    const v = state.data.quiet_verdict[s.priority] ?? {};
    return state.allowCritical ? v.muted_allow_critical : v.muted;
  }
  if (s.route !== 'notification') return false;
  return state.allowCritical ? s.muted_allow_critical : s.muted;
}

function render(now) {
  requestAnimationFrame(render);

  const size = canvas.width / W;
  const dt = state.last ? Math.min(now - state.last, 120) : 0;
  state.last = now;

  paintGrid(now);
  clear(size);
  if (isMuted()) return;                 // quiet hours: the clock shows nothing

  const frames = currentFrames();
  if (!frames.length) return;

  const frame = frames[Math.min(state.frame, frames.length - 1)];
  const { x: zoneX, w: zoneW } = zone(frame);
  const { overflow, duration } = timing(frame, zoneW);

  if (state.cycling) state.phase += dt;

  // A frame that fits is held; one that does not scrolls once, then waits before
  // the loop starts over, because a cut straight back to the start reads as a
  // glitch rather than a repeat.
  const t = state.phase - LEAD_MS;
  const offset = overflow
    ? Math.round(Math.max(0, Math.min(overflow, (t / 1000) * SCROLL_PPS)))
    : 0;

  if (state.cycling && state.phase >= duration) {
    state.phase = 0;
    state.frame = (state.frame + 1) % frames.length;
    dots();
  }

  if (frame.icon) drawIcon(frame.icon, size, now);
  if (frame.goalData) drawBar(frame.goalData, size, zoneX, zoneW);
  if (frame.text) drawText(String(frame.text), size, offset, zoneX, zoneW);
}

/* --- Transport ----------------------------------------------------------- */

/** Move along the same timeline the player uses: scrub a long frame, then step. */
function step(dir) {
  const frames = currentFrames();
  if (!frames.length) return;
  state.phase += dir * STEP_MS;

  const here = () => timing(frames[state.frame], zone(frames[state.frame]).w).duration;
  if (state.phase >= here()) {
    state.frame = (state.frame + 1) % frames.length;
    state.phase = 0;
  } else if (state.phase < 0) {
    state.frame = (state.frame - 1 + frames.length) % frames.length;
    state.phase = Math.max(0, here() - STEP_MS);
  }
  dots();
}

function dots() {
  const box = $('dots');
  const frames = currentFrames();
  if (box.children.length !== frames.length) {
    box.replaceChildren(...frames.map(() => {
      const d = document.createElement('span');
      d.className = 'dot-frame';
      return d;
    }));
  }
  [...box.children].forEach((d, i) => d.toggleAttribute('data-on', i === state.frame));
}

/* --- Picker -------------------------------------------------------------- */

const INPUT_LABEL = {
  notification: 'Payload',
  widget: 'Add-in state',
  clockface: 'Configuration',
};

/** How to produce this frame yourself. Never empty: an empty block would move
    everything below it whenever the scenario changed. */
function body(payload) {
  return JSON.stringify(payload, null, 2)
    .split('\n')
    .map((line, i) => (i ? `  ${line}` : line))
    .join('\n');
}

function reproduce(s) {
  // A widget renders from state, so the request that fills it is a separate
  // document from the state itself.
  const sent = s.trigger ?? s.payload;
  if (s.source?.includes('webhook') && sent) {
    return {
      label: 'Send it yourself',
      text: `curl -X POST http://<hub-host>:8099/hook/${s.addin} \\\n` +
        `  -H 'Content-Type: application/json' \\\n` +
        `  -d '${body(sent)}'`,
    };
  }
  if (s.addin === 'hub') {
    return {
      label: 'Provoke it',
      text: 'RATE_MAX=4 RATE_WINDOW=60 ./hub.py\n' +
        '# then send more than four events inside the window',
    };
  }
  return {
    label: 'Trigger a poll',
    text: `# the hub polls this add-in every ${s.interval ?? 120}s\n` +
      './hub.py --once',
  };
}

function select(id) {
  const s = state.data.scenarios.find((x) => x.id === id);
  if (!s || s === state.scenario) return;

  state.scenario = s;
  state.frame = 0;
  state.phase = 0;

  for (const btn of document.querySelectorAll('.chip')) {
    btn.setAttribute('aria-pressed', String(btn.dataset.id === id));
  }

  const detail = document.querySelector('.detail');
  detail.dataset.swapping = 'true';
  setTimeout(() => {
    $('d-title').textContent = s.title;
    $('d-source').textContent = `${addinLabel(s.addin)} · ${s.source}`;
    $('d-note').textContent = s.note;

    $('d-payload').firstElementChild.textContent = JSON.stringify(s.payload ?? {}, null, 2);
    $('d-payload-label').textContent =
      s.addin === 'hub' ? 'Configuration' : INPUT_LABEL[s.route];

    const how = reproduce(s);
    $('d-curl-label').textContent = how.label;
    $('d-curl').firstElementChild.textContent = how.text;

    const file = s.addin === 'hub' ? 'hub.py' : `addins/${s.addin}.py`;
    const link = $('d-file');
    link.href = `${REPO}/blob/main/${file}`;
    link.firstElementChild.textContent = file;

    detail.dataset.swapping = 'false';
  }, 150);

  readout();
  dots();
}

function readout() {
  const s = state.scenario;
  const frames = currentFrames();
  const muted = isMuted();

  $('route-badge').textContent = ROUTES[s.route].label;

  const prio = $('priority-badge');
  prio.hidden = !(s.route === 'notification' || s.route === 'custom');
  prio.textContent = s.priority ?? 'info';
  prio.dataset.level = s.priority ?? 'info';

  const count = frames.length;
  const plural = `${count} frame${count === 1 ? '' : 's'}`;
  $('frame-count').textContent =
    s.route === 'notification' ? `${plural} · ${s.cycles ?? 1}×` : plural;

  const status = $('status');
  status.dataset.muted = String(muted);
  status.textContent = muted
    ? state.allowCritical
      ? 'Dropped. Quiet hours are active and this event is not critical.'
      : 'Dropped. Quiet hours are active and critical events are not exempt.'
    : ROUTES[s.route].hint;
}

/* --- Composer ------------------------------------------------------------ */

// One definition for both the initial state and the reset, so they cannot drift.
const COMPOSER_DEFAULTS = { text: 'Nothing is on fire', mode: 'text', pct: 73,
                            priority: 'info', icon: 'assets/kuma.png' };

const composer = { mode: 'text', pct: 73, priority: 'info', icon: null };

/** Build the frame, the readout and both code blocks from the current controls. */
function composerUpdate() {
  const text = $('c-text').value;
  const icon = composer.icon;
  const withText = composer.mode !== 'bar';
  const withBar = composer.mode !== 'text';

  const frame = {};
  if (icon) frame.icon = icon.ref;
  if (withText) frame.text = text;
  if (withBar) {
    frame.goalData = { start: 0, current: composer.pct, end: 100, unit: '' };
  }

  CUSTOM.frames = [frame];
  CUSTOM.priority = composer.priority;
  state.frame = 0;

  // Width against the real zone: 28 px next to an icon, 36 px without one.
  const zoneW = W - (icon ? TEXT_X : 1);
  const px = withText ? measure(text) : 0;
  $('c-width').textContent = withText
    ? `${px} of ${zoneW} px · ${px > zoneW ? 'scrolls' : 'fits'}`
    : `bar only · ${zoneW} px wide`;
  $('c-width').dataset.over = String(px > zoneW);
  $('c-reset').disabled = pristine();

  const name = $('c-icon-name');
  name.textContent = icon ? icon.title : 'none';
  // Only a community icon has somewhere to credit; the project's own do not.
  if (icon?.page) name.href = icon.page;
  else name.removeAttribute('href');
  $('c-pct').textContent = `${composer.pct}%`;
  $('c-value').disabled = !withBar;

  const ref = icon ? icon.ref : null;
  $('d-payload').firstElementChild.textContent =
    eventSnippet(ref, withText && text, withBar && composer.pct, composer.priority);

  $('d-curl').firstElementChild.textContent =
    composerCommand(frame, ref, text, composer.priority, withBar);

  if (state.scenario === CUSTOM) {
    readout();
    dots();
  }
}

const cells = [];

/** How to put the composed frame on a real clock. The CLI has no flag for a
    progress bar, so anything with one goes straight at the device API -- the
    same request lametric.call() makes. */
function composerCommand(frame, ref, text, priority, withBar) {
  if (!withBar) {
    return `./lametric.py notify ${JSON.stringify(text)}`
      + (ref ? ` --icon ${ref}` : '')
      + (priority === 'info' ? '' : ` --priority ${priority}`);
  }
  return [
    'curl -k -u dev:$LAMETRIC_KEY -X POST \\',
    '  https://$LAMETRIC_HOST:4343/api/v2/device/notifications \\',
    "  -H 'Content-Type: application/json' \\",
    `  -d '${body({
        priority,
        icon_type: 'none',
        model: { frames: [{ ...frame, icon: ref ?? undefined }], cycles: 1 },
      })}'`,
  ].join('\n');
}

/** What an add-in would return for this frame. The device wants base64, which
    lametric.icon() produces, so the snippet quotes the source reference rather
    than a 300-character data URI. */
function eventSnippet(ref, text, pct, priority) {
  const parts = [];
  if (ref) parts.push(`"icon": lametric.icon(${JSON.stringify(ref)})`);
  if (text !== false) parts.push(`"text": ${JSON.stringify(text)}`);
  if (pct !== false) {
    parts.push(`"goalData": {"start": 0, "current": ${pct}, "end": 100, "unit": ""}`);
  }
  const body = parts.length ? `\n    ${parts.join(',\n    ')}\n  ` : '';
  return `return [{\n  "frames": [{${body}}],\n  "priority": ${JSON.stringify(priority)}\n}]`;
}

/** Is everything already at its default? Then there is nothing to reset. */
function pristine() {
  return $('c-text').value === COMPOSER_DEFAULTS.text
    && composer.mode === COMPOSER_DEFAULTS.mode
    && composer.pct === COMPOSER_DEFAULTS.pct
    && composer.priority === COMPOSER_DEFAULTS.priority
    && composer.icon?.ref === COMPOSER_DEFAULTS.icon;
}

/** Point a segmented control at one of its options. */
function pick(bar, attr, value) {
  for (const seg of $(bar).children) {
    seg.setAttribute('aria-selected', String(seg.dataset[attr] === value));
  }
}

function resetComposer() {
  const d = COMPOSER_DEFAULTS;
  $('c-text').value = d.text;
  $('c-value').value = d.pct;
  composer.mode = d.mode;
  composer.pct = d.pct;
  composer.priority = d.priority;
  pick('c-mode', 'mode', d.mode);
  pick('c-priority', 'priority', d.priority);
  pickIcon(icons.get(d.icon) ? state.data.icons.find((i) => i.ref === d.icon)
                             : state.data.icons[0]);
}

/** A different one every time: drawing the value you already have reads as a
    button that did nothing. */
function other(pool, current) {
  if (pool.length < 2) return pool[0];
  let next = current;
  while (next === current) next = pool[Math.floor(Math.random() * pool.length)];
  return next;
}

function surprise() {
  const text = other(state.data.texts, $('c-text').value);
  $('c-text').value = text;

  // A text that quotes a percentage should move the bar with it, so the two
  // halves of the frame agree even when the bar is switched on afterwards.
  const pct = text.match(/(\d{1,3})\s*%/);
  if (pct) {
    composer.pct = Math.min(100, Number(pct[1]));
    $('c-value').value = composer.pct;
  }

  pickIcon(other(state.data.icons, composer.icon));   // also runs composerUpdate
}

function buildIconGrid() {
  const grid = $('c-icons');
  for (const icon of state.data.icons) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'icon-cell';
    btn.title = `${icon.title}${icon.own ? '' : ' · ' + icon.ref}`;
    btn.setAttribute('role', 'option');
    btn.setAttribute('aria-selected', 'false');

    const cv = document.createElement('canvas');
    cv.width = icon.w;
    cv.height = icon.h;
    btn.append(cv);
    const cx = cv.getContext('2d');
    cells.push({ icon, ctx: cx, at: -1, buf: cx.createImageData(icon.w, icon.h) });

    btn.addEventListener('click', () => pickIcon(icon));
    grid.append(btn);
  }
}

/** Repaint only the thumbnails whose frame actually changed. */
function paintGrid(t) {
  if (state.route !== 'custom') return;      // the grid is not on screen
  for (const cell of cells) {
    const at = iconFrame(cell.icon, t);
    if (at === cell.at) continue;
    cell.at = at;
    const frame = cell.icon.frames[at];
    const px = cell.buf.data;
    px.fill(0);
    for (let i = 0; i < frame.length; i++) {
      const ch = frame[i];
      if (ch === '.') continue;
      const [r, g, b] = cell.icon.rgb[ALPHABET.indexOf(ch)];
      px.set([r, g, b, 255], i * 4);
    }
    cell.ctx.putImageData(cell.buf, 0, 0);
  }
}

function pickIcon(icon) {
  composer.icon = icon;
  const cells = [...$('c-icons').children];
  cells.forEach((c, i) => c.setAttribute(
    'aria-selected', String(state.data.icons[i] === icon)));
  composerUpdate();
}

function setupComposer() {
  buildIconGrid();
  resetComposer();

  $('c-text').addEventListener('input', composerUpdate);

  for (const seg of $('c-mode').children) {
    seg.addEventListener('click', () => {
      composer.mode = seg.dataset.mode;
      pick('c-mode', 'mode', composer.mode);
      composerUpdate();
    });
  }

  for (const seg of $('c-priority').children) {
    seg.addEventListener('click', () => {
      composer.priority = seg.dataset.priority;
      pick('c-priority', 'priority', composer.priority);
      composerUpdate();
    });
  }

  $('c-reset').addEventListener('click', resetComposer);

  $('c-value').addEventListener('input', (e) => {
    composer.pct = Number(e.target.value);
    composerUpdate();
  });

  $('c-random').addEventListener('click', surprise);
}

/* --- Routing ------------------------------------------------------------- */

/** Display name for an add-in, from the order the build script defines. */
function addinLabel(addin) {
  return state.data.groups.find((g) => g.addin === addin)?.label ?? addin;
}

/** Scenarios of a route, grouped by add-in in the order the build defines, with
    anything unlisted appended rather than dropped. */
function grouped(route) {
  const here = state.data.scenarios.filter((s) => s.route === route);
  const order = state.data.groups.map((g) => g.addin);
  const seen = [...new Set(here.map((s) => s.addin))];
  seen.sort((a, b) => {
    const [ia, ib] = [order.indexOf(a), order.indexOf(b)];
    return (ia < 0 ? order.length : ia) - (ib < 0 ? order.length : ib);
  });
  return seen.map((addin) => ({
    addin,
    label: addinLabel(addin),
    items: here.filter((s) => s.addin === addin),
  }));
}

function showRoute(route) {
  state.route = route;
  // Only the route bar. The composer's frame and priority controls are segmented
  // too, and a global .seg query was clearing their selection on every switch.
  pick('routes', 'route', route);
  const chips = $('chips');
  $('composer').hidden = route !== 'custom';
  chips.hidden = route === 'custom';

  if (route === 'custom') {
    state.scenario = CUSTOM;
    state.phase = 0;
    $('d-title').textContent = CUSTOM.title;
    $('d-source').textContent = CUSTOM.source;
    $('d-note').textContent = CUSTOM.note;
    $('d-payload-label').textContent = 'Add-in event';
    $('d-curl-label').textContent = 'Try it on your clock';
    const link = $('d-file');
    link.href = `${REPO}/blob/main/docs/writing-add-ins.md`;
    link.firstElementChild.textContent = 'docs/writing-add-ins.md';
    composerUpdate();
    return;
  }

  chips.replaceChildren();
  const groups = grouped(route);
  // One group is not a grouping: no label, no columns, just the chips.
  chips.classList.toggle('single', groups.length < 2);

  for (const group of groups) {
    const box = document.createElement('div');
    box.className = 'chip-group';

    if (groups.length > 1) {
      const label = document.createElement('span');
      label.className = 'chip-label';
      label.textContent = group.label;
      box.append(label);
    }

    const row = document.createElement('div');
    row.className = 'chip-row';
    for (const s of group.items) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'chip';
      btn.dataset.id = s.id;
      btn.textContent = s.short;
      btn.setAttribute('aria-pressed', 'false');
      btn.addEventListener('click', () => select(s.id));
      row.append(btn);
    }
    box.append(row);
    chips.append(box);
  }

  // Keep the current pick when returning to a route, and re-sync the chips
  // afterwards: select() short-circuits when the scenario has not changed, so it
  // cannot be relied on to mark the freshly built buttons.
  const inRoute = state.data.scenarios.filter((x) => x.route === route);
  const keep = inRoute.includes(state.scenario) ? state.scenario.id : inRoute[0].id;
  select(keep);
  dots();
  for (const btn of chips.children) {
    btn.setAttribute('aria-pressed', String(btn.dataset.id === keep));
  }
}

function buildTabs() {
  const bar = $('routes');
  for (const [route, meta] of Object.entries(ROUTES)) {
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'seg';
    tab.role = 'tab';
    tab.dataset.route = route;
    tab.textContent = meta.label;
    tab.setAttribute('aria-selected', 'false');
    tab.addEventListener('click', () => showRoute(route));
    bar.append(tab);
  }
}

/* --- Chrome -------------------------------------------------------------- */

function themeNow() {
  const set = document.documentElement.dataset.theme;
  if (set) return set;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function setupTheme() {
  const btn = $('theme');
  const sync = () => {
    const dark = themeNow() === 'dark';
    btn.setAttribute('aria-label', `Switch to ${dark ? 'light' : 'dark'} mode`);
    readTheme();
  };

  btn.addEventListener('click', () => {
    const next = themeNow() === 'dark' ? 'light' : 'dark';

    // A theme flip changes colour on nearly every element at once. Without this
    // every transition fires together and the switch smears instead of snapping.
    const stop = document.createElement('style');
    stop.textContent = '*,*::before,*::after{transition:none !important}';
    document.head.append(stop);

    document.documentElement.dataset.theme = next;
    localStorage.setItem('theme', next);
    sync();

    void document.body.offsetHeight;                 // force a reflow
    requestAnimationFrame(() => stop.remove());
  });

  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', sync);
  sync();
}

/** Inline every <template id="icon-*"> into the elements that ask for it. */
function setupIcons() {
  for (const el of document.querySelectorAll('[data-icon]')) {
    const tpl = $(`icon-${el.dataset.icon}`);
    if (tpl) el.prepend(tpl.content.cloneNode(true));
  }
}

/** Copy, without assuming the Clipboard API is there.

    navigator.clipboard only exists in a secure context, so over plain http on a
    LAN address it is missing entirely and the promise rejection went nowhere:
    the button threw and never changed. The textarea fallback still works there,
    and if even that fails the label says so instead of lying. */
async function copy(btn) {
  const text = $(btn.dataset.copy).textContent;
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch {
    const pad = document.createElement('textarea');
    pad.value = text;
    pad.setAttribute('readonly', '');
    pad.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.append(pad);
    pad.select();
    try {
      ok = document.execCommand('copy');
    } catch {
      ok = false;
    }
    pad.remove();
  }

  const was = btn.dataset.label ?? btn.textContent;
  btn.dataset.label = was;
  btn.textContent = ok ? 'Copied' : 'Press Ctrl+C';
  setTimeout(() => { btn.textContent = was; }, 1400);
}

async function main() {
  setupIcons();
  setupTheme();

  state.data = await (await fetch('preview.json')).json();
  prepareIcons(state.data.icons);
  buildTabs();
  setupComposer();

  const toggle = (el, key, after) => el.addEventListener('click', () => {
    state[key] = !state[key];
    el.setAttribute('aria-pressed', String(state[key]));
    after?.();
  });

  toggle($('quiet'), 'quiet', () => {
    $('critical').disabled = !state.quiet;
    readout();
  });
  toggle($('critical'), 'allowCritical', readout);
  toggle($('play'), 'cycling');
  $('play').setAttribute('aria-pressed', String(state.cycling));

  // Stepping implies you want to look at something, so it pauses the player
  // rather than fighting it.
  const manual = (dir) => {
    if (state.cycling) $('play').click();
    step(dir);
  };
  $('prev').addEventListener('click', () => manual(-1));
  $('next').addEventListener('click', () => manual(1));
  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, textarea')) return;
    if (e.key === 'ArrowLeft') manual(-1);
    if (e.key === 'ArrowRight') manual(1);
  });

  for (const btn of document.querySelectorAll('.copy')) {
    btn.addEventListener('click', () => copy(btn));
  }

  $('build').textContent = `v${state.data.version} · built ${state.data.generated.slice(0, 10)}`;

  showRoute('notification');
  requestAnimationFrame(render);
}

main();
