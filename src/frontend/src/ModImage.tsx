import { memo, useEffect, useRef, useState } from "react";
import { ImageOff } from "lucide-react";
import { useModStore } from "./store";
import type { ModRecord } from "./types";

export function ModImage({ src, fallback, className, alt }: {
  src?: string; fallback?: string; className: string; alt: string;
}) {
  const [failed, setFailed] = useState<string[]>([]);
  useEffect(() => setFailed([]), [src, fallback]);
  const url = [src, fallback].find((value) => value && !failed.includes(value));
  if (!url) return <span className={`${className} image-placeholder`} aria-label={alt}><ImageOff size={16} /></span>;
  return <img className={className} src={url} alt={alt} loading="eager" decoding="async" referrerPolicy="no-referrer"
    onError={() => setFailed((values) => [...values, url])} />;
}

export const ModThumbnail = memo(function ModThumbnail({ mod }: { mod: ModRecord }) {
  const element = useRef<HTMLSpanElement>(null);
  const load = useModStore((state) => state.loadModMedia);
  useEffect(() => {
    if (mod.mediaLoaded || (mod.mediaAttempts ?? 0) >= 3 || !element.current) return;
    const scrollRoot = element.current.closest<HTMLElement>(".table-wrap");
    const loadIfVisible = () => {
      const node = element.current;
      if (!node) return;
      const nodeRect = node.getBoundingClientRect();
      const rootRect = scrollRoot?.getBoundingClientRect();
      const top = rootRect?.top ?? 0;
      const bottom = rootRect?.bottom ?? window.innerHeight;
      if (nodeRect.bottom < top - 240 || nodeRect.top > bottom + 240) return;
      observer.disconnect();
      scrollRoot?.removeEventListener("scroll", loadIfVisible);
      void load(mod.id);
    };
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      scrollRoot?.removeEventListener("scroll", loadIfVisible);
      void load(mod.id);
    }, { root: scrollRoot, rootMargin: "240px" });
    observer.observe(element.current);
    scrollRoot?.addEventListener("scroll", loadIfVisible, { passive: true });
    // Some WebView builds do not reliably deliver IntersectionObserver events
    // for a nested table scroller; check the initial viewport explicitly.
    loadIfVisible();
    return () => {
      observer.disconnect();
      scrollRoot?.removeEventListener("scroll", loadIfVisible);
    };
  }, [mod.id, mod.path, mod.size, mod.modifiedMs, mod.mediaLoaded, mod.mediaAttempts, load]);
  return <span ref={element} className="mod-thumbnail">
    <ModImage src={mod.previewUrl || mod.iconUrl} fallback={mod.iconUrl} className="mod-badge" alt={mod.displayName} />
  </span>;
});
