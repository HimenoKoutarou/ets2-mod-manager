import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const output = fileURLToPath(new URL("../build/mod-directory-smoke/", import.meta.url));
const server = await createServer({ root, server: { host: "127.0.0.1", port: 0 } });
await server.listen();
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
try {
  await mkdir(output, { recursive: true });
  for (const [language, title, choose, relocate, confirm, restore, close] of [
    ["zh_CN", "Mod 目录", "选择文件夹", "迁移到新位置", "确认操作", "还原到游戏目录", "关闭"],
    ["en_US", "Mod directory", "Choose folder", "Relocate", "Confirm", "Restore game directory", "Close"],
    ["ru_RU", "Папка модов", "Выбрать папку", "Перенести", "Подтвердить", "Вернуть в папку игры", "Закрыть"],
  ]) {
    const page = await browser.newPage({ viewport: { width: language === "ru_RU" ? 1100 : 1440, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript(() => {
      let sequence = 0;
      const callbacks = new Map();
      const listeners = new Map();
      const gamePath = "C:\\Users\\Test\\Documents\\Euro Truck Simulator 2\\mod";
      const target = "F:\\ETS2 Mods\\中文 & Русский";
      const state = { gamePath, target, kind: "local", actualPath: gamePath, recoveryPending: false, changes: [], scans: 0, fail: false };
      window.directoryTest = state;
      const emit = (event, payload) => {
        for (const [id, args] of listeners) if (args.event === event) callbacks.get(args.handler)?.({ event, payload, id });
      };
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
          if (command === "profile_list") return [{ id: "local", name: "本地 Profile", company: "", location: "local", writable: true }];
          if (command === "mod_list") return [{ id: "sample", packageName: "sample", path: `${state.actualPath}\\sample.scs`, displayName: "Sample Mod", enabled: true }];
          if (["mod_media_batch", "preset_list", "save_list_local"].includes(command)) return [];
          if (command === "profile_write_active") return { success: true };
          if (command === "mod_scan") { state.scans++; return { total: 1, added: 0, updated: 0, removed: 0, inspected: 0, elapsedMs: 0 }; }
          if (command === "mod_directory_status") return { gamePath, actualPath: state.actualPath, kind: state.kind, recoveryPending: state.recoveryPending };
          if (command === "mod_directory_pick") return target;
          if (command === "mod_directory_change") {
            state.changes.push(args);
            emit("ets2-directory-progress", { phase: "copy", path: `${gamePath}\\sample.scs`, completed: 37, total: 100 });
            await new Promise((resolve) => setTimeout(resolve, 650));
            if (state.fail) throw "Close ETS2 / ATS before changing the Mod directory.";
            state.kind = args.operation === "restore" || args.operation === "recover" ? "local" : "linked";
            state.actualPath = state.kind === "local" ? gamePath : args.target;
            state.recoveryPending = false;
            return { status: { gamePath, actualPath: state.actualPath, kind: state.kind, recoveryPending: false }, retainedPath: "F:\\backup" };
          }
          throw new Error(`Unexpected IPC: ${command}`);
        },
      };
      window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: () => {} };
    });
    await page.goto(server.resolvedUrls.local[0], { waitUntil: "domcontentloaded", timeout: 90000 });
    await page.locator(".language-picker select").selectOption(language);
    await page.getByRole("button", { name: title, exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: choose, exact: true }).click();
    assert.equal(await dialog.locator("input").inputValue(), "F:\\ETS2 Mods\\中文 & Русский");
    await dialog.getByRole("button", { name: relocate, exact: true }).click();
    assert.equal(await page.evaluate(() => window.directoryTest.changes.length), 0, "confirmation must precede writes");
    await dialog.getByRole("button", { name: confirm, exact: true }).click();
    await dialog.locator("progress").waitFor();
    assert.equal(await dialog.locator("progress").getAttribute("value"), "37");
    assert.ok(await dialog.getByRole("button", { name: close, exact: true }).isDisabled());
    assert.match(await dialog.locator(".directory-progress").innerText(), /sample.scs/);
    await page.screenshot({ path: `${output}/${language}-progress.png` });
    await dialog.locator(".directory-notice").waitFor();
    assert.equal(await page.evaluate(() => window.directoryTest.scans), 0, "relocation must reuse the persisted index");
    await dialog.getByRole("button", { name: close, exact: true }).click();
    await page.getByRole("button", { name: title, exact: true }).click();
    await dialog.getByRole("button", { name: restore, exact: true }).click();
    await dialog.getByRole("button", { name: confirm, exact: true }).click();
    await dialog.locator(".directory-notice").waitFor();
    assert.equal(await page.evaluate(() => window.directoryTest.kind), "local");
    assert.equal(await page.evaluate(() => window.directoryTest.changes[1].operation), "restore");
    await dialog.getByRole("button", { name: close, exact: true }).click();
    if (language === "zh_CN") {
      // Dirty priority/enabled edits block migration without discarding them.
      await page.locator("tbody .toggle").click();
      await page.getByRole("button", { name: title, exact: true }).click();
      await dialog.getByRole("button", { name: choose, exact: true }).click();
      assert.ok(await dialog.getByRole("button", { name: relocate, exact: true }).isDisabled());
      await dialog.getByRole("button", { name: close, exact: true }).click();
      await page.getByRole("button", { name: "保存 Profile", exact: true }).click();
      await page.evaluate(() => { window.directoryTest.kind = "broken"; });
      await page.getByRole("button", { name: title, exact: true }).click();
      await dialog.getByRole("button", { name: choose, exact: true }).click();
      await dialog.getByRole("button", { name: "修复链接", exact: true }).click();
      await dialog.getByRole("button", { name: confirm, exact: true }).click();
      await dialog.locator(".directory-notice").waitFor();
      assert.equal(await page.evaluate(() => window.directoryTest.scans), 1);
      await dialog.getByRole("button", { name: close, exact: true }).click();
      await page.evaluate(() => { window.directoryTest.recoveryPending = true; });
      await page.getByRole("button", { name: title, exact: true }).click();
      await dialog.getByRole("button", { name: "恢复上次未完成的操作", exact: true }).click();
      await dialog.getByRole("button", { name: confirm, exact: true }).click();
      await dialog.locator(".directory-notice").waitFor();
      assert.equal(await page.evaluate(() => window.directoryTest.recoveryPending), false);
      await dialog.getByRole("button", { name: close, exact: true }).click();
      await page.evaluate(() => { window.directoryTest.fail = true; });
      await page.getByRole("button", { name: title, exact: true }).click();
      await dialog.getByRole("button", { name: choose, exact: true }).click();
      await dialog.getByRole("button", { name: relocate, exact: true }).click();
      await dialog.getByRole("button", { name: confirm, exact: true }).click();
      await dialog.locator(".directory-error").waitFor();
      assert.match(await dialog.locator(".directory-error").innerText(), /Close ETS2/);
      assert.ok(await dialog.getByRole("button", { name: close, exact: true }).isEnabled());
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Directory UI passed: three locales, chooser, confirmation, file progress, busy close guard, persistence, restore, repair, interrupted recovery, dirty Mod protection, native error and no relocation rescan.");
} finally {
  await browser.close();
  await server.close();
}
