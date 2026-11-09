create table if not exists public.orders (
  order_id   text primary key,
  buyer_id   text not null,
  seller_id  text not null,
  capture_id text,
  status     text not null,
  data       jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists orders_seller_idx  on public.orders (seller_id, created_at desc);
create index if not exists orders_buyer_idx   on public.orders (buyer_id);
create index if not exists orders_capture_idx on public.orders (capture_id);

create table if not exists public.webhook_events (
  event_id    text primary key,
  received_at timestamptz not null default now()
);

-- RLS on with no policies: only the backend's service-role key can read or write.
alter table public.orders         enable row level security;
alter table public.webhook_events enable row level security;

-- Production-oriented webhook idempotency: PROCESSING claims expire after five minutes.
alter table public.webhook_events
  add column if not exists status text not null default 'PROCESSED',
  add column if not exists locked_at timestamptz,
  add column if not exists processed_at timestamptz;

create or replace function public.claim_webhook_event(p_event_id text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare affected integer := 0;
begin
  insert into public.webhook_events(event_id, status, locked_at)
  values (p_event_id, 'PROCESSING', now())
  on conflict (event_id) do update
    set status = 'PROCESSING', locked_at = now()
    where public.webhook_events.status <> 'PROCESSED'
      and (public.webhook_events.locked_at is null
           or public.webhook_events.locked_at < now() - interval '5 minutes');
  get diagnostics affected = row_count;
  return affected > 0;
end;
$$;

create or replace function public.release_webhook_event(p_event_id text)
returns void
language sql
security definer
set search_path = public
as $$
  delete from public.webhook_events where event_id = p_event_id and status = 'PROCESSING';
$$;

create or replace function public.mark_webhook_event_processed(p_event_id text)
returns void
language sql
security definer
set search_path = public
as $$
  update public.webhook_events
     set status = 'PROCESSED', processed_at = now(), locked_at = null
   where event_id = p_event_id;
$$;

create table if not exists public.group_buy_campaigns (
  id uuid primary key default gen_random_uuid(),
  owner_id text not null,
  title text not null check (length(title) between 3 and 120),
  product_ref text not null,
  target_quantity integer not null check (target_quantity >= 2),
  minimum_quantity integer not null check (minimum_quantity >= 2 and minimum_quantity <= target_quantity),
  committed_quantity integer not null default 0 check (committed_quantity >= 0),
  currency text not null check (currency in ('USD','EUR','GBP','CAD','AUD')),
  target_unit_price numeric(12,2) not null check (target_unit_price > 0),
  deadline timestamptz not null,
  status text not null default 'OPEN' check (status in ('OPEN','AWARDED_PENDING_BUYER_APPROVAL','CLOSED','CANCELLED')),
  selected_quote_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists group_buy_campaigns_status_idx
  on public.group_buy_campaigns(status, deadline);

create table if not exists public.supplier_quotes (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references public.group_buy_campaigns(id) on delete cascade,
  supplier_id text not null,
  unit_price numeric(12,2) not null check (unit_price > 0),
  shipping_per_unit numeric(12,2) not null default 0 check (shipping_per_unit >= 0),
  currency text not null check (currency in ('USD','EUR','GBP','CAD','AUD')),
  lead_days integer not null check (lead_days between 0 and 365),
  minimum_quantity integer not null default 1 check (minimum_quantity >= 1),
  terms text not null default '',
  status text not null default 'SUBMITTED' check (status in ('SUBMITTED','SELECTED_PENDING_APPROVAL','REJECTED')),
  created_at timestamptz not null default now()
);
create index if not exists supplier_quotes_campaign_idx on public.supplier_quotes(campaign_id, created_at);

create table if not exists public.group_buy_commitments (
  campaign_id uuid not null references public.group_buy_campaigns(id) on delete cascade,
  buyer_id text not null,
  quantity integer not null check (quantity between 1 and 10000),
  max_unit_price numeric(12,2) not null check (max_unit_price > 0),
  status text not null default 'COMMITTED' check (status in ('COMMITTED','PAYMENT_PENDING','PAYMENT_FAILED','PAYMENT_REVIEW','PAID','REFUNDED','CANCELLED')),
  payment_order_id text unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (campaign_id, buyer_id)
);
create index if not exists group_buy_commitments_buyer_idx on public.group_buy_commitments(buyer_id, created_at desc);

alter table public.group_buy_campaigns enable row level security;
alter table public.supplier_quotes enable row level security;
alter table public.group_buy_commitments enable row level security;
-- These tables intentionally have no client policies. Only the backend service role accesses them.

create or replace function public.join_group_buy(
  p_campaign_id uuid, p_buyer_id text, p_quantity integer, p_max_unit_price numeric
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  c public.group_buy_campaigns%rowtype;
  prior_qty integer := 0;
  result_row public.group_buy_commitments%rowtype;
begin
  select * into c from public.group_buy_campaigns where id = p_campaign_id for update;
  if not found or c.status <> 'OPEN' or c.deadline <= now() then
    raise exception 'Campaign is not open';
  end if;
  if p_quantity < 1 or p_quantity > 10000 or p_max_unit_price <= 0 then
    raise exception 'Invalid commitment';
  end if;
  select quantity into prior_qty from public.group_buy_commitments
    where campaign_id = p_campaign_id and buyer_id = p_buyer_id for update;
  if c.committed_quantity - coalesce(prior_qty, 0) + p_quantity > c.target_quantity then
    raise exception 'Campaign target quantity would be exceeded';
  end if;
  insert into public.group_buy_commitments(campaign_id,buyer_id,quantity,max_unit_price,status)
  values (p_campaign_id,p_buyer_id,p_quantity,p_max_unit_price,'COMMITTED')
  on conflict (campaign_id,buyer_id) do update set quantity = excluded.quantity,
    max_unit_price = excluded.max_unit_price, status = 'COMMITTED', updated_at = now();
  update public.group_buy_campaigns set committed_quantity = committed_quantity + p_quantity - coalesce(prior_qty,0),
    updated_at = now() where id = p_campaign_id;
  select * into result_row from public.group_buy_commitments
    where campaign_id = p_campaign_id and buyer_id = p_buyer_id;
  return to_jsonb(result_row);
end;
$$;

create or replace function public.submit_supplier_quote(
  p_campaign_id uuid, p_supplier_id text, p_unit_price numeric, p_shipping_per_unit numeric,
  p_currency text, p_lead_days integer, p_minimum_quantity integer, p_terms text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  c public.group_buy_campaigns%rowtype;
  q public.supplier_quotes%rowtype;
begin
  select * into c from public.group_buy_campaigns where id = p_campaign_id for update;
  if not found or c.status <> 'OPEN' or c.deadline <= now() then
    raise exception 'Campaign is not open';
  end if;
  if p_currency <> c.currency or p_unit_price <= 0 or p_shipping_per_unit < 0
     or p_lead_days < 0 or p_lead_days > 365 or p_minimum_quantity < 1 then
    raise exception 'Supplier quote values are invalid';
  end if;
  insert into public.supplier_quotes(campaign_id,supplier_id,unit_price,shipping_per_unit,currency,
      lead_days,minimum_quantity,terms,status)
  values (p_campaign_id,p_supplier_id,p_unit_price,p_shipping_per_unit,p_currency,
      p_lead_days,p_minimum_quantity,left(coalesce(p_terms,''),1000),'SUBMITTED')
  returning * into q;
  return to_jsonb(q);
end;
$$;

create or replace function public.award_group_buy_quote(
  p_campaign_id uuid, p_owner_id text, p_quote_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  c public.group_buy_campaigns%rowtype;
  q public.supplier_quotes%rowtype;
begin
  select * into c from public.group_buy_campaigns where id = p_campaign_id for update;
  if not found or c.owner_id <> p_owner_id or c.status <> 'OPEN' then
    raise exception 'Campaign not found, not owned, or not open';
  end if;
  if c.deadline <= now() or c.committed_quantity < c.minimum_quantity then
    raise exception 'Minimum quantity not reached or campaign expired';
  end if;
  select * into q from public.supplier_quotes where id = p_quote_id and campaign_id = p_campaign_id for update;
  if not found or q.currency <> c.currency or q.minimum_quantity > c.committed_quantity then
    raise exception 'Supplier quote is invalid for this campaign';
  end if;
  update public.group_buy_campaigns set status = 'AWARDED_PENDING_BUYER_APPROVAL',
    selected_quote_id = p_quote_id, updated_at = now() where id = p_campaign_id returning * into c;
  update public.supplier_quotes set status = case when id = p_quote_id then 'SELECTED_PENDING_APPROVAL' else 'REJECTED' end
    where campaign_id = p_campaign_id;
  return to_jsonb(c);
end;
$$;

-- The API calls these functions with the backend service-role key only.
revoke all on function public.claim_webhook_event(text) from public;
revoke all on function public.release_webhook_event(text) from public;
revoke all on function public.mark_webhook_event_processed(text) from public;
revoke all on function public.join_group_buy(uuid,text,integer,numeric) from public;
revoke all on function public.award_group_buy_quote(uuid,text,uuid) from public;
revoke all on function public.submit_supplier_quote(uuid,text,numeric,numeric,text,integer,integer,text) from public;


grant execute on function public.claim_webhook_event(text) to service_role;
grant execute on function public.release_webhook_event(text) to service_role;
grant execute on function public.mark_webhook_event_processed(text) to service_role;
grant execute on function public.join_group_buy(uuid,text,integer,numeric) to service_role;
grant execute on function public.award_group_buy_quote(uuid,text,uuid) to service_role;
grant execute on function public.submit_supplier_quote(uuid,text,numeric,numeric,text,integer,integer,text) to service_role;

grant select, insert, update, delete on public.group_buy_campaigns to service_role;
grant select, insert, update, delete on public.supplier_quotes to service_role;
grant select, insert, update, delete on public.group_buy_commitments to service_role;
grant select, insert, update, delete on public.webhook_events to service_role;
