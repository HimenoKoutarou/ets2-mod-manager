import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const output = fileURLToPath(new URL("../build/mod-media-smoke/", import.meta.url));
const server = await createServer({ root, server: { host: "127.0.0.1", port: 0 } });
await server.listen();
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: "zh-CN" });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    let sequence = 0;
    const callbacks = new Map();
    const listeners = new Map();
    const makeImage = (color) => {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 34;
      const context = canvas.getContext("2d");
      context.fillStyle = color;
      context.fillRect(0, 0, 34, 34);
      return canvas.toDataURL();
    };
    const state = { calls: [], media: [], running: 0, peak: 0, preview: makeImage("#2563eb"), icon: makeImage("#16a34a") };
    window.mediaTest = state;
    const emit = (event, payload) => {
      for (const [id, args] of listeners) if (args.event === event) callbacks.get(args.handler)?.({ event, payload, id });
    };
    window.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main", windowLabel: "main" } },
      transformCallback: (callback) => { const id = ++sequence; callbacks.set(id, callback); return id; },
      unregisterCallback: (id) => callbacks.delete(id),
      invoke: async (command, args = {}) => {
        state.calls.push({ command, args });
        if (command === "plugin:event|listen") { const id = ++sequence; listeners.set(id, args); return id; }
        if (command === "plugin:event|unlisten") { listeners.delete(args.eventId); return; }
        if (command === "plugin:event|emit_to") {
          if (args.event === "ets2-main-ready") setTimeout(() => emit("ets2-initialization-complete"), 0);
          return;
        }
        if (command === "plugin:window|get_all_windows") return ["main", "initializer"];
        if (command === "plugin:window|show" || command === "plugin:window|close") return;
        if (command === "profile_list") return [{ id: "test", name: "Test profile", location: "local", company: "", writable: true }];
        if (command === "mod_list") return Array.from({ length: 100 }, (_, index) => ({
          id: `mod-${index}`, packageName: `package-${index}`, path: `C:\\test\\mod-${index}.scs`,
          packageType: "scs", displayName: `Preview test ${index}`, enabled: true, size: index, modifiedMs: 1,
        }));
        if (command === "save_list_local" || command === "preset_list") return [];
        if (command === "category_list") return { folders: [], assignments: {} };
        if (command === "mod_media_batch") {
          const id = args.requests[0].modId;
          state.media.push(id);
          state.running += 1;
          state.peak = Math.max(state.peak, state.running);
          await new Promise((resolve) => setTimeout(resolve, 80));
          state.running -= 1;
          if (id === "mod-3") return [{ modId: id }];
          return [{ modId: id, previewUrl: id === "mod-2" ? "data:image/png;base64,broken" : state.preview, iconUrl: state.icon }];
        }
        throw new Error(`Unexpected command: ${command}`);
      },
    };
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: () => {} };
  });
  await page.goto(server.resolvedUrls.local[0], { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.waitForFunction(() => document.querySelectorAll("img.mod-badge").length >= 5);
  await page.waitForFunction(() => window.mediaTest.running === 0);
  const result = await page.evaluate(() => ({
    media: window.mediaTest.media, peak: window.mediaTest.peak,
    firstSrc: document.querySelector("tbody tr:nth-child(1) img.mod-badge").src,
    fallbackSrc: document.querySelector("tbody tr:nth-child(3) img.mod-badge").src,
    preview: window.mediaTest.preview, icon: window.mediaTest.icon,
  }));
  assert.ok(result.media.length < 30, `Offscreen rows loaded eagerly: ${result.media.length}`);
  assert.equal(result.peak, 2);
  assert.equal(result.media.filter((id) => id === "mod-0").length, 1, "selected + visible must deduplicate");
  assert.equal(result.firstSrc, result.preview, "row must prefer preview over icon");
  assert.equal(result.fallbackSrc, result.icon, "broken preview must use icon");
  assert.equal(await page.locator("tbody tr:nth-child(4) .image-placeholder").count(), 1);
  await mkdir(output, { recursive: true });
  await page.screenshot({ path: `${output}/visible-rows.png` });
  const row80 = page.locator("tbody tr").nth(80);
  await row80.scrollIntoViewIfNeeded();
  await page.waitForFunction(() => window.mediaTest.media.includes("mod-80"));
  await page.waitForFunction(() => {
    const img = document.querySelector("tbody tr:nth-child(81) img.mod-badge");
    return img?.complete && img.naturalWidth > 0;
  });
  // Reordering must move the existing image with the Mod, without refetching.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.locator("tbody tr").nth(1).click();
  await page.getByTitle("置顶", { exact: true }).click();
  assert.match(await page.locator("tbody tr").first().innerText(), /Preview test 1/);
  assert.equal(await page.locator("tbody tr").first().locator("img.mod-badge").count(), 1);
  const counts = await page.evaluate(() => window.mediaTest.media);
  assert.equal(counts.filter((id) => id === "mod-1").length, 1);
  assert.equal(counts.filter((id) => id === "mod-3").length, 1);
  assert.deepEqual(errors, []);
  console.log("Mod thumbnails passed: visible rows, scroll loading, max 2 workers, deduplication, preview preference, broken-image fallback, missing art and reorder.");
  console.log(`Screenshot: ${output}/visible-rows.png`);
} finally {
  await browser.close();
  await server.close();
}
