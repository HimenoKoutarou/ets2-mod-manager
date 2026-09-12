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
  return <img className={className} src={url} alt={alt} loading="lazy" decoding="async"
    onError={() => setFailed((values) => [...values, url])} />;
}

export const ModThumbnail = memo(function ModThumbnail({ mod }: { mod: ModRecord }) {
  const element = useRef<HTMLSpanElement>(null);
  const load = useModStore((state) => state.loadModMedia);
  useEffect(() => {
    if (mod.mediaLoaded || !element.current) return;
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      void load(mod.id);
    }, { rootMargin: "120px" });
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [mod.id, mod.path, mod.size, mod.modifiedMs, mod.mediaLoaded, load]);
  return <span ref={element} className="mod-thumbnail">
    <ModImage src={mod.previewUrl || mod.iconUrl} fallback={mod.iconUrl} className="mod-badge" alt={mod.displayName} />
  </span>;
});
