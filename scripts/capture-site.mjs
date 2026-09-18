// Capture the README artwork from the running preview site.
//
// Everything in docs/assets is a recording of the real page, not a mock-up, so
// it cannot drift from what the site actually renders. Needs a checkout with
// Playwright and ImageMagick available, and the built site being served:
//
//   scripts/build-preview.py
//   python3 -m http.server 8088 --directory _site &
//   node scripts/capture-site.mjs
//
// The hero is recorded against a transparent page and cropped to the bezel, so
// one recording sits correctly on a light and a dark README. Its drop shadow is
// switched off first: GIF transparency is on or off with nothing in between, and
// a soft shadow through that becomes a hard smear.

import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import { chromium } from 'playwright';

const run = promisify(execFile);

const SITE = process.env.SITE ?? 'http://127.0.0.1:8088/';

// The viewport is chosen so the bezel comes out at exactly WIDE css pixels:
// .wrap is the viewport minus its 24px gutters. Captured at twice that and
// halved, every edge pixel is the average of four whole pixels, so the straight
// sides stay fully opaque. A fractional crop or an awkward scale leaves them
// hovering around half alpha, and GIF transparency turns that into a ragged
// column because it can only answer yes or no.
const WIDE = 820;
const VIEWPORT = WIDE + 48;
const OUT = new URL('../docs/assets/', import.meta.url).pathname;
const WORK = '/tmp/hero-frames';

// One scenario per add-in, chosen for variety: titles that scroll, quota frames
// that fit, a progress bar, and icons in four colours. The route has to come
// along, because a chip only exists while its tab is open.
const HERO = [
  ['Notification', 'Episode starts'],   // Plex
  ['Notification', 'Build fails'],      // Forgejo
  ['Notification', 'Monitor down'],     // Uptime Kuma
  ['Notification', 'Game starts'],      // PlayStation
  ['DIY widget', 'Codex quota'],        // two frames and a bar
  ['Notification', 'Claude at 10%'],    // bar, critical
];

const EVERY = 160;     // ms between captures
const SETTLE = 420;    // let a frame come to rest before the first capture
const LEAD = 70;       // centiseconds held on that resting frame
const TAIL = 55;       // and on the last one, before the cut
const CAP = 70;        // frames per scenario, in case a cycle never closes

const browser = await chromium.launch();

async function open(theme, viewport) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  await page.addInitScript((t) => localStorage.setItem('theme', t), theme);
  await page.goto(SITE, { waitUntil: 'networkidle' });
  await page.waitForTimeout(900);
  return { ctx, page };
}

/* --- The hero, recorded off the real display ------------------------------ */

{
  const dir = `${WORK}/hero`;
  await rm(dir, { recursive: true, force: true });
  await mkdir(dir, { recursive: true });

  // The device is dark whatever the page is, so the hero only needs recording
  // once. Clear the page behind it and let the screenshot keep the alpha.
  const { ctx, page } = await open('dark', { width: VIEWPORT, height: 900 });
  await page.evaluate(() => {
    document.documentElement.style.background = 'transparent';
    document.body.style.background = 'transparent';
    document.querySelector('.device').style.boxShadow = 'none';
  });

  // Exactly the bezel: with the shadow gone there is nothing outside it worth
  // keeping, and its own rounded corners become the edge of the image. Whole
  // pixels only, so the crop cannot slice one down the middle.
  const box = await page.locator('.device').boundingBox();
  const clip = { x: Math.round(box.x), y: Math.round(box.y),
                 width: Math.round(box.width), height: Math.round(box.height) };
  if (clip.width !== WIDE) {
    throw new Error(`bezel is ${clip.width}px, expected ${WIDE}: adjust VIEWPORT`);
  }

  // Record each scenario for exactly one cycle. A fixed frame count cut the
  // long titles off mid-scroll, and the cycle length is not something the page
  // exposes -- but it is visible: capture until the display comes back to the
  // frame it started on. The run of identical frames during the opening pause
  // has to be skipped first, or it would match immediately.
  const shots = [];
  let open_route = null;
  let n = 0;

  for (const [route, chip] of HERO) {
    if (route !== open_route) {
      await page.getByRole('tab', { name: route, exact: true }).click();
      await page.waitForTimeout(250);
      open_route = route;
    }
    await page.getByRole('button', { name: chip, exact: true }).click();
    await page.waitForTimeout(SETTLE);

    const shot = async () => {
      const buf = await page.screenshot({ clip, omitBackground: true });
      const file = `${dir}/${String(n++).padStart(4, '0')}.png`;
      await writeFile(file, buf);
      return { file, hash: createHash('sha1').update(buf).digest('hex') };
    };

    const first = await shot();
    const cycle = [first];
    let moved = false;
    for (let i = 1; i < CAP; i++) {
      await page.waitForTimeout(EVERY);
      const next = await shot();
      if (next.hash !== first.hash) moved = true;
      else if (moved) break;            // back where it started: one full cycle
      cycle.push(next);
    }
    // A frame that fits and carries no animated icon never changes. One image
    // and a long hold says the same thing as seventy identical ones.
    shots.push(moved ? cycle : cycle.slice(0, 1));
    console.log(`  ${chip}: ${shots.at(-1).length} frame(s)`);
  }
  await ctx.close();

  // Hold the first and last frame of every scenario. Without that beat the
  // whole thing reads as one continuous scroll with no punctuation.
  const args = ['-loop', '0'];
  let frames = 0;
  for (const cycle of shots) {
    cycle.forEach((s, i) => {
      const delay = cycle.length === 1 ? LEAD + TAIL
        : i === 0 ? LEAD
        : i === cycle.length - 1 ? TAIL
        : EVERY / 10;
      args.push('-delay', String(delay), s.file);
      frames += 1;
    });
  }

  args.push('-resize', `${WIDE}x`, '-background', 'none', '-dispose', 'background',
            '-layers', 'OptimizeTransparency', '-colors', '128', `${OUT}hero.gif`);
  await run('convert', args);
  console.log(`docs/assets/hero.gif  (${frames} frames)`);
}

await browser.close();
