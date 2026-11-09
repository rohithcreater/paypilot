from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "PAYPILOT AI"
    ENV: str = "development"
    DEMO_MODE: bool = True            # simulated trust seed + demo tracking. Never use with PAYPAL_MODE=live.
    AUTH_MODE: str = "demo"           # demo (client-asserted user_id) | jwt (Supabase access token)

    FRONTEND_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:3000"   # comma separated

    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    PAYPAL_MODE: str = "sandbox"      # sandbox | live
    PAYPAL_WEBHOOK_ID: str = ""

    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""    # service-role key, backend only
    SUPABASE_JWT_SECRET: str = ""

    LLM_API_KEY: str = ""             # optional: any OpenAI-compatible endpoint
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"

    RATE_LIMIT_PER_MINUTE: int = 30
    AGENT_TIMEOUT_SECONDS: float = 25.0
    MAX_ORDER_AMOUNT: float = 10000.0
    IMPACT_RATE: float = 0.01
    IMPACT_USD_PER_TREE: float = 1.0
    MIN_TRUST_SCORE: float = 0.5      # sellers with history scoring below this are blocked

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip().rstrip("/") for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def use_supabase(self) -> bool:
        return bool(self.SUPABASE_URL and self.SUPABASE_SERVICE_KEY)

    def validate_runtime(self) -> None:
        if self.PAYPAL_MODE not in ("sandbox", "live"):
            raise RuntimeError("PAYPAL_MODE must be 'sandbox' or 'live'")
        if self.AUTH_MODE not in ("demo", "jwt"):
            raise RuntimeError("AUTH_MODE must be 'demo' or 'jwt'")
        if not 1 <= self.AGENT_TIMEOUT_SECONDS <= 120:
            raise RuntimeError("AGENT_TIMEOUT_SECONDS must be between 1 and 120")
        if self.AUTH_MODE == "jwt" and not self.SUPABASE_JWT_SECRET:
            raise RuntimeError("AUTH_MODE=jwt requires SUPABASE_JWT_SECRET")
        if self.ENV == "production" or self.PAYPAL_MODE == "live":
            problems = []
            if self.DEMO_MODE:
                problems.append("DEMO_MODE must be false")
            if self.AUTH_MODE != "jwt":
                problems.append("AUTH_MODE must be jwt")
            if not self.use_supabase:
                problems.append("Supabase storage must be configured")
            if self.PAYPAL_MODE == "live" and not self.PAYPAL_WEBHOOK_ID:
                problems.append("PAYPAL_WEBHOOK_ID must be set for live PayPal")
            if problems:
                raise RuntimeError("Refusing to start in production/live mode: " + "; ".join(problems))


settings = Settings()
