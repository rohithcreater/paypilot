export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

export interface AgentLog {
  agent: string;
  task_id: string;
  status: string;
  result: { summary?: string; draft?: string; [key: string]: unknown };
  requires_human_approval: boolean;
  timestamp: string;
}

export interface Order {
  order_id: string;
  seller_id: string;
  amount: string;
  currency: string;
  status: string;
  captured_at: string | null;
  impact: { amount: string; currency: string; est_trees: number; project: string } | null;
}

export interface ChatResult {
  response: string;
  order_id: string | null;
  payment_url: string | null;
  plan: string[];
  agent_logs: AgentLog[];
  order: Order | null;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function newId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  }
}

// Demo auth: an anonymous id kept in this browser. For live use, switch the backend to
// AUTH_MODE=jwt and send the signed-in user's Supabase access token in an Authorization header.
export function getUserId(): string {
  try {
    let id = localStorage.getItem('paypilot_user');
    if (!id) {
      id = `user_${newId().replace(/-/g, '').slice(0, 12)}`;
      localStorage.setItem('paypilot_user', id);
    }
    return id;
  } catch {
    return 'user_anonymous';
  }
}

async function request<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError('Cannot reach the server. Check your connection and try again.', 0);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    throw new ApiError(typeof detail === 'string' ? detail : 'Please check your input and try again.', res.status);
  }
  return data as T;
}

export interface ChatInput {
  message: string;
  seller_id?: string;
  amount?: string;
  order_id?: string | null;
  request_id?: string;
  procurement_campaign_id?: string;
}

export const sendChat = (input: ChatInput) =>
  request<ChatResult>('/api/chat', { ...input, user_id: getUserId() });

export const captureOrder = (orderId: string) =>
  request<Order>(`/api/orders/${encodeURIComponent(orderId)}/capture`, { user_id: getUserId() });
