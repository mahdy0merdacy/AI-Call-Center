import { RotateCcw } from 'lucide-react';
import { Fragment } from 'react';
import { WaveformVisual } from '@/components/ui/WaveformVisual';
import { messages } from '@/i18n';
import { cn } from '@/lib/utils';
import type { DemoCall } from './demoCalls';
import { useCallPlayback, type Stage, type VisibleLine } from './useCallPlayback';

const t = messages.voice;

export function formatClock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
}

export function LiveDot({ active = true, className }: { active?: boolean; className?: string }) {
  return (
    <span className={cn('relative inline-flex size-2 shrink-0', className)} aria-hidden>
      {active && (
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-brand-phosphor opacity-60" />
      )}
      <span
        className={cn(
          'relative inline-flex size-2 rounded-full transition-colors',
          active ? 'bg-brand-phosphor' : 'bg-brand-cream/35',
        )}
      />
    </span>
  );
}

function ThinkingDots() {
  return (
    <span className="inline-flex items-center gap-2 pt-1 font-mono text-[11px] uppercase tracking-[0.14em] text-voice-thinking">
      <span className="inline-flex gap-1" aria-hidden>
        {[0, 160, 320].map((delay) => (
          <span
            key={delay}
            className="size-1.5 animate-pulse rounded-full bg-voice-thinking"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </span>
      {t.thinking}
    </span>
  );
}

const ROW = 'grid grid-cols-[3.75rem_minmax(0,1fr)] gap-x-3 sm:grid-cols-[4.5rem_minmax(0,1fr)]';
const LABEL = 'pt-[0.3rem] font-mono text-[10px] font-medium uppercase tracking-[0.16em] sm:text-[10.5px]';

function TranscriptLine({ line, duration }: { line: VisibleLine; duration: number }) {
  if (line.kind === 'caller') {
    return (
      <div className={cn(ROW, 'animate-rise-in')}>
        <span className={cn(LABEL, 'text-voice-caller/55')}>{t.caller}</span>
        <p className="text-[14.5px] leading-relaxed text-voice-caller sm:text-[15px]">
          {line.words.map((word, index) => (
            <Fragment key={index}>
              {index > 0 && ' '}
              <span className={index >= line.partialFrom ? 'asr-partial' : undefined}>{word}</span>
            </Fragment>
          ))}
        </p>
      </div>
    );
  }

  if (line.kind === 'agent') {
    return (
      <div className={cn(ROW, 'animate-rise-in')}>
        <span className={cn(LABEL, 'text-voice-agent')}>{t.agent}</span>
        {line.thinking ? (
          <ThinkingDots />
        ) : (
          <p className="font-serif text-[18px] leading-snug text-[hsl(40_40%_96%)] sm:text-[19.5px]">
            {line.text}
            {line.typing && <span className="type-caret text-voice-agent" />}
          </p>
        )}
      </div>
    );
  }

  const { step } = line;
  if (step.tag === 'end') {
    return (
      <div className="flex animate-rise-in items-center gap-3 pt-1 font-mono text-[10.5px] uppercase tracking-[0.14em] text-brand-phosphor/90">
        <span className="h-px flex-1 bg-brand-phosphor/25" />
        {t.ended(formatClock(duration))}
        <span className="h-px flex-1 bg-brand-phosphor/25" />
      </div>
    );
  }

  return (
    <div className={cn(ROW, 'animate-rise-in')}>
      <span aria-hidden className="pt-[0.2rem] text-right font-mono text-[11px] text-brand-cream/25">
        ↳
      </span>
      <div className="flex min-w-0 items-center gap-2 font-mono text-[11px] text-brand-cream/60 sm:text-[11.5px]">
        <span className="shrink-0 rounded-[4px] border border-brand-amber/30 bg-brand-amber/10 px-1.5 py-px text-[10px] uppercase tracking-wider text-brand-amber">
          {t.events[step.tag]}
        </span>
        <span className="truncate">{step.text}</span>
        {step.ms !== undefined && (
          <span className="ml-auto shrink-0 tabular-nums text-brand-cream/40">{step.ms} ms</span>
        )}
      </div>
    </div>
  );
}

const STAGES: { id: Exclude<Stage, null>; code: string; dot: string }[] = [
  { id: 'stt', code: 'STT', dot: 'bg-voice-caller' },
  { id: 'llm', code: 'LLM', dot: 'bg-voice-thinking' },
  { id: 'tts', code: 'TTS', dot: 'bg-voice-agent' },
];

interface CallConsoleProps {
  calls: DemoCall[];
  className?: string;
  /** Height of the transcript; pass `min-h-0 flex-1` to fill a flex parent. */
  transcriptClassName?: string;
  /** Shows a replay button in the header. */
  controls?: boolean;
  bars?: number;
}

/**
 * A sample call replayed live: the caller's words stream in like speech-to-text
 * partials, pipeline steps (intent, slots, tools, documents) tick by, and the
 * agent's reply types out while the waveform follows whoever is speaking.
 */
export function CallConsole({
  calls,
  className,
  transcriptClassName,
  controls = false,
  bars = 64,
}: CallConsoleProps) {
  const playback = useCallPlayback(calls);
  const { call, lines, voice, stage, elapsed, duration, ended } = playback;

  return (
    <section
      aria-label={t.transcriptTitle(call.tenant)}
      className={cn('console-panel relative flex flex-col overflow-hidden rounded-2xl text-brand-cream', className)}
    >
      {/* Screen readers get the whole call at once instead of a typing animation. */}
      <ol className="sr-only" lang={call.locale}>
        {call.steps.map((step, index) =>
          step.kind === 'event' ? null : (
            <li key={index}>
              {step.kind === 'caller' ? t.caller : t.agent}: {step.text}
            </li>
          ),
        )}
      </ol>

      <div aria-hidden className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between gap-3 border-b border-brand-cream/10 px-4 py-3 font-mono text-[10.5px] uppercase tracking-[0.14em] sm:text-[11px]">
          <div className="flex min-w-0 items-center gap-2.5">
            <LiveDot active={!ended} />
            <span className="shrink-0 text-brand-cream/90">{ended ? t.sample : t.live}</span>
            <span className="text-brand-cream/25">/</span>
            <span className="truncate text-brand-cream/65">{call.tenant}</span>
            <span className="hidden shrink-0 text-brand-cream/35 md:inline">
              {call.locale} · {t.speaker(call.speaker)}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            {calls.length > 1 && (
              <span className="flex gap-1">
                {calls.map((item, index) => (
                  <span
                    key={item.slug}
                    className={cn(
                      'h-1 rounded-full transition-all duration-500',
                      index === playback.callIndex ? 'w-4 bg-brand-cream/80' : 'w-1 bg-brand-cream/25',
                    )}
                  />
                ))}
              </span>
            )}
            <span className="tabular-nums text-brand-cream/75">{formatClock(elapsed)}</span>
          </div>
        </div>

        <div className="relative h-16 shrink-0 px-4 pt-3 sm:h-20">
          <WaveformVisual voice={voice} bars={bars} />
        </div>

        <div
          lang={call.locale}
          className={cn(
            'fade-top flex h-[280px] flex-col justify-end gap-3 overflow-hidden px-4 pb-4 pt-2',
            transcriptClassName,
          )}
        >
          {lines.map((line) => (
            <TranscriptLine key={`${call.slug}-${line.key}`} line={line} duration={duration} />
          ))}
        </div>

        <div className="grid shrink-0 grid-cols-3 divide-x divide-brand-cream/10 border-t border-brand-cream/10 font-mono text-[10px] uppercase tracking-[0.12em] sm:text-[10.5px]">
          {STAGES.map(({ id, code, dot }) => {
            const active = stage === id;
            return (
              <div
                key={id}
                className={cn(
                  'flex items-center justify-between gap-2 px-3 py-2.5 transition-colors duration-300',
                  active ? 'bg-brand-cream/[0.07] text-brand-cream' : 'text-brand-cream/40',
                )}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    className={cn(
                      'size-1.5 shrink-0 rounded-full transition-all duration-300',
                      active ? cn(dot, 'shadow-[0_0_10px_currentColor]') : 'bg-brand-cream/20',
                    )}
                  />
                  <span className="font-semibold">{code}</span>
                  <span className="hidden truncate normal-case tracking-normal xl:inline">{t.stages[id]}</span>
                </span>
                <span className="tabular-nums normal-case">{call.latency[id]} ms</span>
              </div>
            );
          })}
        </div>
      </div>

      {controls && (
        <button
          type="button"
          onClick={playback.replay}
          className="absolute bottom-12 right-3 inline-flex size-8 items-center justify-center rounded-full border border-brand-cream/15 bg-brand-ink/70 text-brand-cream/70 backdrop-blur transition-colors hover:bg-brand-cream/10 hover:text-brand-cream focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-signal"
          aria-label={t.replay}
          title={t.replay}
        >
          <RotateCcw className="size-3.5" aria-hidden />
        </button>
      )}
    </section>
  );
}

/** One-line version for small screens: the waveform and the latest words only. */
export function CallTicker({ calls, className }: { calls: DemoCall[]; className?: string }) {
  const { call, lines, voice } = useCallPlayback(calls);
  const latest = [...lines].reverse().find((line) => line.kind !== 'event');

  return (
    <div aria-hidden className={cn('space-y-2', className)}>
      <div className="h-9">
        <WaveformVisual voice={voice} bars={40} intensity={0.9} />
      </div>
      <div lang={call.locale} className="flex h-[2.6rem] flex-col justify-end overflow-hidden">
        {latest?.kind === 'caller' && (
          <p className="text-[13px] leading-snug text-voice-caller">
            <span className="mr-2 font-mono text-[10px] uppercase tracking-[0.14em] text-voice-caller/55">
              {t.caller}
            </span>
            {latest.words.join(' ')}
          </p>
        )}
        {latest?.kind === 'agent' && (
          <p className="text-[15px] leading-snug text-[hsl(40_40%_96%)]">
            <span className="mr-2 font-mono text-[10px] uppercase tracking-[0.14em] text-voice-agent">
              {t.agent}
            </span>
            {latest.thinking ? (
              <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-voice-thinking">
                {t.thinking}…
              </span>
            ) : (
              <span className="font-serif">
                {latest.text}
                {latest.typing && <span className="type-caret text-voice-agent" />}
              </span>
            )}
          </p>
        )}
      </div>
    </div>
  );
}
