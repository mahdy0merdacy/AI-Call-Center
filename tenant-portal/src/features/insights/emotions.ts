import type { BadgeProps } from '@/components/ui/badge';
import { messages } from '@/i18n';
import { EMOTIONS, type Emotion } from './types';

const isEmotion = (value: string): value is Emotion => (EMOTIONS as readonly string[]).includes(value);

/** Translated name; an emotion the portal doesn't know yet is shown as sent. */
export function emotionLabel(emotion: string): string {
  return isEmotion(emotion) ? messages.insights.emotions[emotion] : emotion;
}

const BAR_CLASS: Record<Emotion, string> = {
  neutral: 'bg-muted-foreground/60',
  satisfied: 'bg-success',
  confused: 'bg-info',
  frustrated: 'bg-warning',
  angry: 'bg-destructive',
  anxious: 'bg-brand-amber',
  sad: 'bg-info/60',
};

export function emotionBarClass(emotion: string): string {
  return isEmotion(emotion) ? BAR_CLASS[emotion] : 'bg-muted-foreground/40';
}

const BADGE_VARIANT: Record<Emotion, NonNullable<BadgeProps['variant']>> = {
  neutral: 'neutral',
  satisfied: 'success',
  confused: 'info',
  frustrated: 'warning',
  angry: 'destructive',
  anxious: 'warning',
  sad: 'info',
};

export function emotionBadgeVariant(emotion: string): NonNullable<BadgeProps['variant']> {
  return isEmotion(emotion) ? BADGE_VARIANT[emotion] : 'neutral';
}
