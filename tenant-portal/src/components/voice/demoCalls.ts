/**
 * Sample calls replayed on the call console. They follow the demo tenants' real
 * processes (`tenants/*.yaml`) and fixture data (`tenants/fixtures/`): Acme's
 * enforced shipment ID, SwiftNet's outage branch, Maison Verte answering in
 * French from its documents. The words are spoken in the call's own language,
 * whatever the portal's language; only the console's labels are translated.
 */

export type EventTag = 'intent' | 'slot' | 'tool' | 'knowledge' | 'rule' | 'end';

export type CallStep =
  /** Caller speech, streamed word by word like speech-to-text partials. */
  | {
      kind: 'caller';
      text: string;
      /** A word the recogniser first gets wrong, then revises with more context. */
      mishear?: { word: number; as: string };
    }
  /** Agent reply, typed out as it is spoken. `think` is the pause before it (ms). */
  | { kind: 'agent'; text: string; think?: number }
  /** A pipeline step: technical, language neutral. */
  | { kind: 'event'; tag: EventTag; text: string; ms?: number };

export interface DemoCall {
  slug: string;
  tenant: string;
  locale: 'en-US' | 'fr-FR';
  /** TTS speaker from the tenant's config. */
  speaker: string;
  /** Sample per-stage latencies shown under the transcript (ms). */
  latency: { stt: number; llm: number; tts: number };
  steps: CallStep[];
}

export const demoCalls: DemoCall[] = [
  {
    slug: 'acme',
    tenant: 'Acme Delivery',
    locale: 'en-US',
    speaker: 'Jason',
    latency: { stt: 180, llm: 410, tts: 290 },
    steps: [
      { kind: 'agent', text: 'Acme Delivery, this is the automated assistant. How can I help?', think: 300 },
      {
        kind: 'caller',
        text: 'Hi, where is my parcel? It was due yesterday.',
        mishear: { word: 1, as: 'wear' },
      },
      { kind: 'event', tag: 'intent', text: 'track_shipment · 0.94' },
      {
        kind: 'agent',
        text: 'I can check that. Can I take your shipment ID? It’s ten characters, on your confirmation email.',
      },
      { kind: 'caller', text: 'Sure, it’s AC1199Z044.' },
      { kind: 'event', tag: 'slot', text: 'shipment_id = AC1199Z044 ✓' },
      { kind: 'event', tag: 'tool', text: 'lookup_shipment → delayed', ms: 182 },
      {
        kind: 'agent',
        text: 'Sorry about that. It’s delayed by weather on the Marseille route, and the new estimate is the 2nd of October. Would another day suit you better?',
      },
      { kind: 'caller', text: 'No, the 2nd is fine. Thanks.' },
      { kind: 'agent', text: 'You’re welcome. Have a good day.', think: 380 },
      { kind: 'event', tag: 'end', text: '' },
    ],
  },
  {
    slug: 'maisonverte',
    tenant: 'Maison Verte',
    locale: 'fr-FR',
    speaker: 'Sofia',
    latency: { stt: 170, llm: 380, tts: 270 },
    steps: [
      { kind: 'agent', text: 'Maison Verte, bonjour. Comment puis-je vous aider ?', think: 300 },
      {
        kind: 'caller',
        text: 'Bonjour, tous les combien faut-il arroser un monstera ?',
        mishear: { word: 5, as: 'arrêter' },
      },
      { kind: 'event', tag: 'knowledge', text: 'maison-verte-produits.md · Monstera' },
      {
        kind: 'agent',
        text: 'Quand les trois premiers centimètres du terreau sont secs : environ une fois par semaine en été, toutes les deux semaines en hiver.',
      },
      { kind: 'caller', text: 'Parfait. Et vous livrez sur Nantes ?' },
      { kind: 'event', tag: 'knowledge', text: 'maison-verte-infos.md · Livraison' },
      {
        kind: 'agent',
        text: 'Oui, sous 48 heures pour 6 €, et c’est gratuit dès 80 € d’achat.',
      },
      { kind: 'caller', text: 'Super, merci beaucoup !' },
      { kind: 'agent', text: 'Avec plaisir, bonne journée !', think: 380 },
      { kind: 'event', tag: 'end', text: '' },
    ],
  },
  {
    slug: 'swiftnet',
    tenant: 'SwiftNet Broadband',
    locale: 'en-US',
    speaker: 'Aria',
    latency: { stt: 190, llm: 440, tts: 300 },
    steps: [
      {
        kind: 'agent',
        text: 'SwiftNet support, you’re through to the automated assistant. What’s the problem?',
        think: 300,
      },
      { kind: 'caller', text: 'My internet has been down all morning and I work from home.' },
      { kind: 'event', tag: 'intent', text: 'report_fault · 0.91' },
      {
        kind: 'agent',
        text: 'That’s really frustrating, I’m sorry. What’s the landline number on the account?',
      },
      { kind: 'caller', text: 'It’s 02 98 44 51 02.', mishear: { word: 2, as: '90' } },
      { kind: 'event', tag: 'slot', text: 'phone_number = 0298445102 ✓' },
      { kind: 'event', tag: 'tool', text: 'check_line_status → area_outage', ms: 236 },
      { kind: 'event', tag: 'rule', text: 'outage → skip troubleshooting' },
      {
        kind: 'agent',
        text: 'There’s a known outage in Nantes centre, so it isn’t your equipment. Engineers expect it back by 6 pm today, and your reference is OUT-4471.',
      },
      { kind: 'caller', text: 'Okay, good to know it’s not my router.' },
      { kind: 'agent', text: 'Not at all. Sorry again for the trouble.', think: 380 },
      { kind: 'event', tag: 'end', text: '' },
    ],
  },
];

/** The tenant's own sample call when there is one, otherwise every demo call in turn. */
export function callsForTenant(slug: string | null | undefined): DemoCall[] {
  const own = demoCalls.filter((call) => call.slug === slug);
  return own.length > 0 ? own : demoCalls;
}
