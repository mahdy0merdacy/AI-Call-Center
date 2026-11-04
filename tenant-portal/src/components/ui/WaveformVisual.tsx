import { useReducedMotion } from 'framer-motion';
import { useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';

interface WaveformVisualProps {
  className?: string;
  /** Number of bars; fewer bars for compact placements. */
  bars?: number;
  /** 0–1 overall loudness multiplier. */
  intensity?: number;
}

function cssColor(element: Element, variable: string, alpha = 1): string {
  const value = getComputedStyle(element).getPropertyValue(variable).trim();
  return value ? `hsl(${value} / ${alpha})` : `rgba(255,255,255,${alpha})`;
}

/**
 * Canvas voice waveform: a bell-shaped spectrum modulated by layered sines
 * and a slow "speech" envelope so it reads as someone talking, pausing,
 * talking again. Pauses when the tab is hidden; static under reduced motion.
 */
export function WaveformVisual({ className, bars = 56, intensity = 1 }: WaveformVisualProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;

    let width = 0;
    let height = 0;
    let frame = 0;
    let colors = { start: '', mid: '', end: '' };

    const readColors = () => {
      colors = {
        start: cssColor(canvas, '--brand-indigo'),
        mid: cssColor(canvas, '--brand-violet'),
        end: cssColor(canvas, '--brand-teal'),
      };
    };

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const draw = (timeMs: number) => {
      context.clearRect(0, 0, width, height);
      if (width === 0 || height === 0) return;

      const t = timeMs / 1000;
      const slot = width / bars;
      const barWidth = Math.max(2, slot * 0.52);
      const gradient = context.createLinearGradient(0, 0, width, 0);
      gradient.addColorStop(0, colors.start);
      gradient.addColorStop(0.55, colors.mid);
      gradient.addColorStop(1, colors.end);
      context.fillStyle = gradient;

      // Speech envelope: bursts of ~2.5s separated by short quiet gaps.
      const envelope = 0.28 + 0.72 * Math.max(0, Math.sin(t * 1.25)) ** 0.5;
      const half = (bars - 1) / 2;

      for (let i = 0; i < bars; i += 1) {
        const distance = Math.abs(i - half) / half;
        const bell = 0.22 + 0.78 * Math.cos((distance * Math.PI) / 2) ** 1.4;
        const noise =
          (Math.sin(i * 0.55 + t * 3.1) +
            0.6 * Math.sin(i * 0.23 - t * 1.7) +
            0.35 * Math.sin(i * 1.31 + t * 5.3)) /
          1.95;
        const amplitude = 0.45 + 0.55 * noise;
        const barHeight = Math.max(
          barWidth,
          height * 0.92 * bell * amplitude * envelope * intensity,
        );
        const x = i * slot + (slot - barWidth) / 2;
        const y = (height - barHeight) / 2;
        context.beginPath();
        context.roundRect(x, y, barWidth, barHeight, barWidth / 2);
        context.fill();
      }
    };

    const loop = (time: number) => {
      draw(time);
      frame = window.requestAnimationFrame(loop);
    };

    const start = () => {
      window.cancelAnimationFrame(frame);
      if (reduceMotion) draw(1800);
      else frame = window.requestAnimationFrame(loop);
    };

    const onVisibility = () => {
      if (document.hidden) window.cancelAnimationFrame(frame);
      else start();
    };

    // Re-read colours when the theme class flips.
    const themeObserver = new MutationObserver(() => {
      readColors();
      if (reduceMotion) draw(1800);
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

    const resizeObserver = new ResizeObserver(() => {
      resize();
      if (reduceMotion) draw(1800);
    });
    resizeObserver.observe(canvas);

    readColors();
    resize();
    start();
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      window.cancelAnimationFrame(frame);
      resizeObserver.disconnect();
      themeObserver.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [bars, intensity, reduceMotion]);

  return <canvas ref={canvasRef} aria-hidden className={cn('block h-full w-full', className)} />;
}
