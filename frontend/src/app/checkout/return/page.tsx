'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { CheckCircle2, Clock, Loader2, XCircle } from 'lucide-react';
import { ApiError, Order, captureOrder } from '@/lib/api';

type View =
  | { kind: 'loading' }
  | { kind: 'done'; order: Order }
  | { kind: 'error'; message: string };

function Return() {
  const token = useSearchParams().get('token');
  const [view, setView] = useState<View>({ kind: 'loading' });
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!token) {
      setView({ kind: 'error', message: 'This page needs a PayPal order. Start a purchase from the Command Center.' });
      return;
    }
    captureOrder(token)
      .then((order) => {
        try { sessionStorage.setItem('paypilot_order', order.order_id); } catch { /* ignore */ }
        setView({ kind: 'done', order });
      })
      .catch((e) => setView({ kind: 'error', message: e instanceof ApiError ? e.message : 'Something went wrong.' }));
  }, [token]);

  return (
    <main className="min-h-screen bg-[#080D1B] text-white flex items-center justify-center p-6">
      <div className="max-w-md w-full bg-white/5 border border-white/10 rounded-3xl p-8 text-center space-y-4">
        {view.kind === 'loading' && (
          <>
            <Loader2 className="mx-auto animate-spin text-[#20C8E5]" size={40} />
            <p>Confirming your payment with PayPal...</p>
          </>
        )}
        {view.kind === 'done' && (
          <>
            {view.order.status === 'CAPTURED'
              ? <CheckCircle2 className="mx-auto text-green-400" size={44} />
              : <Clock className="mx-auto text-yellow-300" size={44} />}
            <h1 className="text-2xl font-bold">
              {view.order.status === 'CAPTURED' ? 'Payment complete' : 'Payment is processing'}
            </h1>
            <p className="text-white/70">
              {view.order.currency} {view.order.amount} to {view.order.seller_id}
              {view.order.status !== 'CAPTURED' && ` (status: ${view.order.status.replace(/_/g, ' ').toLowerCase()})`}
            </p>
            {view.order.impact && (
              <p className="text-sm text-green-300">
                {view.order.impact.currency} {view.order.impact.amount} pledged to {view.order.impact.project}
                {' '}(about {view.order.impact.est_trees} trees).
              </p>
            )}
          </>
        )}
        {view.kind === 'error' && (
          <>
            <XCircle className="mx-auto text-red-400" size={44} />
            <h1 className="text-2xl font-bold">We could not confirm the payment</h1>
            <p className="text-white/70">{view.message}</p>
          </>
        )}
        <a href="/" className="inline-block mt-2 bg-[#7867FF] hover:bg-[#6655e0] px-6 py-3 rounded-xl font-bold">
          Back to Command Center
        </a>
      </div>
    </main>
  );
}

export default function ReturnPage() {
  return (
    <Suspense fallback={null}>
      <Return />
    </Suspense>
  );
}
