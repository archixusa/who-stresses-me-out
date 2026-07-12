"""Ortam degiskenlerini tek yerden yukler ve dogrular."""
import os

from dotenv import load_dotenv

load_dotenv()


def _get(name, default=None, required=False):
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Eksik ortam degiskeni: {name} -> .env dosyasini doldur")
    return val


# --- Telegram (bot icin zorunlu) ---
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")

# --- Whoop gayriresmi (dakikalik HR) ---
WHOOP_EMAIL = _get("WHOOP_EMAIL")
WHOOP_PASSWORD = _get("WHOOP_PASSWORD")

# --- Whoop resmi OAuth (Faz 2, opsiyonel) ---
WHOOP_CLIENT_ID = _get("WHOOP_CLIENT_ID")
WHOOP_CLIENT_SECRET = _get("WHOOP_CLIENT_SECRET")
WHOOP_REDIRECT_URI = _get("WHOOP_REDIRECT_URI", "http://localhost:8080/callback")
TOKEN_STORE_PATH = _get("TOKEN_STORE_PATH", "whoop_tokens.json")

# --- Otomatik baglam kaynaklari (takvim / Slack) ---
AUTO_SOURCES = _get("AUTO_SOURCES", "")   # or. "google_calendar,slack" (bos = kapali)
GOOGLE_CREDENTIALS_PATH = _get("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
GOOGLE_TOKEN_PATH = _get("GOOGLE_TOKEN_PATH", "google_token.json")
GOOGLE_CALENDAR_ID = _get("GOOGLE_CALENDAR_ID", "primary")
SLACK_TOKEN = _get("SLACK_TOKEN")
SLACK_GAP_MIN = int(_get("SLACK_GAP_MIN", "20"))   # bu kadar dk sessizlik yeni konusma sayilir

# --- Zaman dilimi (raporlarda yerel saat) ---
LOCAL_TZ = _get("LOCAL_TZ", "Europe/Istanbul")

# --- Analiz ayarlari ---
DEFAULT_WINDOW_MIN = int(_get("DEFAULT_WINDOW_MIN", "90"))
MAX_WINDOW_MIN = int(_get("MAX_WINDOW_MIN", "240"))          # /bitir gec kalirsa tavan
TRIM_MINUTES = int(_get("TRIM_MINUTES", "10"))               # pencere basindan (varis/yuruyus) kirp
BASELINE_PRE_MIN = int(_get("BASELINE_PRE_MIN", "90"))       # bulusma oncesi dinlenme penceresi
MIN_BASELINE_SAMPLES = int(_get("MIN_BASELINE_SAMPLES", "10"))
MIN_EVENT_SAMPLES = int(_get("MIN_EVENT_SAMPLES", "5"))
ELEVATION_THRESHOLD_BPM = int(_get("ELEVATION_THRESHOLD_BPM", "12"))
SHRINK_K = int(_get("SHRINK_K", "2"))                        # kucuk-orneklem geri cekme gucu

# --- Belirsizlik / kanit seviyesi ---
BOOTSTRAP_N = int(_get("BOOTSTRAP_N", "1000"))               # bootstrap yeniden ornekleme sayisi
BOOTSTRAP_SEED = int(_get("BOOTSTRAP_SEED", "1234"))         # deterministik test icin sabit tohum
MIN_COVERAGE = float(_get("MIN_COVERAGE", "0.5"))            # gruptaki HR-eslesen event orani esigi
WIDE_CI_BPM = float(_get("WIDE_CI_BPM", "20"))               # bundan genis GA -> zayif kanit
CONFOUNDER_FRAC = float(_get("CONFOUNDER_FRAC", "0.5"))      # bu orani asan confounder -> kanit dusur
MATCHED_CONTROL_HALFWIN_MIN = int(_get("MATCHED_CONTROL_HALFWIN_MIN", "90"))  # eslesmis kontrol saat penceresi

# --- Sync ---
DB_PATH = _get("DB_PATH", "whoop_stress.db")
SYNC_DAYS = int(_get("SYNC_DAYS", "8"))
HR_WINDOW_DAYS = int(_get("HR_WINDOW_DAYS", "7"))            # gayriresmi HR API pencere limiti
HR_PROVIDER = _get("HR_PROVIDER", "unofficial")             # unofficial | none (token yolu manuel)
# Gayriresmi HR icin jeton: ONCE env, yoksa whoop_token.txt (kullanimdan sonra sil)
WHOOP_ACCESS_TOKEN = _get("WHOOP_ACCESS_TOKEN")


def require_bot():
    """Bot baslarken cagir: zorunlu Telegram degiskenlerini dogrular."""
    _get("TELEGRAM_BOT_TOKEN", required=True)
    _get("TELEGRAM_CHAT_ID", required=True)


def require_hr():
    """Gayriresmi HR sync'i baslarken cagir."""
    _get("WHOOP_EMAIL", required=True)
    _get("WHOOP_PASSWORD", required=True)


def require_oauth():
    """Resmi API'yi kullanirken cagir."""
    _get("WHOOP_CLIENT_ID", required=True)
    _get("WHOOP_CLIENT_SECRET", required=True)
