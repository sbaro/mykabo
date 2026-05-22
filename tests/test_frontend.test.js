/**
 * Unit tests for MyKaBo frontend utility functions.
 *
 * Run from tests/:
 *   npm test
 *
 * The functions are pure (no DOM / API dependencies) and are copied
 * verbatim from index.html so they can be tested in Node without a browser.
 */

// ─── Pure functions (copied verbatim from index.html) ─────────────────────

function esc(s) {
  return (s == null ? "" : String(s))
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function darken(hex) {
  let c = hex.replace("#", "");
  if (c.length === 3) c = c.split("").map(x => x + x).join("");
  if (c.length !== 6) return "#aaa";
  const n = parseInt(c, 16);
  return `rgb(${Math.max(0, ((n >> 16) & 0xff) - 40)},${Math.max(0, ((n >> 8) & 0xff) - 40)},${Math.max(0, (n & 0xff) - 40)})`;
}

function blockReasonShort(raw) {
  if (!raw) return "";
  const m = raw.match(/^🚧 \[BLOQUÉ le [^\]]*\] — (?:Cause : )?(.+)$/s);
  return m ? m[1] : raw.replace(/^🚧\s*/, "");
}

function getStackRepresentatives(colTasks) {
  const seen = new Set(), result = [];
  for (const t of colTasks) {
    if (!t.stack_id) { result.push(t); continue; }
    if (t.stack_pos === 0 && !seen.has(t.stack_id)) { seen.add(t.stack_id); result.push(t); }
  }
  return result;
}

// ─── esc() ────────────────────────────────────────────────────────────────

describe("esc()", () => {
  test("leaves plain text unchanged", () => {
    expect(esc("hello world")).toBe("hello world");
  });

  test("escapes ampersand", () => {
    expect(esc("a & b")).toBe("a &amp; b");
  });

  test("escapes < and >", () => {
    expect(esc("<script>alert(1)</script>")).toBe("&lt;script&gt;alert(1)&lt;/script&gt;");
  });

  test("escapes double quotes", () => {
    expect(esc('say "hi"')).toBe("say &quot;hi&quot;");
  });

  test("returns empty string for null", () => {
    expect(esc(null)).toBe("");
  });

  test("returns empty string for undefined", () => {
    expect(esc(undefined)).toBe("");
  });

  test("coerces numbers to string", () => {
    expect(esc(42)).toBe("42");
  });

  test("escapes all special chars together", () => {
    expect(esc('<a href="x&y">Z</a>')).toBe(
      '&lt;a href=&quot;x&amp;y&quot;&gt;Z&lt;/a&gt;'
    );
  });
});

// ─── darken() ─────────────────────────────────────────────────────────────

describe("darken()", () => {
  test("returns rgb() for a valid 6-digit hex", () => {
    expect(darken("#ffffff")).toMatch(/^rgb\(\d+,\d+,\d+\)$/);
  });

  test("expands 3-digit shorthand to same result as 6-digit", () => {
    expect(darken("#fff")).toBe(darken("#ffffff"));
    expect(darken("#abc")).toBe(darken("#aabbcc"));
  });

  test("each channel is reduced by 40", () => {
    // #646464 = rgb(100,100,100) → rgb(60,60,60)
    expect(darken("#646464")).toBe("rgb(60,60,60)");
  });

  test("channels are clamped at 0 (black stays black)", () => {
    expect(darken("#000000")).toBe("rgb(0,0,0)");
  });

  test("does not produce negative channel values for dark colours", () => {
    const result = darken("#141414"); // rgb(20,20,20) → rgb(0,0,0)
    const parts = result.match(/\d+/g).map(Number);
    parts.forEach(v => expect(v).toBeGreaterThanOrEqual(0));
  });

  test("returns fallback #aaa for strings with wrong length after stripping #", () => {
    expect(darken("notahex")).toBe("#aaa");   // 7 chars
    expect(darken("#12345")).toBe("#aaa");    // 5 chars
  });

  test("strips # before parsing", () => {
    expect(darken("ffffff")).toBe(darken("#ffffff"));
  });
});

