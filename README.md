# PayPilot AI — Group buying with accountable AI

**PayPilot helps small businesses buy together without giving up control of their money.** A campaign owner gathers buyer commitments, suppliers submit quotes, and PayPilot compares the full landed price and lead time. The owner chooses an offer; each buyer then checks out and approves their own payment through PayPal.

The AI coordinates the work around that transaction: it can assess available seller signals, prepare a dispute response from recorded order evidence, explain order status, summarize merchant sales, compare supplier quotes, and report an impact ledger when asked. It does not award suppliers, invent evidence, or approve payments for users.

## Why this is useful

Small buyers often pay more because they cannot negotiate as a group. PayPilot turns that problem into a transparent workflow: buyers state their quantity and price ceiling, suppliers state price and delivery terms, and the campaign owner sees the trade-offs before selecting an offer. Every participant can review the terms and approve their own PayPal checkout.

## A 90-second judge demo

1. Create a campaign for a shared purchase, such as laptop accessories.
2. Add several buyers with different quantities and maximum prices.
3. Submit two supplier quotes with different unit prices, shipping costs, and lead times.
4. Ask PayPilot to compare the offers. Show the landed-price calculation, ranking basis, and an optional counter-offer draft.
5. Have the owner explicitly award one quote. Then have a buyer open their own PayPal sandbox checkout and approve it.
6. Show the order moving to captured only after PayPal confirms payment. The other buyers must approve their own checkouts separately.

This demo emphasizes PayPal as the actual payment rail and AI as decision support around a real buyer problem.

## What is implemented

- **Collective procurement:** campaign creation, buyer commitments, supplier quotes, transparent quote ranking, owner-approved awards, and individual buyer checkout.
- **PayPal checkout:** order creation, buyer approval, capture, webhook processing, idempotency, and payment-state reconciliation.
- **Seller trust:** a score calculated from available order history and dispute signals, with the factors shown in the response.
- **Delivery support:** order status and tracking based on recorded order data; demo tracking is labeled as demo data.
- **Dispute copilot:** a draft response assembled from order and tracking evidence, with evidence gaps called out. It requires human review and is not submitted automatically.
- **Merchant intelligence:** summaries computed from recorded orders rather than invented business metrics.
- **Optional impact ledger:** reported when requested; no donation is implied unless a real donation flow is separately configured.
- **AI orchestration:** an optional OpenAI-compatible model routes and summarizes requests. A constrained deterministic router remains available when no model key is configured.

## Agent system

The system has **six core specialist agents**, coordinated by the master orchestrator, plus one opt-in impact capability:

1. Trust — summarizes seller history and risk signals.
2. Payment — creates PayPal checkout; buyer approval is required before capture.
3. Delivery — presents shipment information and tracking evidence.
4. Dispute — prepares an evidence-based draft for human review.
5. Intelligence — summarizes merchant order metrics.
6. Collective Procurement — owns group-buy discovery, buyer commitments, supplier quote comparison, landed-cost ranking and human-reviewed counter-offers.
7. Optional Impact capability — reports the purchase impact ledger only when explicitly requested. Impact metadata may still be calculated during capture, but the impact specialist is not run on every purchase.

The former standalone Commerce/Group-buy agent has been removed. Its campaign-discovery responsibility is handled by Collective Procurement, avoiding duplicate routing and overlapping campaign summaries. The orchestrator allowlists only these six core agents plus the opt-in impact capability. The collective agent is **decision support**, not an autonomous buyer: it cannot award suppliers, bind buyers, or move money. Campaign owners explicitly award a quote, and each buyer individually approves their own PayPal checkout.

A normal purchase routes through Trust and Payment. Delivery, Dispute, Intelligence, Collective Procurement and Impact are invoked only when the request requires them. Impact is not automatically invoked for a purchase.

## New collective procurement flow

1. An authenticated owner creates a campaign with a target quantity, minimum quantity, currency, price target and deadline.
2. Buyers commit quantities and maximum acceptable unit prices.
3. Suppliers submit unit price, shipping-per-unit, minimum quantity and lead time.
4. The owner awards a quote only after the minimum buyer quantity is reached. The database function locks the campaign row and rechecks the conditions atomically.
5. Each committed buyer can request a separate PayPal checkout. The total is quantity × (unit price + shipping per unit). PayPal does not capture funds until the buyer approves on PayPal.
6. The existing PayPal webhook/payment flow records the payment state.

