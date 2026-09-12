import type { ModRecord } from "./types";

export const ALL_CATEGORIES = "\0all";
export type BatchAction = "enable" | "disable" | "invert";
export type MoveDirection = "top" | "up" | "down" | "bottom";

export function batchEnabled(mods: ModRecord[], ids: readonly string[], action: BatchAction): ModRecord[] {
  const selected = new Set(ids);
  let changed = false;
  const next = mods.map((mod) => {
    if (!selected.has(mod.id)) return mod;
    const enabled = action === "invert" ? !mod.enabled : action === "enable";
    if (enabled === mod.enabled) return mod;
    changed = true;
    return { ...mod, enabled };
  });
  return changed ? [...next.filter((mod) => mod.enabled), ...next.filter((mod) => !mod.enabled)] : mods;
}

export function moveBatch(
  mods: ModRecord[], ids: readonly string[], direction: MoveDirection, steps = 1, beforeId?: string,
): ModRecord[] {
  const selected = new Set(ids);
  const active = mods.filter((mod) => mod.enabled);
  const moving = active.filter((mod) => selected.has(mod.id));
  if (!moving.length || (beforeId && selected.has(beforeId))) return mods;
  const first = active.findIndex((mod) => selected.has(mod.id));
  const last = active.findIndex((mod) => mod.id === moving[moving.length - 1].id);
  const count = Math.max(0, Math.floor(steps));
  if (!beforeId && (direction === "up" || direction === "down") && !count) return mods;
  if (!beforeId && direction === "up" && first === 0) return mods;
  let target = beforeId ? active.findIndex((mod) => mod.id === beforeId)
    : direction === "top" ? 0 : direction === "bottom" ? active.length
    : direction === "up" ? Math.max(0, first - count) : Math.min(active.length, last + 1 + count);
  if (target < 0) return mods;
  // This is the old reorder_before contract: gather the selected block in its
  // existing order, then insert before an index in the original active list.
  const before = active.slice(0, target).filter((mod) => !selected.has(mod.id)).length;
  const remaining = active.filter((mod) => !selected.has(mod.id));
  remaining.splice(before, 0, ...moving);
  const next = [...remaining, ...mods.filter((mod) => !mod.enabled)];
  return next.every((mod, index) => mod === mods[index]) ? mods : next;
}
