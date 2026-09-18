// The 3x5 caps font of a LaMetric Time, for the 28x8 text zone next to an icon.
//
// Traced off photographs of a real LM 37X8: the glyphs marked below were read
// LED by LED from the panel, the rest are drawn in the same style because no
// frame captured them. LaMetric ships no font file, so this stays a
// reproduction, but the metrics it is measured on are exact.
//
// What the photographs settle:
//   - the device has no lowercase. "One Piece" comes off the wire mixed case
//     and renders ONE PIECE, so glyph() folds case rather than inventing it.
//   - caps are 3 wide, 5 rows tall, with one dark column between them. N is 4
//     wide, I is 1.
//   - a space is 2 wide.
//   - text sits on display rows 1-5, clear of the progress bar on row 7.
//
// Each glyph is an array of rows, '#' lit and '.' dark, drawn from glyph row 0.
// The renderer draws glyph row 0 at display row 1, so a comma still has a row
// below the baseline to hang in.

export const GLYPH_ROWS = 6;
export const LETTER_SPACING = 1;

// Glyphs read off the panel are marked; the others match their style.
const RAW = {
  ' ': ['..'],                                            // measured

  A: ['##.', '#.#', '###', '#.#', '#.#'],                 // measured
  B: ['##.', '#.#', '##.', '#.#', '##.'],
  C: ['.##', '#..', '#..', '#..', '.##'],                 // measured
  D: ['##.', '#.#', '#.#', '#.#', '##.'],                 // measured
  E: ['###', '#..', '###', '#..', '###'],                 // measured
  F: ['###', '#..', '###', '#..', '#..'],
  G: ['.##', '#..', '#.#', '#.#', '.##'],                 // measured
  H: ['#.#', '#.#', '###', '#.#', '#.#'],                 // measured
  I: ['#', '#', '#', '#', '#'],                           // measured, 1 wide
  J: ['..#', '..#', '..#', '#.#', '.#.'],
  K: ['#.#', '#.#', '##.', '#.#', '#.#'],
  L: ['#..', '#..', '#..', '#..', '###'],
  M: ['#...#', '##.##', '#.#.#', '#...#', '#...#'],
  N: ['#..#', '##.#', '#.##', '#..#', '#..#'],            // measured, 4 wide
  O: ['.#.', '#.#', '#.#', '#.#', '.#.'],                 // measured
  P: ['###', '#.#', '##.', '#..', '#..'],                 // measured
  Q: ['.#.', '#.#', '#.#', '#.#', '.##'],
  R: ['###', '#.#', '##.', '#.#', '#.#'],                 // measured
  S: ['.##', '#..', '.#.', '..#', '##.'],
  T: ['###', '.#.', '.#.', '.#.', '.#.'],                 // measured
  U: ['#.#', '#.#', '#.#', '#.#', '.#.'],
  V: ['#.#', '#.#', '#.#', '.#.', '.#.'],
  W: ['#...#', '#...#', '#.#.#', '#.#.#', '.#.#.'],
  X: ['#.#', '#.#', '.#.', '#.#', '#.#'],
  Y: ['#.#', '#.#', '.#.', '.#.', '.#.'],
  Z: ['###', '..#', '.#.', '#..', '###'],

  0: ['###', '#.#', '#.#', '#.#', '###'],
  1: ['.#.', '##.', '.#.', '.#.', '###'],   // measured
  2: ['###', '..#', '###', '#..', '###'],
  3: ['###', '..#', '###', '..#', '###'],   // measured
  4: ['#.#', '#.#', '###', '..#', '..#'],   // measured
  5: ['###', '#..', '###', '..#', '###'],   // measured
  6: ['###', '#..', '###', '#.#', '###'],   // measured
  7: ['###', '..#', '..#', '..#', '..#'],   // measured
  8: ['###', '#.#', '###', '#.#', '###'],
  9: ['###', '#.#', '###', '..#', '###'],   // measured

  '.': ['.', '.', '.', '.', '#'],
  ',': ['.', '.', '.', '.', '#', '#'],
  ':': ['.', '#', '.', '#', '.'],
  ';': ['.', '#', '.', '#', '.', '#'],
  '!': ['#', '#', '#', '.', '#'],
  '?': ['##.', '..#', '.#.', '...', '.#.'],
  '%': ['#.#', '..#', '.#.', '#..', '#.#'],             // measured
  '+': ['...', '.#.', '###', '.#.', '...'],
  '-': ['...', '...', '###', '...', '...'],
  '/': ['..#', '..#', '.#.', '#..', '#..'],
  '\\': ['#..', '#..', '.#.', '..#', '..#'],
  '(': ['.#', '#.', '#.', '#.', '.#'],
  ')': ['#.', '.#', '.#', '.#', '#.'],
  '[': ['##', '#.', '#.', '#.', '##'],
  ']': ['##', '.#', '.#', '.#', '##'],
  '·': ['.', '.', '#', '.', '.'],
  '•': ['.', '.', '#', '.', '.'],
  "'": ['#', '#'],
  '"': ['#.#', '#.#'],
  '*': ['#.#', '.#.', '#.#', '...', '...'],
  '=': ['...', '###', '...', '###', '...'],
  '_': ['...', '...', '...', '...', '###'],
  '<': ['..#', '.#.', '#..', '.#.', '..#'],
  '>': ['#..', '.#.', '..#', '.#.', '#..'],
  '#': ['.#.#.', '#####', '.#.#.', '#####', '.#.#.'],
  '&': ['.#..', '#.#.', '.#..', '#.#.', '.#.#'],
  '@': ['.###.', '#...#', '#.###', '#....', '.###.'],
  '°': ['.#.', '#.#', '.#.', '...', '...'],
  '|': ['#', '#', '#', '#', '#'],
};

