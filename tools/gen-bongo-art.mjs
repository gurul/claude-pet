#!/usr/bin/env node
// tools/gen-bongo-art.mjs — generates firmware/claude_pet/src/buddies/bongo_art.h
// from the petlab bongo-cat candidates. The generated header is the ONLY
// artifact — never hand-edit it; re-run this script instead.
//
// Re-run:
//   node tools/gen-bongo-art.mjs
// or with a different petlab checkout:
//   PETLAB_DIR=/path/to/era-maker/tools/petlab node tools/gen-bongo-art.mjs
//
// Source of truth: the 7 mood candidates char-bongo-{sleep,idle,busy,
// attention,celebrate,dizzy,heart}.mjs. Each default-exports { frameMs,
// frames: Op[][] } where an Op is one of (see petlab kit.mjs):
//   { t:'px',   x, y, on }
//   { t:'rect', x, y, w, h, on }
//   { t:'line', x0, y0, x1, y1, on }
// on:true draws in the body colour, on:false CARVES (background colour).
// Op order within a frame is load-bearing (carves paint over earlier ink)
// and is preserved verbatim.
//
// Emitted per mood: a flat op array + a frame-offset index + nFrames +
// ticksPerFrame (authored frameMs quantized to the firmware's 200ms tick).
// Also emitted: the global 1-bit art bounding box across all moods/frames,
// so the species can clear exactly its own footprint.

import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { writeFileSync } from 'node:fs';

const __dirname = dirname(fileURLToPath(import.meta.url));

const PETLAB_DIR = process.env.PETLAB_DIR
  ?? join(__dirname, '../../era-maker/tools/petlab');
const OUT = join(__dirname, '../firmware/claude_pet/src/buddies/bongo_art.h');

// PersonaState order — must match the enum in src/buddy.cpp.
const MOODS = ['sleep', 'idle', 'busy', 'attention', 'celebrate', 'dizzy', 'heart'];
const TICK_MS = 200; // firmware buddy tick (buddy.cpp TICK_MS)

const KIND = { px: 0, rect: 1, line: 2 };
const ON_BIT = 0x80;

function u8(v, what) {
  if (!Number.isInteger(v) || v < 0 || v > 255) {
    throw new Error(`${what} out of uint8 range: ${v}`);
  }
  return v;
}

// Encode one op as 5 bytes: kind|on, a, b, c, d.
//   px:   a=x, b=y, c=d=0
//   rect: a=x, b=y, c=w, d=h
//   line: a=x0, b=y0, c=x1, d=y1
function encode(op) {
  const kind = KIND[op.t];
  if (kind === undefined) {
    throw new Error(`unsupported op type '${op.t}' — only px/rect/line render 1-bit`);
  }
  const k = kind | (op.on ? ON_BIT : 0);
  if (op.t === 'px')   return [k, u8(op.x, 'px.x'), u8(op.y, 'px.y'), 0, 0];
  if (op.t === 'rect') return [k, u8(op.x, 'rect.x'), u8(op.y, 'rect.y'), u8(op.w, 'rect.w'), u8(op.h, 'rect.h')];
  return [k, u8(op.x0, 'line.x0'), u8(op.y0, 'line.y0'), u8(op.x1, 'line.x1'), u8(op.y1, 'line.y1')];
}

// Track the ink bounding box (on:true only) across every frame of every mood.
const bbox = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
function grow(x, y) {
  if (x < bbox.minX) bbox.minX = x;
  if (y < bbox.minY) bbox.minY = y;
  if (x > bbox.maxX) bbox.maxX = x;
  if (y > bbox.maxY) bbox.maxY = y;
}
function trackBbox(op) {
  if (!op.on) return; // carves never extend ink
  if (op.t === 'px') grow(op.x, op.y);
  else if (op.t === 'rect') { grow(op.x, op.y); grow(op.x + op.w - 1, op.y + op.h - 1); }
  else { grow(op.x0, op.y0); grow(op.x1, op.y1); }
}

// The firmware clears exactly the INK bbox (+2px margin) before replaying a
// frame, so a carve outside it would paint background beyond the owned box
// with no visual signal until it eats a neighbouring screen region. Assert
// the invariant instead of trusting future candidates to keep it.
function assertCarveInside(op, mood) {
  if (op.on) return;
  const pts = op.t === 'px' ? [[op.x, op.y]]
    : op.t === 'rect' ? [[op.x, op.y], [op.x + op.w - 1, op.y + op.h - 1]]
    : [[op.x0, op.y0], [op.x1, op.y1]];
  for (const [x, y] of pts) {
    if (x < bbox.minX || x > bbox.maxX || y < bbox.minY || y > bbox.maxY) {
      throw new Error(
        `char-bongo-${mood}: carve op ${JSON.stringify(op)} extends outside the ` +
        `ink bbox x${bbox.minX}..${bbox.maxX} y${bbox.minY}..${bbox.maxY} — ` +
        `the firmware's self-clear won't cover it`);
    }
  }
}

