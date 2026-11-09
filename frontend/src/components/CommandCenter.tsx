'use client';

import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import type { LucideIcon } from 'lucide-react';
import {
  Activity, AlertTriangle, BarChart3, Brain, CheckCircle2, CreditCard, Leaf, Loader2,
  Scale, Send, ShieldCheck, Truck, XCircle, PackageSearch,
} from 'lucide-react';
import { AgentLog, ApiError, newId, sendChat } from '@/lib/api';

interface Message { role: 'user' | 'ai'; text: string; error?: boolean }

const AGENTS: Record<string, { label: string; Icon: LucideIcon }> = {
  orchestrator: { label: 'Orchestrator', Icon: Brain },
  trust_agent: { label: 'Trust', Icon: ShieldCheck },
  payment_agent: { label: 'Payment', Icon: CreditCard },
  impact_agent: { label: 'Impact', Icon: Leaf },
  delivery_agent: { label: 'Delivery', Icon: Truck },
  dispute_agent: { label: 'Dispute', Icon: Scale },
  intel_agent: { label: 'Insights', Icon: BarChart3 },
  collective_agent: { label: 'Collective Procurement', Icon: PackageSearch },
};

const ORDER_KEY = 'paypilot_order';

export default function CommandCenter() {
  const [input, setInput] = useState('');
  const [seller, setSeller] = useState('seller_abc');
  const [amount, setAmount] = useState('500');
  const [campaignId, setCampaignId] = useState('');
  const [messages, setMessages] = useState<Message[]>([
    { role: 'ai', text: 'Hi, I am PayPilot. Tell me what you want to buy, or ask about an order. Set the seller and amount below for purchases.' },
  ]);
  const [logs, setLogs] = useState<AgentLog[]>([]);
  const [paymentUrl, setPaymentUrl] = useState<string | null>(null);
  const [orderId, setOrderId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cancelled, setCancelled] = useState(false);

  useEffect(() => {
    try {
      setOrderId(sessionStorage.getItem(ORDER_KEY));
    } catch { /* storage unavailable */ }
    setCancelled(new URLSearchParams(window.location.search).get('cancelled') === '1');
  }, []);

  const remember = (id: string | null) => {
    setOrderId(id);
    try {
      if (id) sessionStorage.setItem(ORDER_KEY, id);
      else sessionStorage.removeItem(ORDER_KEY);
    } catch { /* ignore */ }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setMessages((m) => [...m, { role: 'user', text }]);
    setInput('');
    setLoading(true);
    setPaymentUrl(null);
    setCancelled(false);
    try {
      const res = await sendChat({
        message: text,
        seller_id: seller.trim() || undefined,
        amount: amount.trim() || undefined,
        order_id: orderId,
        request_id: newId(),
        procurement_campaign_id: campaignId.trim() || undefined,
      });
      setMessages((m) => [...m, { role: 'ai', text: res.response }]);
      setLogs(res.agent_logs);
      setPaymentUrl(res.payment_url);
      if (res.order_id) remember(res.order_id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) remember(null);
      setMessages((m) => [...m, { role: 'ai', error: true, text: e instanceof ApiError ? e.message : 'Something went wrong.' }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#080D1B] text-white p-4 md:p-6 flex flex-col lg:flex-row gap-6">
      <div className="flex-1 flex flex-col max-w-4xl mx-auto w-full">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl md:text-4xl font-bold bg-gradient-to-r from-[#7867FF] to-[#20C8E5] bg-clip-text text-transparent">
              PAYPILOT AI
            </h1>
            <p className="text-white/50 text-sm">6 core agents + optional impact · human-approved commerce</p>
          </div>
          <div className="px-3 py-1 rounded-full bg-white/10 border border-white/20 text-xs">Sandbox</div>
        </header>

        {cancelled && (
          <div className="mb-4 p-3 rounded-xl bg-yellow-500/10 border border-yellow-500/30 text-yellow-200 text-sm flex gap-2 items-center">
            <AlertTriangle size={16} /> You cancelled the PayPal checkout. Nothing was charged.
          </div>
        )}

        <div aria-live="polite" className="flex-1 min-h-[320px] max-h-[55vh] overflow-y-auto space-y-4 mb-4 p-4 md:p-6 rounded-3xl bg-white/5 border border-white/10">
          <AnimatePresence initial={false}>
            {messages.map((m, i) => (
              <motion.div key={i} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[85%] p-4 rounded-2xl whitespace-pre-wrap ${
                  m.role === 'user' ? 'bg-[#7867FF]' : m.error ? 'bg-red-500/15 border border-red-500/30' : 'bg-white/10 border border-white/10'
                }`}>{m.text}</div>
              </motion.div>
            ))}
          </AnimatePresence>
          {loading && (
            <div className="flex items-center gap-2 text-[#20C8E5] text-sm font-mono">
              <Loader2 size={14} className="animate-spin" /> Agents working...
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
          <label className="text-xs text-white/50">Seller ID
            <input value={seller} onChange={(e) => setSeller(e.target.value)} maxLength={100}
              className="mt-1 w-full bg-white/5 border border-white/10 rounded-xl p-3 text-white text-sm focus:outline-none focus:ring-2 ring-[#7867FF]" />
          </label>
          <label className="text-xs text-white/50">Amount (USD)
            <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" maxLength={12}
              className="mt-1 w-full bg-white/5 border border-white/10 rounded-xl p-3 text-white text-sm focus:outline-none focus:ring-2 ring-[#7867FF]" />
          </label>
          <label className="text-xs text-white/50">Procurement campaign ID (optional)
            <input value={campaignId} onChange={(e) => setCampaignId(e.target.value)} maxLength={64}
              placeholder="Paste a campaign ID to compare quotes"
              className="mt-1 w-full bg-white/5 border border-white/10 rounded-xl p-3 text-white text-sm focus:outline-none focus:ring-2 ring-[#7867FF]" />
          </label>
        </div>

        <div className="flex gap-3">
          <input value={input} onChange={(e) => setInput(e.target.value)} maxLength={1000}
            onKeyDown={(e) => e.key === 'Enter' && send()} aria-label="Command"
            placeholder="e.g. I want to buy a laptop from this seller and check they are trustworthy"
            className="flex-1 bg-white/5 border border-white/10 rounded-2xl p-4 focus:outline-none focus:ring-2 ring-[#7867FF]" />
          <button onClick={send} disabled={loading || !input.trim()}
            className="bg-[#7867FF] hover:bg-[#6655e0] disabled:opacity-50 px-6 rounded-2xl font-bold flex items-center gap-2">
            <Send size={18} /> Send
          </button>
        </div>

        {paymentUrl && (
          <motion.div initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
            className="mt-5 p-5 bg-green-500/10 border border-green-500/30 rounded-2xl flex flex-wrap gap-3 justify-between items-center">
            <div className="flex items-center gap-3">
              <CreditCard className="text-green-400" />
              <span>Your payment is ready. You approve it on PayPal; nothing is charged before that.</span>
            </div>
            <a href={paymentUrl} className="bg-green-500 text-[#080D1B] px-6 py-2 rounded-xl font-bold hover:bg-green-400">
              Review on PayPal
            </a>
          </motion.div>
        )}
      </div>

      <aside className="w-full lg:w-96 bg-white/5 border border-white/10 rounded-3xl p-5 self-start">
        <h2 className="text-xl font-bold mb-2 flex items-center gap-2">
          <Activity size={20} className="text-[#20C8E5]" /> Agent Pipeline
        </h2>
        <p className="text-xs text-white/40 mb-5">6 core agents + optional impact capability · coordinated by the master orchestrator</p>
        {logs.length === 0 ? (
          <p className="text-center text-white/30 py-10 italic text-sm">Awaiting command...</p>
        ) : (
          <ol className="space-y-5">
            {logs.map((log, i) => {
              const meta = AGENTS[log.agent] ?? { label: log.agent, Icon: Brain };
              const Status = log.status === 'completed' ? CheckCircle2 : log.status === 'failed' ? XCircle : AlertTriangle;
              const color = log.status === 'completed' ? 'text-green-400' : log.status === 'failed' ? 'text-red-400' : 'text-yellow-300';
              return (
                <li key={i} className="pl-4 border-l-2 border-white/10">
                  <div className="flex items-center gap-2 text-xs font-mono uppercase text-[#20C8E5] mb-1">
                    <meta.Icon size={12} /> {meta.label}
                    <Status size={12} className={color} />
                    {log.requires_human_approval && <span className="text-yellow-300 normal-case">needs your approval</span>}
                  </div>
                  <p className="text-sm text-white/80">{log.result.summary}</p>
                  {typeof log.result.draft === 'string' && (
                    <details className="mt-2 text-xs text-white/60">
                      <summary className="cursor-pointer">View draft</summary>
                      <pre className="mt-2 whitespace-pre-wrap bg-black/30 p-3 rounded-lg">{log.result.draft}</pre>
                    </details>
                  )}
                  {Array.isArray(log.result.ranked_quotes) && log.result.ranked_quotes.length > 0 && (
                    <details className="mt-3 text-xs text-white/70" open>
                      <summary className="cursor-pointer font-semibold">Supplier comparison · {log.result.ranked_quotes.length} quotes</summary>
                      <div className="mt-2 space-y-2">
                        {log.result.ranked_quotes.map((quote: any, quoteIndex: number) => (
                          <div key={quote.id ?? quoteIndex} className="rounded-xl bg-black/25 border border-white/10 p-3">
                            <div className="flex justify-between gap-2">
                              <span className="font-semibold text-white">{quote.supplier_id}</span>
                              <span className="text-[#20C8E5]">Score {quote.decision_score}</span>
                            </div>
                            <p className="mt-1">Landed unit price: {quote.currency} {quote.landed_unit_price} · {quote.lead_days} days</p>
                            {typeof quote.counter_offer_draft === 'string' && (
                              <details className="mt-2">
                                <summary className="cursor-pointer text-yellow-200">Counter-offer draft · not sent</summary>
                                <p className="mt-1 whitespace-pre-wrap text-white/70">{quote.counter_offer_draft}</p>
                              </details>
                            )}
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </aside>
    </div>
  );
}
