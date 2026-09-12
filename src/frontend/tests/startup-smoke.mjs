import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const output = fileURLToPath(new URL("../build/startup-smoke/", import.meta.url));
const server = await createServer({ root, server: { host: "127.0.0.1", port: 0 } });
await server.listen();
const url = server.resolvedUrls.local[0];
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
const errors = [];

async function pageFor(label, locale, width = 520) {
  const page = await browser.newPage({ locale, viewport: { width, height: 420 } });
  page.setDefaultTimeout(20000);
  console.log(`Checking ${label} ${locale} at ${width}px`);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript((label) => {
    let sequence = 0;
    const callbacks = new Map();
    const listeners = new Map();
    const state = { calls: [], resolveScan: null, rejectScan: null, resolveProfiles: null };
    window.startupTest = state;
    state.emit = (event, payload) => {
      for (const [id, listener] of listeners) {
        if (listener.event === event) callbacks.get(listener.handler)?.({ event, payload, id });
      }
    };
    window.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label }, currentWebview: { label, windowLabel: label } },
      transformCallback: (callback) => { const id = ++sequence; callbacks.set(id, callback); return id; },
      unregisterCallback: (id) => callbacks.delete(id),
      invoke: async (command, args = {}) => {
        state.calls.push({ command, args });
        if (command === "plugin:event|listen") {
          const id = ++sequence;
          listeners.set(id, args);
          return id;
        }
        if (command === "plugin:event|unlisten") { listeners.delete(args.eventId); return; }
        if (command === "plugin:event|emit_to") return;
        if (command === "mod_initialize") return new Promise((resolve, reject) => {
          state.resolveScan = resolve;
          state.rejectScan = reject;
        });
        if (command === "profile_list") return new Promise((resolve) => { state.resolveProfiles = resolve; });
        if (command === "category_list") return { folders: [], assignments: {} };
        if (command === "plugin:window|get_all_windows") return ["main", "initializer"];
        if (command === "plugin:window|show" || command === "plugin:window|close") return;
        throw new Error(`Unexpected IPC call: ${command}`);
      },
    };
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: () => {} };
  }, label);
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 90000 });
  return page;
}

try {
  await mkdir(output, { recursive: true });
  for (const [locale, width] of [["zh-CN", 520], ["en-US", 390], ["ru-RU", 520]]) {
    const page = await pageFor("initializer", locale, width);
    await page.waitForFunction(() => window.startupTest.resolveScan);
    const modPath = "E:\\SteamLibrary\\steamapps\\workshop\\content\\227300\\1061306287\\very-long-mod-package-name\\地图模组\\manifest.sii";
    await page.evaluate((path) => window.startupTest.emit("ets2-scan-progress", {
      phase: "metadata", current: 17, total: 323, name: "ProMods / 地图测试 / Карта Европы", path,
    }), modPath);
    await page.waitForFunction(() => document.querySelector(".initializer-current strong").textContent.includes("ProMods"));
    assert.equal(await page.locator("progress").getAttribute("value"), "5");
    assert.match(await page.locator(".initializer-percent").innerText(), /17 \/ 323/);
    assert.equal(await page.locator(".initializer-path").innerText(), modPath);
    await page.waitForFunction(() => /[1-9]/.test(document.querySelector(".initializer-status").textContent));
    const sizes = await page.evaluate(() => ({
      width: innerWidth, height: innerHeight,
      scrollWidth: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight,
    }));
    assert.ok(sizes.scrollWidth <= sizes.width && sizes.scrollHeight <= sizes.height, JSON.stringify(sizes));
    await page.screenshot({ path: `${output}/${locale}.png` });
    // Zero/unknown totals have no fabricated percentage.
    await page.evaluate(() => window.startupTest.emit("ets2-scan-progress", {
      phase: "workshop", current: 0, total: 0, name: "", path: "E:\\Workshop",
    }));
    await page.waitForFunction(() => !document.querySelector("progress").hasAttribute("value"));
    await page.evaluate(() => window.startupTest.rejectScan("Test archive read failure"));
    await page.getByRole("alert").waitFor();
    await page.getByRole("button").click();
    await page.waitForFunction(() => window.startupTest.calls.filter((call) => call.command === "mod_initialize").length === 2);
    await page.evaluate(() => window.startupTest.resolveScan({
      total: 323, added: 0, updated: 0, removed: 0, inspected: 0, elapsedMs: 1,
    }));
    // Main readiness is deliberately delivered after the scan finishes.
    await page.evaluate(() => window.startupTest.emit("ets2-main-ready"));
    await page.waitForFunction(() => window.startupTest.calls.some((call) => call.args.event === "ets2-initialization-complete"));
    assert.equal(await page.evaluate(() => window.startupTest.calls.filter((call) => call.command === "mod_initialize").length), 2);
    assert.equal(await page.evaluate(() => window.startupTest.calls.filter((call) => call.command === "profile_list").length), 0);
    await page.close();
  }
  const main = await pageFor("main", "zh-CN", 1440);
  await main.waitForFunction(() => window.startupTest.calls.filter((call) => call.args.event === "ets2-main-ready").length >= 2);
  await main.evaluate(() => window.startupTest.emit("ets2-initialization-complete"));
  await main.waitForFunction(() => window.startupTest.resolveProfiles);
  assert.equal(await main.evaluate(() => window.startupTest.calls.filter((call) => call.command === "plugin:window|show").length), 0);
  await main.evaluate(() => window.startupTest.emit("ets2-initialization-complete"));
  assert.equal(await main.evaluate(() => window.startupTest.calls.filter((call) => call.command === "profile_list").length), 1);
  await main.evaluate(() => window.startupTest.resolveProfiles([]));
  await main.waitForFunction(() => window.startupTest.calls.some((call) => call.command === "plugin:window|close"));
  const calls = await main.evaluate(() => window.startupTest.calls);
  assert.ok(calls.findIndex((call) => call.command === "plugin:window|show") < calls.findIndex((call) => call.command === "plugin:window|close"));
  assert.equal(calls.filter((call) => call.command === "mod_initialize").length, 0);
  await main.close();
  assert.deepEqual(errors, []);
  console.log("Startup smoke passed: 3 locales, 2 widths, progress/count/path, live timer, retry, StrictMode, late handshake, main-load gate.");
  console.log(`Screenshots: ${output}`);
} finally {
  await browser.close();
  await server.close();
}
