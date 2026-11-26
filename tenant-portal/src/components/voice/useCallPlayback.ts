import { useReducedMotion } from 'framer-motion';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Voice } from '@/components/ui/WaveformVisual';
import type { CallStep, DemoCall } from './demoCalls';

/** Pacing, in ms. Tuned to feel like a real call, slightly sped up. */
const WORD_MS = 250;
const CHAR_MS = 24;
const EVENT_MS = 520;
const THINK_MS = 700;
const THINK_AFTER_EVENT_MS = 260;
const GAP_MS = 520;
const HOLD_MS = 4200;
const TICK_MS = 33;

export type Stage = 'stt' | 'llm' | 'tts' | null;

interface TimedStep {
  step: CallStep;
  start: number;
  /** Caller: last word heard. Agent: typing done. */
  speakEnd: number;
  /** Agent only: end of the pause before it speaks. */
  thinkEnd: number;
  end: number;
  words: string[];
}

function buildTimeline(call: DemoCall): { steps: TimedStep[]; total: number } {
  let cursor = 0;
  let previous: CallStep['kind'] | null = null;
  const steps = call.steps.map((step): TimedStep => {
    const start = cursor;
    let thinkEnd = start;
    let speakEnd = start;
    let end = start;
    const words = step.kind === 'caller' ? step.text.split(/\s+/).filter(Boolean) : [];

    if (step.kind === 'caller') {
      speakEnd = start + words.length * WORD_MS;
      end = speakEnd + GAP_MS;
    } else if (step.kind === 'agent') {
      thinkEnd = start + (step.think ?? (previous === 'event' ? THINK_AFTER_EVENT_MS : THINK_MS));
      speakEnd = thinkEnd + step.text.length * CHAR_MS;
      end = speakEnd + GAP_MS;
    } else {
      speakEnd = start;
      end = start + (step.tag === 'end' ? 0 : EVENT_MS);
    }
    cursor = end;
    previous = step.kind;
    return { step, start, speakEnd, thinkEnd, end, words };
  });
  return { steps, total: cursor };
}

/** One transcript line as it should look at the current instant. */
export type VisibleLine =
  | { kind: 'caller'; key: number; words: string[]; partialFrom: number; final: boolean }
  | { kind: 'agent'; key: number; text: string; thinking: boolean; typing: boolean }
  | { kind: 'event'; key: number; step: Extract<CallStep, { kind: 'event' }> };

function lineAt(timed: TimedStep, index: number, t: number): VisibleLine | null {
  const { step } = timed;
  if (t < timed.start) return null;

  if (step.kind === 'caller') {
    const heard = Math.min(timed.words.length, Math.floor((t - timed.start) / WORD_MS) + 1);
    const final = t >= timed.speakEnd;
    const words = timed.words.slice(0, heard);
    // The recogniser revises a word once it has two more words of context.
    if (step.mishear && !final && heard <= step.mishear.word + 2 && heard > step.mishear.word) {
      words[step.mishear.word] = step.mishear.as;
    }
    return { kind: 'caller', key: index, words, partialFrom: final ? words.length : Math.max(0, heard - 1), final };
  }

  if (step.kind === 'agent') {
    if (t < timed.thinkEnd) return { kind: 'agent', key: index, text: '', thinking: true, typing: false };
    const chars = Math.min(step.text.length, Math.floor((t - timed.thinkEnd) / CHAR_MS));
    return {
      kind: 'agent',
      key: index,
      text: step.text.slice(0, chars),
      thinking: false,
      typing: t < timed.speakEnd,
    };
  }

  return { kind: 'event', key: index, step };
}

function voiceAt(steps: TimedStep[], t: number): { voice: Voice; stage: Stage } {
  const current = steps.find((timed) => t >= timed.start && t < timed.end);
  if (!current) return { voice: 'idle', stage: null };
  const { step } = current;
  if (step.kind === 'caller') {
    return t < current.speakEnd ? { voice: 'caller', stage: 'stt' } : { voice: 'thinking', stage: 'stt' };
  }
  if (step.kind === 'agent') {
    if (t < current.thinkEnd) return { voice: 'thinking', stage: 'llm' };
    return t < current.speakEnd ? { voice: 'agent', stage: 'tts' } : { voice: 'idle', stage: null };
  }
  return step.tag === 'end' ? { voice: 'idle', stage: null } : { voice: 'thinking', stage: 'llm' };
}

export interface CallPlayback {
  call: DemoCall;
  callIndex: number;
  lines: VisibleLine[];
  voice: Voice;
  stage: Stage;
  /** Call clock in seconds, frozen once the call ends. */
  elapsed: number;
  /** Length of the whole call in seconds. */
  duration: number;
  ended: boolean;
  replay: () => void;
}

/**
 * Plays sample calls one after the other, looping. Everything visible is
 * derived from a single clock, so a frame never shows an inconsistent state.
 * The clock stops while the tab is hidden; under reduced motion the first call
 * is shown complete and still.
 */
export function useCallPlayback(calls: DemoCall[]): CallPlayback {
  const reduceMotion = useReducedMotion();
  const [callIndex, setCallIndex] = useState(0);
  const [t, setT] = useState(0);
  const clockRef = useRef(0);
  const safeIndex = calls.length > 0 ? callIndex % calls.length : 0;
  const call = calls[safeIndex]!;
  const timeline = useMemo(() => buildTimeline(call), [call]);
  const totalRef = useRef(timeline.total);
  totalRef.current = timeline.total;
  const countRef = useRef(calls.length);
  countRef.current = calls.length;

  // A different call (rotation, or the tenant's own once its slug loads) starts from zero.
  useEffect(() => {
    clockRef.current = 0;
    setT(0);
  }, [call]);

  useEffect(() => {
    if (reduceMotion) return;
    let last = performance.now();
    let id = 0;

    const tick = () => {
      const now = performance.now();
      const dt = Math.min(250, now - last);
      last = now;
      const next = clockRef.current + dt;
      if (next < totalRef.current + HOLD_MS) {
        clockRef.current = next;
        setT(next);
        return;
      }
      clockRef.current = 0;
      setT(0);
      setCallIndex((index) => (index + 1) % Math.max(1, countRef.current));
    };

    const start = () => {
      window.clearInterval(id);
      last = performance.now();
      id = window.setInterval(tick, TICK_MS);
    };
    const onVisibility = () => {
      if (document.hidden) window.clearInterval(id);
      else start();
    };

    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [reduceMotion]);

  const now = reduceMotion ? timeline.total : t;
  const lines = timeline.steps
    .map((timed, index) => lineAt(timed, index, now))
    .filter((line): line is VisibleLine => line !== null);
  const { voice, stage } = reduceMotion ? { voice: 'agent' as Voice, stage: null } : voiceAt(timeline.steps, now);

  const replay = useCallback(() => {
    clockRef.current = 0;
    setT(0);
  }, []);

  return {
    call,
    callIndex: safeIndex,
    lines,
    voice,
    stage,
    elapsed: Math.min(now, timeline.total) / 1000,
    duration: timeline.total / 1000,
    ended: now >= timeline.total,
    replay,
  };
}