The campaign/quote/commitment API is available at:

- `POST /api/group-buys`
- `GET /api/group-buys`
- `POST /api/group-buys/{campaign_id}/quotes`
- `POST /api/group-buys/{campaign_id}/join`
- `POST /api/group-buys/{campaign_id}/award`
- `POST /api/group-buys/{campaign_id}/checkout`

Demo requests include `user_id`; production requests use `Authorization: Bearer <Supabase access token>` instead.

```json
// POST /api/group-buys
{
  "user_id": "owner_1",
  "title": "USB-C hubs bulk order",
  "product_ref": "SKU-USB-C-HUB",
  "target_quantity": 100,
  "minimum_quantity": 20,
  "currency": "USD",
  "target_unit_price": "18.00",
  "deadline": "2026-12-01T18:00:00+00:00"
}
```

A supplier posts a quote with `unit_price`, `shipping_per_unit`, `currency`, `lead_days` and `minimum_quantity`. A buyer joins with `quantity` and `max_unit_price`. The campaign owner then awards a quote; each buyer calls the checkout endpoint and approves their own PayPal order.

For the chat agent, include `procurement_campaign_id` in `/api/chat` and ask it to compare supplier quotes. The frontend has an optional campaign ID field. Group-buy discovery and comparison are handled by the Collective Procurement agent; there is no separate Commerce agent.

## Run locally

```bash
cp .env.example backend/.env
# Add PayPal sandbox credentials if you want to test checkout.
cd backend
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. Demo auth is only for local sandbox testing. The default frontend uses a browser-generated demo user ID.

## Supabase setup

Run `backend/schema.sql` in the Supabase SQL editor. It creates the existing order tables plus group-buy tables and database functions for atomic webhook claiming, buyer commitments and supplier award checks. Configure `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` in `backend/.env`. Never expose the service-role key in the frontend.

## Production gate — read before deploying

**This repository is production-oriented, not certified production-ready. Do not process live money until every item below is implemented and verified in your own environment.**

- The frontend still needs a real Supabase Auth sign-in/session implementation and must send verified access tokens. Backend JWT mode currently expects HS256 Supabase JWTs; confirm this matches your project's signing configuration or implement JWKS verification for asymmetric signing keys.
- Replace the in-process IP rate limiter with a shared distributed limiter (for example Redis or a database-backed atomic limiter) and configure trusted proxy IP handling.
- Add authorization policies/role management for supplier onboarding, campaign ownership, seller accounts and administrative actions; the backend service-role key bypasses RLS, so every endpoint must enforce identity and ownership.
- Add campaign cancellation, expiry worker, quote withdrawal, commitment cancellation/refund handling, partial fulfilment and failed-payment recovery.
- Add supplier identity verification and evidence-backed reliability metrics. Current quote ranking uses only landed price and lead time; it does not prove supplier quality.
- Add payment reconciliation, refunds/reversals, dispute lifecycle sync and operational alerts. Never rely on a browser redirect as proof of payment.
- Add distributed tracing, structured audit logs, secret rotation, backups, retention rules, monitoring and incident response.
- Verify PayPal webhook signature configuration and all event types in a PayPal sandbox account. Test duplicate, concurrent, delayed and out-of-order deliveries.
- Run backend tests, frontend typecheck/build, API integration tests against a disposable Supabase project, and an end-to-end sandbox purchase before release.

Production/live startup now refuses demo auth, demo data and in-memory storage. `ENV=production` requires `DEMO_MODE=false`, `AUTH_MODE=jwt` and configured Supabase storage. `PAYPAL_MODE=live` additionally requires the webhook ID.

## Validation performed for this package

- Backend Python compilation: passed.
- Backend unit tests: 19 passed.
- FastAPI app construction: passed.
- Frontend typecheck/build: not run successfully because frontend dependencies are not installed in this environment. Run `npm install`, `npm run typecheck` and `npm run build` locally.
- Supabase SQL and live PayPal integration: not executed against external services. No real PayPal transaction, Supabase deployment, or production security audit is claimed.
