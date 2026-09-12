import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/modBatch.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { batchEnabled, moveBatch } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const row = (id, enabled = true) => ({ id, enabled });
const ids = (mods) => mods.map((mod) => mod.id).join("");
const mods = [..."abcdef"].map((id) => row(id)).concat(row("x", false));
assert.equal(ids(moveBatch(mods, ["b", "d"], "up")), "bdacefx");
assert.equal(ids(moveBatch(mods, ["b", "d"], "down")), "acebdfx");
assert.equal(ids(moveBatch(mods, ["b", "d"], "top")), "bdacefx");
assert.equal(ids(moveBatch(mods, ["b", "d"], "bottom")), "acefbdx");
assert.equal(ids(moveBatch(mods, ["b", "d"], "up", 100)), "bdacefx");
assert.equal(ids(moveBatch(mods, ["b", "d"], "down", 100)), "acefbdx");
assert.equal(ids(moveBatch(mods, ["b", "d"], "top", 1, "f")), "acebdfx");
assert.equal(moveBatch(mods, ["b", "d"], "top", 1, "d"), mods);
assert.equal(moveBatch(mods, ["x"], "up"), mods);
assert.equal(moveBatch(mods, ["a"], "up"), mods);
assert.equal(moveBatch(mods, ["f"], "down"), mods);
assert.equal(moveBatch(mods, ["b"], "up", 0), mods);
assert.equal(batchEnabled(mods, ["a"], "enable"), mods);
assert.equal(ids(batchEnabled(mods, ["b", "d"], "disable")), "acefbdx");
assert.equal(ids(batchEnabled(mods, ["b", "d", "x"], "invert")), "acefxbd");
assert.equal(batchEnabled(mods, ["missing"], "disable"), mods);
// Exhaustive subsets: stable order of both selected and unselected Mods,
// no duplicate/lost entries, and disabled Mods untouched by priority moves.
for (let mask = 0; mask < 64; mask++) {
  const selected = [..."abcdef"].filter((_, index) => mask & (1 << index));
  for (const direction of ["up", "down", "top", "bottom"]) {
    for (const steps of [1, 10, 50, 100]) {
      const result = moveBatch(mods, selected, direction, steps);
      assert.equal(new Set(result.map((mod) => mod.id)).size, mods.length);
      assert.equal(result.at(-1), mods.at(-1));
      assert.deepEqual(result.filter((mod) => selected.includes(mod.id)).map((mod) => mod.id), selected);
      assert.deepEqual(result.filter((mod) => !selected.includes(mod.id)).map((mod) => mod.id),
        mods.filter((mod) => !selected.includes(mod.id)).map((mod) => mod.id));
    }
  }
}
console.log("Batch rules passed: legacy block semantics, boundaries, 1/10/50/100 steps, drag target, enable/disable/invert, and 1024 subset movement cases.");
