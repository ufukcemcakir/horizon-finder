from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    DB_PATH: str = "horizon.db"
    API_KEY: str = "SEDIA"
    FT_SEARCH_BASE: str = (
        "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
    )

    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_DELAY_SEC: int = 6
    MAX_DESC_CHARS: int = 800
    MAX_PROFILE_CHARS: int = 1500
    LLM_MAX_TOKENS_SCREENING: int = 800
    LLM_MAX_TOKENS_ANALYSIS: int = 350
    LLM_MAX_TOKENS_IDEA: int = 1000

    SCREENING_BATCH_SIZE: int = 12
    PREFILTER_TOP_K: int = 60

    GROQ_TPM_LIMIT: int = 5500
    GROQ_RPM_LIMIT: int = 28
    GROQ_MAX_RETRIES: int = 3
    GROQ_RETRY_BASE_SEC: float = 15.0

    MAX_PROFILE_LENGTH: int = 15_000
    MIN_PASSWORD_LENGTH: int = 6
    MAX_EMAIL_LENGTH: int = 254

    DEFAULT_RESULTS_LIMIT: int = 50
    DEFAULT_ANALYZE_COUNT: int = 5
    API_FETCH_DELAY: float = 0.15


CFG = Config()

ALLOWED_ACTION_TYPES: frozenset[str] = frozenset({
    "IA", "RIA", "HORIZON-IA", "HORIZON-RIA",
    "Innovation Action", "Research and Innovation Action",
    "HORIZON Innovation Actions",
    "HORIZON Research and Innovation Actions",
})

CALLS_COLS: tuple[str, ...] = (
    "topic_id", "title", "call_description", "summary", "status", "deadline",
    "opening_date", "type_of_action", "programme_period", "url", "cluster", "budget", "raw_json",
)
_CALLS_PH: str = ",".join("?" * len(CALLS_COLS))

STATUS_MAP: dict[str, str] = {
    "31094501": "Open", "31094502": "Forthcoming", "31094503": "Closed",
    "open": "Open", "forthcoming": "Forthcoming", "closed": "Closed",
}

CURATED_LINKS: tuple[tuple[str, str], ...] = (
    ("Funding & Tenders Portal",
     "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"
     "screen/opportunities/browse-by-programme"),
    ("Ideal-ist (ICT/Digital)", "https://www.ideal-ist.eu/"),
    ("Enterprise Europe Network", "https://een.ec.europa.eu/"),
    ("EEN Partnering",
     "https://een.ec.europa.eu/partnering-opportunities"),
    ("Horizon NCP Networks", "https://www.ncpportal.eu/"),
    ("EUREKA Network", "https://www.eurekanetwork.org/"),
)

ACTIVE_STATUSES: tuple[str, ...] = (
    "31094501", "31094502", "open", "forthcoming",
)
