/**
 * Mock of the insights service (call analytics). Same gate as the documents
 * mock: bearer token, `tenant_admin` role, tenant taken from the token.
 * Unknown `days` / `synthetic` values get a 400.
 */
import { HttpResponse, http, type JsonBodyType } from 'msw';
import type { SyntheticMode } from '@/features/insights/types';
import { env } from '@/lib/env';
import {
  activityFixture,
  knowledgeFixture,
  reviewCallsFixture,
  sentimentFixture,
  summaryFixture,
  toolsFixture,
} from '../insightsFixtures';
import { authenticate, drfError, latency } from '../utils';

const insightsUrl = (path: string): string => `${env.insightsRoot}/insights/${path}`;

const DAYS = new Set([7, 30, 90]);
const SYNTHETIC = new Set<SyntheticMode>(['include', 'exclude', 'only']);
const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 100;

interface Params {
  tenantId: string;
  days: number;
  synthetic: SyntheticMode;
  limit: number;
}

function parseRequest(request: Request): Params | Response {
  const auth = authenticate(request);
  if ('response' in auth) return auth.response;
  const { user } = auth;
  if (user.role !== 'tenant_admin') return drfError(403, 'This account role cannot use the insights service.');
  if (user.tenant_id === null) return drfError(401, 'Token does not carry a valid tenant.');

  const url = new URL(request.url);
  const days = Number(url.searchParams.get('days') ?? 30);
  const synthetic = (url.searchParams.get('synthetic') ?? 'include') as SyntheticMode;
  if (!DAYS.has(days)) return drfError(400, 'days must be one of 7, 30, 90.');
  if (!SYNTHETIC.has(synthetic)) return drfError(400, 'synthetic must be one of include, exclude, only.');
  const limit = Number.parseInt(url.searchParams.get('limit') ?? '', 10);

  return {
    tenantId: user.tenant_id,
    days,
    synthetic,
    limit: Number.isFinite(limit) && limit > 0 ? Math.min(limit, MAX_LIMIT) : DEFAULT_LIMIT,
  };
}

function endpoint(path: string, build: (params: Params) => JsonBodyType) {
  return http.get(insightsUrl(path), async ({ request }) => {
    await latency();
    const params = parseRequest(request);
    if (params instanceof Response) return params;
    return HttpResponse.json(build(params));
  });
}

export const insightsHandlers = [
  endpoint('summary', summaryFixture),
  endpoint('sentiment', sentimentFixture),
  endpoint('tools', toolsFixture),
  endpoint('knowledge', knowledgeFixture),
  endpoint('activity', activityFixture),
  endpoint('calls', (params) => reviewCallsFixture(params, params.limit)),
];
