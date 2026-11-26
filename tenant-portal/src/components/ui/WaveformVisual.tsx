import { useReducedMotion } from 'framer-motion';
import { useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';

/** Who the waveform is "listening" to. */
export type Voice = 'idle' | 'caller' | 'thinking' | 'agent';

interface WaveformVisualProps {
  className?: string;
  /** Number of bars; fewer bars for compact placements. */
  bars?: number;
  /** 0–1 overall loudness multiplier. */
  intensity?: number;
  /**
   * Who is speaking. The bars blend between voices: cream for the caller, the
   * signal gradient for the agent, an amber scanning pulse while it thinks.
   * Defaults to the agent, which reads well on both paper and ink.
   */
  voice?: Voice;
}

type Hsl = [number, number, number];
const VOICES: Voice[] = ['idle', 'caller', 'thinking', 'agent'];

function readHsl(element: Element, variable: string, fallback: Hsl): Hsl {
  const raw = getComputedStyle(element).getPropertyValue(variable).trim();
  const parts = raw.split(/[\s,]+/).map((part) => parseFloat(part));
  return parts.length >= 3 && parts.every((value) => Number.isFinite(value))
    ? [parts[0]!, parts[1]!, parts[2]!]
    : fallback;
}

const mix = (a: number, b: number, t: number) => a + (b - a) * t;

/**
 * Canvas voice waveform: a bell-shaped spectrum modulated by layered sines and
 * a slow "speech" envelope so it reads as someone talking, pausing, talking
 * again. Pauses when the tab is hidden; a still frame under reduced motion.
 */
export function WaveformVisual({
  className,
  bars = 56,
  intensity = 1,
  voice = 'agent',
}: WaveformVisualProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const voiceRef = useRef<Voice>(voice);
  const redrawRef = useRef<(() => void) | null>(null);
  const reduceMotion = useReducedMotion();

  voiceRef.current = voice;

  // Under reduced motion there is no loop, so a voice change repaints once.
  useEffect(() => {
    redrawRef.current?.();
  }, [voice]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;

    let width = 0;
    let height = 0;
    let frame = 0;
    let lastTime = 0;
    let colors = {
      caller: [40, 30, 84] as Hsl,
      agent: [14, 96, 58] as Hsl,
      agentEnd: [36, 96, 58] as Hsl,
      thinking: [36, 96, 58] as Hsl,
    };
    // Blend weight per voice; eased toward the current one every frame.
    const weights: Record<Voice, number> = { idle: 0, caller: 0, thinking: 0, agent: 0 };
    weights[voiceRef.current] = 1;

    const readColors = () => {
      colors = {
        caller: readHsl(canvas, '--voice-caller', colors.caller),
        agent: readHsl(canvas, '--voice-agent', colors.agent),
        agentEnd: readHsl(canvas, '--brand-amber', colors.agentEnd),
        thinking: readHsl(canvas, '--voice-thinking', colors.thinking),
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

    const ease = (dt: number) => {
      const target = voiceRef.current;
      const k = 1 - Math.exp(-dt * 7);
      for (const v of VOICES) weights[v] = mix(weights[v], v === target ? 1 : 0, k);
    };

    const draw = (timeMs: number) => {
      context.clearRect(0, 0, width, height);
      if (width === 0 || height === 0) return;

      const t = timeMs / 1000;
      const slot = width / bars;
      const barWidth = Math.max(2, slot * 0.5);
      const half = (bars - 1) / 2;

      // Speech envelopes: bursts separated by short gaps; the agent is steadier.
      const callerEnvelope = 0.3 + 0.7 * Math.max(0, Math.sin(t * 1.9) * 0.6 + Math.sin(t * 3.7) * 0.4) ** 0.6;
      const agentEnvelope = 0.42 + 0.58 * Math.max(0, Math.sin(t * 1.25)) ** 0.5;
      // The thinking pulse sweeps across and back.
      const sweep = (Math.sin(t * 2.4) * 0.5 + 0.5) * (bars - 1);

      for (let i = 0; i < bars; i += 1) {
        const distance = Math.abs(i - half) / half;
        const bell = 0.22 + 0.78 * Math.cos((distance * Math.PI) / 2) ** 1.4;
        const noise =
          (Math.sin(i * 0.55 + t * 3.1) +
            0.6 * Math.sin(i * 0.23 - t * 1.7) +
            0.35 * Math.sin(i * 1.31 + t * 5.3)) /
          1.95;
        const jitter =
          (Math.sin(i * 0.91 + t * 6.3) + 0.7 * Math.sin(i * 0.37 - t * 4.1)) / 1.7;

        const speaking = 0.45 + 0.55 * noise;
        const amplitudes: Record<Voice, number> = {
          idle: 0.08 + 0.05 * Math.sin(i * 0.4 + t * 1.2),
          caller: bell * (0.4 + 0.6 * (0.6 * noise + 0.4 * jitter)) * callerEnvelope,
          thinking: 0.08 + 0.42 * Math.exp(-(((i - sweep) / (bars * 0.07)) ** 2)),
          agent: bell * speaking * agentEnvelope,
        };

        let amplitude = 0;
        for (const v of VOICES) amplitude += weights[v] * amplitudes[v];

        // Colour: the agent runs signal → amber across the bars.
        const position = i / Math.max(1, bars - 1);
        const agentColor: Hsl = [
          mix(colors.agent[0], colors.agentEnd[0], position),
          mix(colors.agent[1], colors.agentEnd[1], position),
          mix(colors.agent[2], colors.agentEnd[2], position),
        ];
        const palette: Record<Voice, Hsl> = {
          idle: colors.caller,
          caller: colors.caller,
          thinking: colors.thinking,
          agent: agentColor,
        };
        let h = 0;
        let s = 0;
        let l = 0;
        for (const v of VOICES) {
          h += weights[v] * palette[v][0];
          s += weights[v] * palette[v][1];
          l += weights[v] * palette[v][2];
        }
        const alpha = 0.55 + 0.45 * Math.min(1, amplitude * 1.6) - weights.idle * 0.25;

        const barHeight = Math.max(barWidth, height * 0.92 * Math.abs(amplitude) * intensity);
        const x = i * slot + (slot - barWidth) / 2;
        const y = (height - barHeight) / 2;
        context.fillStyle = `hsl(${h} ${s}% ${l}% / ${alpha})`;
        context.beginPath();
        context.roundRect(x, y, barWidth, barHeight, barWidth / 2);
        context.fill();
      }
    };

    const loop = (time: number) => {
      const dt = lastTime ? Math.min(0.1, (time - lastTime) / 1000) : 0;
      lastTime = time;
      ease(dt);
      draw(time);
      frame = window.requestAnimationFrame(loop);
    };

    const drawStill = () => {
      for (const v of VOICES) weights[v] = v === voiceRef.current ? 1 : 0;
      draw(1800);
    };

    const start = () => {
      window.cancelAnimationFrame(frame);
      lastTime = 0;
      if (reduceMotion) drawStill();
      else frame = window.requestAnimationFrame(loop);
    };

    const onVisibility = () => {
      if (document.hidden) window.cancelAnimationFrame(frame);
      else start();
    };

    // Re-read colours when the theme class flips.
    const themeObserver = new MutationObserver(() => {
      readColors();
      if (reduceMotion) drawStill();
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

    const resizeObserver = new ResizeObserver(() => {
      resize();
      if (reduceMotion) drawStill();
    });
    resizeObserver.observe(canvas);

    redrawRef.current = reduceMotion ? drawStill : null;
    readColors();
    resize();
    start();
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      redrawRef.current = null;
      window.cancelAnimationFrame(frame);
      resizeObserver.disconnect();
      themeObserver.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [bars, intensity, reduceMotion]);

  return <canvas ref={canvasRef} aria-hidden className={cn('block h-full w-full', className)} />;
}