const sections = [];
const moodMeta = [];
const allOps = [];   // [mood, op] pairs — carve containment is asserted after the bbox is final

for (const mood of MOODS) {
  const path = join(PETLAB_DIR, 'candidates', `char-bongo-${mood}.mjs`);
  let mod;
  try {
    mod = (await import(pathToFileURL(path).href)).default;
  } catch (e) {
    throw new Error(`failed to import ${path}: ${e.message}`);
  }
  const { frames, frameMs } = mod;
  if (!Array.isArray(frames) || frames.length === 0) {
    throw new Error(`${mood}: no frames`);
  }

  const idx = [0];
  const bytes = [];
  const comments = [];
  for (const [f, ops] of frames.entries()) {
    for (const op of ops) {
      trackBbox(op);
      allOps.push([mood, op]);
      bytes.push(encode(op));
    }
    idx.push(bytes.length);
    comments.push(`frame ${f}: ops ${idx[f]}..${idx[f + 1] - 1}`);
  }

  const ticksPerFrame = Math.max(1, Math.round(frameMs / TICK_MS));
  const M = mood.toUpperCase();
  sections.push(
    `// ${mood}: ${frames.length} frames @ ${frameMs}ms (→ ${ticksPerFrame} tick(s)/frame)\n` +
    `// ${comments.join(', ')}\n` +
    `static const BongoOp BONGO_${M}_OPS[] = {\n` +
    bytes.map(b => `  { 0x${b[0].toString(16).padStart(2, '0')}, ${b[1]}, ${b[2]}, ${b[3]}, ${b[4]} },`).join('\n') +
    `\n};\n` +
    `static const uint16_t BONGO_${M}_IDX[] = { ${idx.join(', ')} };\n`
  );
  moodMeta.push({ M, nFrames: frames.length, ticksPerFrame });
}

// Second pass: bbox is final only now, so carve containment is checked here.
for (const [mood, op] of allOps) assertCarveInside(op, mood);

const header = `#pragma once
#include <stdint.h>

// GENERATED FILE — do not edit by hand.
// Produced by tools/gen-bongo-art.mjs from the petlab bongo-cat candidates
// (era-maker/tools/petlab/candidates/char-bongo-*.mjs). To regenerate:
//   node tools/gen-bongo-art.mjs
// (set PETLAB_DIR to point at a different petlab checkout if needed)
//
// Art space: 128x64 1-bit panel coordinates as authored in petlab.
// Op encoding: kind low 7 bits (0=px 1=rect 2=line), bit7 = on (ink vs carve).
//   px:   a=x, b=y            rect: a=x, b=y, c=w, d=h
//   line: a=x0, b=y0, c=x1, d=y1
// Op order within a frame is load-bearing — carves overwrite earlier ink.

struct BongoOp { uint8_t kind; uint8_t a, b, c, d; };
struct BongoMood {
  const BongoOp* ops;
  const uint16_t* idx;   // nFrames+1 offsets into ops
  uint8_t nFrames;
  uint8_t ticksPerFrame; // authored frameMs quantized to 200ms buddy ticks
};

// Ink bounding box across all moods/frames (art space, inclusive).
static const uint8_t BONGO_ART_MIN_X = ${bbox.minX};
static const uint8_t BONGO_ART_MIN_Y = ${bbox.minY};
static const uint8_t BONGO_ART_MAX_X = ${bbox.maxX};
static const uint8_t BONGO_ART_MAX_Y = ${bbox.maxY};

${sections.join('\n')}
// PersonaState order: 0=sleep 1=idle 2=busy 3=attention 4=celebrate 5=dizzy 6=heart
static const BongoMood BONGO_MOODS[7] = {
${moodMeta.map(m => `  { BONGO_${m.M}_OPS, BONGO_${m.M}_IDX, ${m.nFrames}, ${m.ticksPerFrame} },`).join('\n')}
};
`;

writeFileSync(OUT, header);
const nOps = sections.join('').split('\n').filter(l => l.startsWith('  { 0x')).length;
console.log(`wrote ${OUT}`);
console.log(`moods: ${MOODS.length}, total ops: ${nOps}, bbox: x${bbox.minX}..${bbox.maxX} y${bbox.minY}..${bbox.maxY}`);
