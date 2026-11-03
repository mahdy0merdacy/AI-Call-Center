import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { ExternalLink, Mail, RotateCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { CountdownRing } from '@/components/ui/CountdownRing';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EnvelopeIllustration } from '@/components/ui/EnvelopeIllustration';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { messages } from '@/i18n';
import { toApiError, userMessage } from '@/lib/errors';
import { formatClock } from '@/lib/hooks/useCountdown';
import { RESEND_COOLDOWN_SECONDS, useCooldown } from '@/lib/hooks/useCooldown';
import { mailProviderFor, maskEmail } from '@/lib/mask';
import { resendVerificationRequest } from '../api/registration';
import { emailOnlySchema, type EmailOnlyValues } from '../schemas';

const t = messages.verifyModal;

interface VerifyEmailModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Known address (just registered, or typed on the login form). Asked for when missing. */
  email?: string | null;
  /**
   * "Use a different email". When provided (registration) the caller handles it,
   * e.g. by going back to the form; otherwise the modal asks for the new address.
   */
  onChangeEmail?: () => void;
}

/** Cooldown key shared with callers that just sent an email (see `startCooldown`). */
export const verifyCooldownKey = (email: string) => `verify:${email.toLowerCase()}`;

export function VerifyEmailModal({ open, onOpenChange, email, onChangeEmail }: VerifyEmailModalProps) {
  const [currentEmail, setCurrentEmail] = useState<string | null>(email ?? null);
  const [editing, setEditing] = useState(!email);
  const [status, setStatus] = useState<{ tone: 'success' | 'destructive'; text: string } | null>(null);
  const cooldown = useCooldown(verifyCooldownKey(currentEmail ?? ''));

  const form = useForm<EmailOnlyValues>({
    resolver: zodResolver(emailOnlySchema),
    defaultValues: { email: email ?? '' },
  });

  // Each time the modal opens, start from the caller's address.
  useEffect(() => {
    if (!open) return;
    setCurrentEmail(email ?? null);
    setEditing(!email);
    setStatus(null);
    form.reset({ email: email ?? '' });
  }, [open, email, form]);

  const resend = useMutation({ mutationFn: resendVerificationRequest });

  const send = async (address: string) => {
    setStatus(null);
    try {
      await resend.mutateAsync(address);
      cooldown.start(RESEND_COOLDOWN_SECONDS);
      setStatus({ tone: 'success', text: t.sent });
    } catch (error) {
      const apiError = toApiError(error);
      if (apiError.kind === 'rate_limited') cooldown.start(apiError.retryAfterSeconds ?? RESEND_COOLDOWN_SECONDS);
      setStatus({ tone: 'destructive', text: userMessage(apiError) });
    }
  };

  const onSubmitEmail = form.handleSubmit(async ({ email: address }) => {
    setCurrentEmail(address);
    setEditing(false);
    await send(address);
  });

  const provider = currentEmail ? mailProviderFor(currentEmail) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby="verify-modal-description">
        <div className="flex justify-center pb-2 pt-1">
          <EnvelopeIllustration />
        </div>
        <DialogHeader>
          <DialogTitle>{t.title}</DialogTitle>
          <DialogDescription id="verify-modal-description">
            {editing || !currentEmail ? (
              t.askEmail
            ) : (
              t.body(maskEmail(currentEmail))
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="mt-6 space-y-4">
          <AnimatePresence initial={false}>
            {status && <Alert key={status.text} variant={status.tone} title={status.text} />}
          </AnimatePresence>

          <AnimatePresence mode="wait" initial={false}>
            {editing || !currentEmail ? (
              <motion.form
                key="edit"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                onSubmit={onSubmitEmail}
                noValidate
                className="space-y-4"
              >
                <Field id="verify-modal-email" label={messages.fields.email} error={form.formState.errors.email?.message}>
                  {(control) => (
                    <Input
                      {...control}
                      {...form.register('email')}
                      type="email"
                      autoComplete="email"
                      placeholder={messages.fields.emailPlaceholder}
                      autoFocus
                    />
                  )}
                </Field>
                <Button type="submit" block loading={resend.isPending} loadingText={t.sending}>
                  <Mail className="size-4" aria-hidden />
                  {t.send}
                </Button>
              </motion.form>
            ) : (
              <motion.div
                key="resend"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                className="space-y-3"
              >
                <Button
                  block
                  variant={cooldown.active ? 'outline' : 'default'}
                  disabled={cooldown.active}
                  loading={resend.isPending}
                  loadingText={t.sending}
                  onClick={() => void send(currentEmail)}
                >
                  {cooldown.active && cooldown.endsAt !== null ? (
                    <>
                      <CountdownRing
                        endsAt={cooldown.endsAt}
                        totalSeconds={RESEND_COOLDOWN_SECONDS}
                        size={18}
                        showLabel={false}
                      />
                      {t.resendIn(formatClock(cooldown.secondsLeft))}
                    </>
                  ) : (
                    <>
                      <RotateCw className="size-4" aria-hidden />
                      {t.resend}
                    </>
                  )}
                </Button>
                {provider && (
                  <Button asChild block variant="outline">
                    <a href={provider.url} target="_blank" rel="noreferrer noopener">
                      {t.openMail(provider.name)}
                      <ExternalLink className="size-4" aria-hidden />
                    </a>
                  </Button>
                )}
                <button
                  type="button"
                  onClick={() => (onChangeEmail ? onChangeEmail() : setEditing(true))}
                  className="w-full rounded text-center text-sm font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  {t.changeEmail}
                </button>
              </motion.div>
            )}
          </AnimatePresence>

          <p className="text-center text-[13px] text-muted-foreground">{t.mailHint}</p>
        </div>
      </DialogContent>
    </Dialog>
  );
}
