import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowLeft, ExternalLink, RotateCw, Send } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link } from 'react-router-dom';
import { paths } from '@/app/paths';
import { AuthLayout } from '@/components/layout/AuthLayout';
import { StaggerItem, useShake } from '@/components/motion';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { CountdownRing } from '@/components/ui/CountdownRing';
import { EnvelopeIllustration } from '@/components/ui/EnvelopeIllustration';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { messages } from '@/i18n';
import { toApiError, userMessage } from '@/lib/errors';
import { formatClock } from '@/lib/hooks/useCountdown';
import { RESEND_COOLDOWN_SECONDS, startCooldown, useCooldown } from '@/lib/hooks/useCooldown';
import { mailProviderFor, maskEmail } from '@/lib/mask';
import { requestPasswordReset } from '../api/passwordReset';
import { LockoutAlert } from '../components/LockoutAlert';
import { emailOnlySchema, type EmailOnlyValues } from '../schemas';

const t = messages.forgotPassword;
const cooldownKey = (email: string) => `reset:${email}`;

type Problem =
  | { kind: 'throttled'; until: number | null; totalSeconds: number }
  | { kind: 'message'; text: string };

function toProblem(error: unknown): Problem {
  const apiError = toApiError(error);
  if (apiError.kind === 'rate_limited') {
    const seconds = apiError.retryAfterSeconds;
    return { kind: 'throttled', until: seconds ? Date.now() + seconds * 1000 : null, totalSeconds: seconds ?? 0 };
  }
  return { kind: 'message', text: userMessage(apiError) };
}

function ProblemAlert({ problem, onExpire }: { problem: Problem | null; onExpire: () => void }) {
  return (
    <AnimatePresence mode="popLayout">
      {problem?.kind === 'throttled' && (
        <LockoutAlert
          key="throttled"
          title={t.throttledTitle}
          body={messages.errors.rateLimited}
          until={problem.until}
          totalSeconds={problem.totalSeconds}
          onExpire={onExpire}
        />
      )}
      {problem?.kind === 'message' && <Alert key="message" variant="destructive" title={problem.text} />}
    </AnimatePresence>
  );
}

/** Neutral confirmation: identical whether or not the account exists. */
function SentView({ email, onChangeEmail }: { email: string; onChangeEmail: () => void }) {
  const cooldown = useCooldown(cooldownKey(email));
  const [problem, setProblem] = useState<Problem | null>(null);
  const [resent, setResent] = useState(false);
  const resend = useMutation({ mutationFn: requestPasswordReset });
  const provider = mailProviderFor(email);

  const sendAgain = async () => {
    setProblem(null);
    setResent(false);
    try {
      await resend.mutateAsync(email);
      cooldown.start();
      setResent(true);
    } catch (error) {
      const next = toProblem(error);
      if (next.kind === 'throttled' && next.totalSeconds) cooldown.start(next.totalSeconds);
      setProblem(next);
    }
  };

  return (
    <AuthLayout
      icon={<EnvelopeIllustration className="h-20 w-24" />}
      title={t.sentTitle}
      description={t.sentBody(maskEmail(email))}
    >
      <StaggerItem className="space-y-3">
        <div className="space-y-3 empty:hidden">
          <ProblemAlert problem={problem} onExpire={() => setProblem(null)} />
          <AnimatePresence>{resent && <Alert key="resent" variant="success" title={t.resent} />}</AnimatePresence>
        </div>
        <Button
          block
          size="lg"
          variant={cooldown.active ? 'outline' : 'default'}
          disabled={cooldown.active}
          loading={resend.isPending}
          loadingText={t.submitting}
          onClick={() => void sendAgain()}
        >
          {cooldown.active && cooldown.endsAt !== null ? (
            <>
              <CountdownRing endsAt={cooldown.endsAt} totalSeconds={RESEND_COOLDOWN_SECONDS} size={18} showLabel={false} />
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
          <Button asChild block size="lg" variant="outline">
            <a href={provider.url} target="_blank" rel="noreferrer noopener">
              {messages.verifyModal.openMail(provider.name)}
              <ExternalLink className="size-4" aria-hidden />
            </a>
          </Button>
        )}
        <div className="flex flex-col items-center gap-2 pt-2 text-sm">
          <button
            type="button"
            onClick={onChangeEmail}
            className="rounded font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            {t.differentEmail}
          </button>
          <Link to={paths.login} className="text-muted-foreground hover:text-foreground">
            {t.backToLogin}
          </Link>
        </div>
        <p className="pt-2 text-center text-[13px] text-muted-foreground">{messages.verifyModal.mailHint}</p>
      </StaggerItem>
    </AuthLayout>
  );
}

export function ForgotPasswordPage() {
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const { controls, shake } = useShake();

  const {
    register,
    handleSubmit,
    setFocus,
    formState: { errors },
  } = useForm<EmailOnlyValues>({ resolver: zodResolver(emailOnlySchema), defaultValues: { email: '' } });

  const request = useMutation({ mutationFn: requestPasswordReset });

  const onSubmit = handleSubmit(
    async ({ email }) => {
      setProblem(null);
      try {
        await request.mutateAsync(email);
        startCooldown(cooldownKey(email));
        setSentTo(email);
      } catch (error) {
        shake();
        setProblem(toProblem(error));
      }
    },
    () => shake(),
  );

  if (sentTo) {
    return (
      <SentView
        email={sentTo}
        onChangeEmail={() => {
          setSentTo(null);
          window.setTimeout(() => setFocus('email'), 350);
        }}
      />
    );
  }

  const throttled = problem?.kind === 'throttled' && problem.until !== null;

  return (
    <AuthLayout
      title={t.title}
      description={t.description}
      footer={
        <Link to={paths.login} className="inline-flex items-center gap-1.5 font-medium text-primary hover:underline">
          <ArrowLeft className="size-4" aria-hidden />
          {t.backToLogin}
        </Link>
      }
    >
      <motion.div animate={controls} className="space-y-5">
        <div className="space-y-3 empty:hidden">
          <ProblemAlert problem={problem} onExpire={() => setProblem(null)} />
        </div>
        <form onSubmit={onSubmit} noValidate className="space-y-5" aria-labelledby="page-title">
          <StaggerItem>
            <Field id="forgot-email" label={messages.fields.email} error={errors.email?.message}>
              {(control) => (
                <Input
                  {...control}
                  {...register('email')}
                  type="email"
                  autoComplete="email"
                  inputMode="email"
                  placeholder={messages.fields.emailPlaceholder}
                  autoFocus
                />
              )}
            </Field>
          </StaggerItem>
          <StaggerItem>
            <Button type="submit" block size="lg" loading={request.isPending} loadingText={t.submitting} disabled={throttled}>
              <Send className="size-4" aria-hidden />
              {t.submit}
            </Button>
          </StaggerItem>
          <StaggerItem>
            <p className="text-[13px] leading-relaxed text-muted-foreground">{t.googleHint}</p>
          </StaggerItem>
        </form>
      </motion.div>
    </AuthLayout>
  );
}