// Fold accented characters and typographic punctuation onto a base glyph, so a
// name with an umlaut stays readable instead of turning into a row of boxes.
// Everything else is folded by case: the device has no lowercase.
const FOLD = {
  ä: 'A', ö: 'O', ü: 'U', Ä: 'A', Ö: 'O', Ü: 'U', ß: 'B',
  á: 'A', à: 'A', â: 'A', é: 'E', è: 'E', ê: 'E', í: 'I', ì: 'I',
  ó: 'O', ò: 'O', ô: 'O', ú: 'U', ù: 'U', ñ: 'N', ç: 'C', å: 'A', ø: 'O',
  '–': '-', '—': '-', '’': "'", '‘': "'", '“': '"', '”': '"', '…': '.',
};

// Anything with no glyph becomes a hollow box, the way a display without the
// character would show it. Silently dropping it would hide the problem.
const UNKNOWN = ['###', '#.#', '#.#', '#.#', '###'];

/** Return {width, rows} for one character, where rows are '#'/'.' strings. */
export function glyph(ch) {
  const rows = RAW[FOLD[ch] ?? ch.toUpperCase()] || UNKNOWN;
  return { width: rows[0].length, rows };
}

/** Total pixel width of a string, including letter spacing between glyphs. */
export function measure(text) {
  let w = 0;
  for (const ch of text) w += glyph(ch).width + LETTER_SPACING;
  return Math.max(0, w - LETTER_SPACING);
}

/**
 * Rasterise text into a boolean grid of GLYPH_ROWS rows.
 *
 * Returning a grid rather than drawing directly keeps the renderer free to
 * scroll, clip or measure the result without re-rasterising on every frame.
 */
export function raster(text) {
  const width = measure(text);
  const grid = Array.from({ length: GLYPH_ROWS }, () => new Uint8Array(width));
  let x = 0;
  for (const ch of text) {
    const { width: w, rows } = glyph(ch);
    rows.forEach((row, y) => {
      for (let i = 0; i < w; i++) {
        if (row[i] === '#') grid[y][x + i] = 1;
      }
    });
    x += w + LETTER_SPACING;
  }
  return { width, grid };
}
