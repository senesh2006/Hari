import { useEffect, useState, type ComponentType } from "react";

// The exact Framer module the user asked for. It's a remote ESM that brings its
// own React/framer-motion from esm.sh, so we load it at runtime (outside Vite's
// bundler) and render it as an isolated visual layer. If it can't load (offline,
// blocked egress, etc.) we fall back to the dependency-free CSS liquid blobs.
const FRAMER_URL =
  "https://framer.com/m/AnimatedLiquidBackground-Prod-vIhm.js@ghH1aHLmGZ0iE7qXDFVk";

export default function Background() {
  const [Comp, setComp] = useState<ComponentType<any> | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const mod: any = await import(/* @vite-ignore */ FRAMER_URL);
        const C = mod?.default ?? mod?.AnimatedLiquidBackground ?? null;
        if (alive && C) setComp(() => C);
      } catch {
        /* keep CSS fallback */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="bg" aria-hidden="true">
      {Comp ? (
        <div style={{ position: "absolute", inset: 0 }}>
          <Comp style={{ width: "100%", height: "100%" }} />
        </div>
      ) : (
        <>
          <span className="blob b1" />
          <span className="blob b2" />
          <span className="blob b3" />
          <span className="blob b4" />
        </>
      )}
    </div>
  );
}
