import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const output = fileURLToPath(new URL("../build/mod-categories-smoke/", import.meta.url));
const server = await createServer({ root, server: { host: "127.0.0.1", port: 0 } });
await server.listen();
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  page.setDefaultTimeout(20000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    let sequence = 0;
    const callbacks = new Map();
    const listeners = new Map();
    const initial = { folders: ["地图组"], assignments: { b: "地图组", d: "地图组" }, activeMods: ["e", "d", "c", "b", "a"] };
    const state = { ...JSON.parse(localStorage.getItem("categoryTestData") || JSON.stringify(initial)), writes: [], fail: false, readOnly: false };
    window.categoryTest = state;
    const persist = () => localStorage.setItem("categoryTestData", JSON.stringify({
      folders: state.folders, assignments: state.assignments, activeMods: state.activeMods,
    }));
    const emit = (event, payload) => {
      for (const [id, args] of listeners) if (args.event === event) callbacks.get(args.handler)?.({ event, payload, id });
    };
    const snapshot = () => ({ folders: [...state.folders], assignments: { ...state.assignments } });
    window.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main", windowLabel: "main" } },
      transformCallback: (callback) => { const id = ++sequence; callbacks.set(id, callback); return id; },
      unregisterCallback: (id) => callbacks.delete(id),
      invoke: async (command, args = {}) => {
        if (command === "plugin:event|listen") { const id = ++sequence; listeners.set(id, args); return id; }
        if (command === "plugin:event|unlisten") { listeners.delete(args.eventId); return; }
        if (command === "plugin:event|emit_to") {
          if (args.event === "ets2-main-ready") setTimeout(() => emit("ets2-initialization-complete"), 0);
          return;
        }
        if (command === "plugin:window|get_all_windows") return ["main", "initializer"];
        if (command === "plugin:window|show" || command === "plugin:window|close") return;
        if (command === "profile_list") return [
          { id: "local", name: "本地 Profile", company: "", location: "local", writable: true },
          { id: "readonly", name: "只读测试", company: "", location: "local", writable: false },
        ];
        if (command === "mod_list") {
          const enabled = [...state.activeMods].reverse();
          return [...enabled, ...[..."abcdef"].filter((id) => !enabled.includes(id))].map((id) => ({
            id, packageName: id, path: `C:\\test\\${id}.scs`, packageType: id === "c" ? "workshop" : "scs",
            displayName: `${id.toUpperCase()} Mod`, enabled: enabled.includes(id),
          }));
        }
        if (["mod_media_batch", "preset_list", "save_list_local"].includes(command)) return [];
        if (command === "category_list") return snapshot();
        if (command === "category_mutate") {
          if (state.fail) throw "category_missing";
          const { operation, name, newName, modIds } = args.request;
          if (operation === "create") {
            if (state.folders.includes(name)) throw "category_exists";
            state.folders.push(name);
          } else if (operation === "assign") {
            for (const id of modIds) state.assignments[id] = name;
          } else {
            state.folders = state.folders.flatMap((f) => f === name ? operation === "delete" ? [] : [newName] : [f]);
            for (const id of Object.keys(state.assignments)) if (state.assignments[id] === name) state.assignments[id] = operation === "delete" ? "" : newName;
          }
          persist();
          return snapshot();
        }
        if (command === "profile_write_active") {
          state.writes.push(args.request);
          state.activeMods = args.request.activeMods;
          persist();
          return { success: true };
        }
        if (command === "mod_scan") return { total: 6, added: 0, updated: 0, removed: 0, inspected: 0, elapsedMs: 0 };
        throw new Error(`Unexpected IPC: ${command}`);
      },
    };
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: () => {} };
  });
  const url = server.resolvedUrls.local[0];
  const row = (id) => page.locator(`tbody tr[data-mod-id="${id}"]`);
  const order = () => page.locator("tbody tr").evaluateAll((rows) => rows.map((r) => r.dataset.modId).join(""));
  const folder = (name) => page.locator(".category-folder").filter({ has: page.locator(".category-label", { hasText: name }) });
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 90000 });
  await row("f").waitFor();
  assert.equal(await order(), "abcdef");
  await page.getByRole("button", { name: "新建分类", exact: true }).click();
  await page.getByLabel("分类名称", { exact: true }).fill("我的组合");
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await folder("我的组合").waitFor();
  await row("b").locator(".selection-checkbox").check();
  await row("d").locator(".selection-checkbox").check();
  await page.getByLabel("归入分类", { exact: true }).selectOption("我的组合");
  await page.waitForFunction(() => window.categoryTest.assignments.b === "我的组合");
  assert.match(await row("b").innerText(), /我的组合/);
  assert.ok(await page.getByRole("button", { name: "保存 Profile", exact: true }).isDisabled(), "classification must not alter Profile dirty state");
  await page.getByTitle("置底", { exact: true }).click();
  assert.equal(await order(), "acebdf", "selected noncontiguous block must preserve internal order");
  await page.getByRole("button", { name: "保存 Profile", exact: true }).click();
  await page.waitForFunction(() => window.categoryTest.writes.length === 1);
  assert.deepEqual(await page.evaluate(() => window.categoryTest.writes[0].activeMods), ["d", "b", "e", "c", "a"]);
  await page.reload({ waitUntil: "domcontentloaded" });
  await row("f").waitFor();
  assert.equal(await order(), "acebdf");
  assert.match(await row("d").innerText(), /我的组合/);
  // Ctrl and Shift range selection, then drag the entire selected set into a category.
  await row("a").click();
  await row("c").click({ modifiers: ["Control"] });
  await row("b").click({ modifiers: ["Shift"] });
  assert.deepEqual(await page.locator('tbody tr[aria-selected="true"]').evaluateAll((rows) => rows.map((r) => r.dataset.modId)), ["c", "e", "b"]);
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer();
    data.setData("application/x-ets2-mod-ids", JSON.stringify(["c", "e", "b"]));
    return data;
  });
  await folder("地图组").dispatchEvent("drop", { dataTransfer: transfer });
  await page.waitForFunction(() => window.categoryTest.assignments.c === "地图组");
  // The category checkbox affects the full category and excludes unrelated Mods.
  await folder("地图组").locator(".selection-checkbox").uncheck();
  assert.equal(await row("a").locator(".toggle.is-on").count(), 1);
  assert.equal(await row("d").locator(".toggle.is-on").count(), 1);
  for (const id of ["c", "e", "b"]) assert.equal(await row(id).locator(".toggle.is-on").count(), 0);
  await folder("地图组").locator(".selection-checkbox").check();
  // A search-scoped operation must never enable the unrelated disabled F Mod.
  await page.locator(".search-box input").fill("A Mod");
  await page.locator(".batch-toolbar").getByRole("button", { name: "禁用", exact: true }).click();
  await page.locator(".batch-toolbar").getByRole("button", { name: "启用", exact: true }).click();
  await page.locator(".search-box input").fill("");
  assert.equal(await row("f").locator(".toggle.is-on").count(), 0);
  // Category-scoped priority actions include members hidden by a search.
  await folder("地图组").locator(".category-label").click();
  await page.locator(".search-box input").fill("C Mod");
  await page.getByLabel("操作范围", { exact: true }).selectOption("category");
  await page.getByTitle("置顶", { exact: true }).click();
  await page.locator(".category-section > .category-item").click();
  await page.locator(".search-box input").fill("");
  assert.equal((await order()).slice(0, 3), "ceb");
  // Rename and delete preserve enabled state, ordering, and files.
  await folder("我的组合").getByRole("button", { name: "分类操作: 我的组合", exact: true }).click();
  await page.getByRole("menuitem", { name: "重命名分类", exact: true }).click();
  await page.getByLabel("分类名称", { exact: true }).fill("长期保留");
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await folder("长期保留").waitFor();
  assert.match(await row("d").innerText(), /长期保留/);
  await page.evaluate(() => { window.categoryTest.fail = true; });
  await row("a").click();
  await page.getByLabel("归入分类", { exact: true }).selectOption("长期保留");
  await page.locator(".error-banner").waitFor();
  assert.match(await row("a").innerText(), /未分类/);
  await page.evaluate(() => { window.categoryTest.fail = false; });
  await folder("长期保留").getByRole("button", { name: "分类操作: 长期保留", exact: true }).click();
  await page.getByRole("menuitem", { name: "删除分类", exact: true }).click();
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await page.waitForFunction(() => !window.categoryTest.folders.includes("长期保留"));
  assert.match(await row("d").innerText(), /未分类/);
  assert.equal(await row("d").locator(".toggle.is-on").count(), 1);
  await page.getByRole("button", { name: "保存 Profile", exact: true }).click();
  await page.getByRole("button", { name: /只读测试/ }).click();
  await row("a").click();
  assert.ok(await page.getByRole("button", { name: "禁用", exact: true }).isDisabled());
  assert.ok(await page.getByTitle("置顶", { exact: true }).isDisabled());
  await mkdir(output, { recursive: true });
  for (const locale of ["zh_CN", "en_US", "ru_RU"]) {
    await page.locator(".language-picker select").selectOption(locale);
    await page.setViewportSize({ width: locale === "ru_RU" ? 1100 : 1440, height: 950 });
    await page.screenshot({ path: `${output}/${locale}.png` });
    const overflow = await page.locator(".batch-toolbar, .priority-row, .preset-row").evaluateAll((elements) => elements.some((el) => el.scrollWidth > el.clientWidth + 1));
    assert.equal(overflow, false, `${locale} toolbar must fit`);
    const headerFits = await page.locator("th.check-column").evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const text = range.getBoundingClientRect();
      const cell = element.getBoundingClientRect();
      return text.left >= cell.left && text.right <= cell.right;
    });
    assert.equal(headerFits, true, `${locale} enabled header must not overlap the name column`);
  }
  assert.deepEqual(errors, []);
  console.log("Category UI passed: create/rename/delete, multi/range selection, drag assignment, persistent reload, stable batch sorting, reversed Profile save, category toggles, search scope, hidden category members, write failure, read-only guards and 3 locales.");
} finally {
  await browser.close();
  await server.close();
}
