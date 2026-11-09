import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.errors import BadRequest, DomainError
from app.core.llm import LLMClient
from app.core.paypal_service import PayPalService
from app.core.security import RateLimiter, resolve_user
from app.db.store import MemoryStore, SupabaseStore
from app.services import PayPilot

ID = r"^[A-Za-z0-9_\-:.]{1,100}$"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    user_id: Optional[str] = Field(default=None, pattern=ID)      # demo auth only
    seller_id: Optional[str] = Field(default=None, pattern=ID)
    amount: Optional[Decimal] = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    order_id: Optional[str] = Field(default=None, max_length=64)
    request_id: Optional[str] = Field(default=None, max_length=64)
    procurement_campaign_id: Optional[str] = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    response: str
    order_id: Optional[str] = None
    payment_url: Optional[str] = None
    plan: List[str]
    agent_logs: List[Dict[str, Any]]
    order: Optional[Dict[str, Any]] = None


class UserBody(BaseModel):
    user_id: Optional[str] = Field(default=None, pattern=ID)


class TrackingBody(UserBody):
    carrier: str = Field(min_length=1, max_length=60)
    tracking_number: str = Field(min_length=1, max_length=60)
    status: str = Field(default="SHIPPED", max_length=30)


class CampaignCreate(BaseModel):
    user_id: Optional[str] = Field(default=None, pattern=ID)
    title: str = Field(min_length=3, max_length=120)
    product_ref: str = Field(min_length=1, max_length=160)
    target_quantity: int = Field(ge=2, le=100000)
    minimum_quantity: int = Field(ge=2, le=100000)
    currency: str = Field(pattern=r"^(USD|EUR|GBP|CAD|AUD)$")
    target_unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    deadline: str = Field(min_length=10, max_length=40)


class SupplierQuoteCreate(BaseModel):
    user_id: Optional[str] = Field(default=None, pattern=ID)
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    shipping_per_unit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^(USD|EUR|GBP|CAD|AUD)$")
    lead_days: int = Field(ge=0, le=365)
    minimum_quantity: int = Field(ge=1, le=100000)
    terms: str = Field(default="", max_length=1000)


class GroupBuyJoin(BaseModel):
    user_id: Optional[str] = Field(default=None, pattern=ID)
    quantity: int = Field(ge=1, le=10000)
    max_unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class AwardQuoteBody(BaseModel):
    user_id: Optional[str] = Field(default=None, pattern=ID)
    quote_id: str = Field(min_length=1, max_length=64)


def build_service(settings: Settings) -> PayPilot:
    store = (SupabaseStore(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
             if settings.use_supabase else MemoryStore())
    llm = LLMClient(settings.LLM_API_KEY, settings.LLM_BASE_URL, settings.LLM_MODEL)
    paypal = PayPalService(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET, settings.PAYPAL_MODE)
    return PayPilot(settings, store, llm, paypal)


def create_app(settings: Optional[Settings] = None, service: Optional[PayPilot] = None) -> FastAPI:
    if settings is None:
        from app.core.config import settings as default_settings
        settings = default_settings
    settings.validate_runtime()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service = service or build_service(settings)
    limiter = RateLimiter(settings.RATE_LIMIT_PER_MINUTE)
    if not settings.use_supabase:
        logging.getLogger("paypilot").warning("Using in-memory storage: orders are lost on restart.")

    app = FastAPI(title=settings.PROJECT_NAME, version="1.0.0",
                  docs_url=None if settings.ENV == "production" else "/docs", redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    def guard(request: Request) -> None:
        limiter.check(request.client.host if request.client else "unknown")

    api = APIRouter(prefix="/api")

    @api.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request):
        guard(request)
        user = resolve_user(settings, request.headers, body.user_id)
        return await service.handle_chat(user_id=user, message=body.message, seller_id=body.seller_id,
                                         amount=body.amount, order_id=body.order_id, request_id=body.request_id,
                                         procurement_campaign_id=body.procurement_campaign_id)

    @api.post("/orders/{order_id}/capture")
    async def capture(order_id: str, body: UserBody, request: Request):
        guard(request)
        return await service.capture_order(resolve_user(settings, request.headers, body.user_id), order_id)

    @api.get("/orders/{order_id}")
    async def get_order(order_id: str, request: Request, user_id: Optional[str] = None):
        guard(request)
        return await service.get_order(resolve_user(settings, request.headers, user_id), order_id)

    @api.post("/orders/{order_id}/tracking")
    async def add_tracking(order_id: str, body: TrackingBody, request: Request):
        guard(request)
        user = resolve_user(settings, request.headers, body.user_id)
        return await service.add_tracking(user, order_id, body.carrier, body.tracking_number, body.status)

    @api.post("/group-buys")
    async def create_group_buy(body: CampaignCreate, request: Request):
        guard(request)
        user = resolve_user(settings, request.headers, body.user_id)
        return await service.create_group_buy_campaign(user, title=body.title, product_ref=body.product_ref,
            target_quantity=body.target_quantity, minimum_quantity=body.minimum_quantity,
            currency=body.currency, target_unit_price=body.target_unit_price, deadline=body.deadline)

    @api.post("/group-buys/{campaign_id}/quotes")
    async def submit_quote(campaign_id: str, body: SupplierQuoteCreate, request: Request):
        guard(request)
        user = resolve_user(settings, request.headers, body.user_id)
        return await service.submit_supplier_quote(user, campaign_id, unit_price=body.unit_price,
            currency=body.currency, lead_days=body.lead_days, minimum_quantity=body.minimum_quantity,
            shipping_per_unit=body.shipping_per_unit, terms=body.terms)

    @api.post("/group-buys/{campaign_id}/join")
    async def join_group_buy(campaign_id: str, body: GroupBuyJoin, request: Request):
        guard(request)
        user = resolve_user(settings, request.headers, body.user_id)
        return await service.join_group_buy(user, campaign_id, quantity=body.quantity,
                                             max_unit_price=body.max_unit_price)

    @api.post("/group-buys/{campaign_id}/award")
    async def award_group_buy(campaign_id: str, body: AwardQuoteBody, request: Request):
        guard(request)
        user = resolve_user(settings, request.headers, body.user_id)
        return await service.award_group_buy_quote(user, campaign_id, body.quote_id)

    @api.post("/group-buys/{campaign_id}/checkout")
    async def checkout_group_buy(campaign_id: str, request: Request, user_id: Optional[str] = None):
        guard(request)
        user = resolve_user(settings, request.headers, user_id)
        return await service.checkout_group_buy(user, campaign_id)

    @api.get("/group-buys")
    async def list_group_buys(request: Request, user_id: Optional[str] = None):
        guard(request)
        user = resolve_user(settings, request.headers, user_id)
        return await service.store.list_group_buy_campaigns(limit=50)

    @api.post("/webhooks/paypal")
    async def paypal_webhook(request: Request):
        try:
            event = await request.json()
        except ValueError:
            raise BadRequest("Body must be JSON")
        if not isinstance(event, dict):
            raise BadRequest("Body must be a JSON object")
        return await service.handle_webhook(dict(request.headers), event)

    app.include_router(api)

    @app.get("/health")
    async def health():
        return {"status": "ok", "paypal_mode": settings.PAYPAL_MODE, "demo": settings.DEMO_MODE}

    return app
