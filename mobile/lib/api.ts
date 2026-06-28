const BASE_URL = 'https://mighty-selfserve-production.up.railway.app';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ApiResult<T> {
  ok: boolean;
  status?: number;
  data?: T;
  error?: string;
}

export interface MeResponse {
  email: string;
  api_key: string;
}

export interface FieldEntry {
  label: string;
  value: string;
}

export interface AccountCard {
  source: string;
  display_name?: string;
  emoji?: string;
  last_synced_at?: string | null;
  top_fields?: FieldEntry[];
  sync_status?: 'ok' | 'login_required' | 'no_data' | 'error';
}

export interface LatestSyncResponse {
  last_sync?: string;
  last_sync_ok?: number;
  last_sync_failed?: number;
  syncing?: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function authHeaders(apiKey: string): Record<string, string> {
  return {
    'X-Mighty-Key': apiKey,
    'Content-Type': 'application/json',
  };
}

async function safeFetch<T>(
  url: string,
  options: RequestInit
): Promise<ApiResult<T>> {
  try {
    const resp = await fetch(url, { ...options, headers: { ...options.headers } });
    const status = resp.status;

    if (!resp.ok) {
      let error = `HTTP ${status}`;
      try {
        const body = await resp.json();
        if (body && body.error) error = body.error;
      } catch {}
      return { ok: false, status, error };
    }

    const data = await resp.json() as T;
    return { ok: true, status, data };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

// ── API Functions ─────────────────────────────────────────────────────────────

/**
 * Validate an API key by calling GET /api/me.
 * Returns ok:true with the user profile on success.
 */
export async function getMe(apiKey: string): Promise<ApiResult<MeResponse>> {
  return safeFetch<MeResponse>(`${BASE_URL}/api/me`, {
    method: 'GET',
    headers: authHeaders(apiKey),
  });
}

/**
 * Fetch all account cards for the dashboard.
 * Calls GET /api/account-data which returns the full account list with top fields.
 */
export async function getDashboardData(
  apiKey: string
): Promise<ApiResult<AccountCard[]>> {
  const result = await safeFetch<{ accounts?: AccountCard[] } | AccountCard[]>(
    `${BASE_URL}/api/account-data`,
    {
      method: 'GET',
      headers: authHeaders(apiKey),
    }
  );

  if (!result.ok) return { ok: false, status: result.status, error: result.error };

  // Normalise: the endpoint may return { accounts: [...] } or [...] directly
  let cards: AccountCard[] = [];
  if (Array.isArray(result.data)) {
    cards = result.data;
  } else if (result.data && Array.isArray((result.data as { accounts?: AccountCard[] }).accounts)) {
    cards = (result.data as { accounts: AccountCard[] }).accounts;
  }

  return { ok: true, status: result.status, data: cards };
}

/**
 * Push captured page text for a single account page.
 * Mirrors the Chrome extension's capture payload format.
 */
export async function pushCapture(
  apiKey: string,
  source: string,
  text: string,
  url: string
): Promise<ApiResult<{ status: string }>> {
  return safeFetch<{ status: string }>(`${BASE_URL}/api/extension/capture`, {
    method: 'POST',
    headers: authHeaders(apiKey),
    body: JSON.stringify({
      source,
      page_text: text,
      page_url: url,
      synced_at: new Date().toISOString(),
    }),
  });
}

/**
 * Fetch the latest sync summary (timing, counts, syncing flag).
 */
export async function getLatestSync(
  apiKey: string
): Promise<ApiResult<LatestSyncResponse>> {
  return safeFetch<LatestSyncResponse>(`${BASE_URL}/api/latest-sync`, {
    method: 'GET',
    headers: authHeaders(apiKey),
  });
}
