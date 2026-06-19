import { useEffect, useRef } from "react";

export type OrbMode = "idle" | "active" | "listening";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Canvas voice-orb visualizer (ported from the original vanilla blob renderer). */
export default function Orb({ mode }: { mode: OrbMode }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const modeRef = useRef<OrbMode>(mode);
  modeRef.current = mode;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let energy = 0;
    let t = 0;
    let raf = 0;

    const sizeCanvas = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      const w = rect.width || 48;
      const h = rect.height || 48;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const drawBlob = (w: number, h: number, cx: number, cy: number, active: boolean) => {
      const base = Math.min(w, h) * 0.27;
      const target = active ? 0.16 + 0.12 * (0.5 + 0.5 * Math.sin(t * 3.2)) : 0.06;
      energy += (target - energy) * 0.08;
      const pts = 90;
      ctx.beginPath();
      for (let i = 0; i <= pts; i++) {
        const a = (i / pts) * Math.PI * 2;
        const r =
          base *
          (1 +
            energy *
              (Math.sin(a * 3 + t * 1.6) * 0.5 +
                Math.sin(a * 5 - t * 1.1) * 0.3 +
                Math.sin(a * 2 + t * 0.7) * 0.2));
        const x = cx + Math.cos(a) * r;
        const y = cy + Math.sin(a) * r;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.closePath();
      const g = ctx.createRadialGradient(cx - base * 0.35, cy - base * 0.4, base * 0.2, cx, cy, base * 1.5);
      g.addColorStop(0, cssVar("--orb-1") || "#9cc4ff");
      g.addColorStop(0.55, cssVar("--orb-2") || "#4f93ff");
      g.addColorStop(1, cssVar("--orb-3") || "#2f6fe0");
      ctx.fillStyle = g;
      ctx.shadowColor = "rgba(79,147,255,.55)";
      ctx.shadowBlur = 22;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.beginPath();
      ctx.arc(cx - base * 0.28, cy - base * 0.32, base * 0.32, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,255,255,.2)";
      ctx.fill();
    };

    const loop = () => {
      t += 0.016;
      const w = canvas.clientWidth || 48;
      const h = canvas.clientHeight || 48;
      ctx.clearRect(0, 0, w, h);
      const m = modeRef.current;
      drawBlob(w, h, w / 2, h / 2, m === "active" || m === "listening");
      raf = requestAnimationFrame(loop);
    };

    sizeCanvas();
    window.addEventListener("resize", sizeCanvas);
    loop();
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", sizeCanvas);
    };
  }, []);

  return <canvas ref={canvasRef} width={48} height={48} />;
}
