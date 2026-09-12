import type { ModBackend, ModMedia } from "./backend";
import type { ModRecord } from "./types";

export function mediaKey(mod: ModRecord): string {
  return JSON.stringify([mod.id, mod.path, mod.size, mod.modifiedMs]);
}

export function createMediaLoader(backend: Pick<ModBackend, "loadModMedia">) {
  const pending = new Map<string, Promise<ModMedia | undefined>>();
  const queue: Array<() => void> = [];
  let running = 0;
  function drain() {
    while (running < 2 && queue.length) {
      running += 1;
      queue.shift()!();
    }
  }
  return (mod: ModRecord): Promise<ModMedia | undefined> => {
    const key = mediaKey(mod);
    const existing = pending.get(key);
    if (existing) return existing;
    const request = new Promise<ModMedia | undefined>((resolve, reject) => {
      queue.push(() => {
        void backend.loadModMedia([mod]).then(
          (rows) => resolve(rows.find((row) => row.modId === mod.id)), reject,
        ).finally(() => {
          pending.delete(key);
          running -= 1;
          drain();
        });
      });
    });
    pending.set(key, request);
    drain();
    return request;
  };
}