// ─── blockReasonShort() ───────────────────────────────────────────────────

describe("blockReasonShort()", () => {
  test("extracts reason after 'Cause :'", () => {
    expect(
      blockReasonShort("🚧 [BLOQUÉ le 22/05/2026 12:35:06] — Cause : Mon problème")
    ).toBe("Mon problème");
  });

  test("extracts message for auto-comment without 'Cause :'", () => {
    expect(
      blockReasonShort("🚧 [BLOQUÉ le 22/05/2026 12:35:06] — Pile déplacée vers Blocked")
    ).toBe("Pile déplacée vers Blocked");
  });

  test("returns already-extracted reason unchanged (no 🚧 prefix)", () => {
    expect(blockReasonShort("Mon problème")).toBe("Mon problème");
  });

  test("strips leading 🚧 emoji from raw fallback strings", () => {
    expect(blockReasonShort("🚧 raw reason")).toBe("raw reason");
  });

  test("handles multiline reason (s flag on regex)", () => {
    expect(
      blockReasonShort("🚧 [BLOQUÉ le 22/05/2026 12:35:06] — Cause : Ligne 1\nLigne 2")
    ).toBe("Ligne 1\nLigne 2");
  });

  test("returns empty string for null", () => {
    expect(blockReasonShort(null)).toBe("");
  });

  test("returns empty string for undefined", () => {
    expect(blockReasonShort(undefined)).toBe("");
  });

  test("returns empty string for empty string", () => {
    expect(blockReasonShort("")).toBe("");
  });

  test("works with different date formats in the bracket", () => {
    expect(
      blockReasonShort("🚧 [BLOQUÉ le 01/01/2025 09:00:00] — Cause : Old reason")
    ).toBe("Old reason");
  });
});

// ─── getStackRepresentatives() ────────────────────────────────────────────

describe("getStackRepresentatives()", () => {
  test("returns empty array for empty input", () => {
    expect(getStackRepresentatives([])).toEqual([]);
  });

  test("passes through tasks without a stack_id", () => {
    const tasks = [{ id: 1, stack_id: null }, { id: 2, stack_id: null }];
    expect(getStackRepresentatives(tasks)).toEqual(tasks);
  });

  test("returns only the representative (stack_pos=0) for a two-member stack", () => {
    const tasks = [
      { id: 1, stack_id: "abc", stack_pos: 0 },
      { id: 2, stack_id: "abc", stack_pos: 1 },
    ];
    const result = getStackRepresentatives(tasks);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(1);
  });

  test("handles a mix of plain and stacked tasks", () => {
    const tasks = [
      { id: 1, stack_id: null },
      { id: 2, stack_id: "abc", stack_pos: 0 },
      { id: 3, stack_id: "abc", stack_pos: 1 },
      { id: 4, stack_id: null },
    ];
    const result = getStackRepresentatives(tasks);
    expect(result.map(t => t.id)).toEqual([1, 2, 4]);
  });

  test("returns one representative per distinct stack", () => {
    const tasks = [
      { id: 1, stack_id: "s1", stack_pos: 0 },
      { id: 2, stack_id: "s1", stack_pos: 1 },
      { id: 3, stack_id: "s2", stack_pos: 0 },
      { id: 4, stack_id: "s2", stack_pos: 1 },
    ];
    const result = getStackRepresentatives(tasks);
    expect(result).toHaveLength(2);
    expect(result.map(t => t.id)).toEqual([1, 3]);
  });

  test("skips non-representative stacked task even when it appears first in the array", () => {
    // stack_pos=1 comes before stack_pos=0 in the input order
    const tasks = [
      { id: 2, stack_id: "abc", stack_pos: 1 },
      { id: 1, stack_id: "abc", stack_pos: 0 },
    ];
    const result = getStackRepresentatives(tasks);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(1);
  });

  test("preserves input order for plain tasks", () => {
    const tasks = [
      { id: 3, stack_id: null },
      { id: 1, stack_id: null },
      { id: 2, stack_id: null },
    ];
    expect(getStackRepresentatives(tasks).map(t => t.id)).toEqual([3, 1, 2]);
  });
});
