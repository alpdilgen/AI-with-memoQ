# Configuration for Enhanced Translation Assistant

# Supported languages - EU languages + major world languages
# Format: 'language_code': 'Language Name'
SUPPORTED_LANGUAGES = {
    # EU Languages
    'bul': 'Bulgarian',
    'hrv': 'Croatian',
    'cze': 'Czech',
    'dan': 'Danish',
    'dut': 'Dutch',
    'eng': 'English',
    'en-gb': 'English (UK)',
    'est': 'Estonian',
    'fin': 'Finnish',
    'fre': 'French',
    'ger': 'German',
    'gre': 'Greek',
    'hun': 'Hungarian',
    'gle': 'Irish',
    'ita': 'Italian',
    'lav': 'Latvian',
    'lit': 'Lithuanian',
    'mlt': 'Maltese',
    'pol': 'Polish',
    'por': 'Portuguese',
    'rum': 'Romanian',
    'slo': 'Slovak',
    'slv': 'Slovenian',
    'spa': 'Spanish',
    'swe': 'Swedish',
    
    # Major World Languages
    'ara': 'Arabic',
    'zho': 'Chinese (Simplified)',
    'zht': 'Chinese (Traditional)',
    'jpn': 'Japanese',
    'kor': 'Korean',
    'rus': 'Russian',
    'tur': 'Turkish',
    'hin': 'Hindi',
    'ben': 'Bengali',
    'vie': 'Vietnamese',
    'tha': 'Thai',
    'afr': 'Afrikaans',
    'heb': 'Hebrew',
    'ukr': 'Ukrainian',
    'nor': 'Norwegian',
}

# OpenAI Models
OPENAI_MODELS = [
    'gpt-4o',
    'gpt-4o-mini',
    'gpt-4-turbo',
]

# Default values
DEFAULT_SOURCE_LANGUAGE = 'eng'
DEFAULT_TARGET_LANGUAGE = 'tur'
DEFAULT_MODEL = 'gpt-4o'

# Translation settings
DEFAULT_ACCEPTANCE_THRESHOLD = 95      # % - bypass segments at this match or higher
DEFAULT_MATCH_THRESHOLD = 70           # % - use fuzzy match for TM context
DEFAULT_CHAT_HISTORY = 5               # segments to include in history for consistency

# API Settings
OPENAI_API_BASE = "https://api.openai.com/v1"

# App name
APP_NAME = "Enhanced Translation Assistant"

# UI Settings
LAYOUT = "wide"
THEME = "light"

# Batch processing
DEFAULT_BATCH_SIZE = 20
MAX_BATCH_SIZE = 50

# Cost calculation
TOKENS_PER_SEGMENT = 100
GPT_4O_INPUT_PRICE = 0.00025   # per token
GPT_4O_OUTPUT_PRICE = 0.001    # per token
CONTEXT_DISCOUNT = 0.5         # 50% discount for fuzzy match segments

# Prompt template
PROMPT_TEMPLATE_PATH = None  # Set to file path if using custom template, None for default

# ISO 639-1 to memoQ 3-letter language code mapping
ISO_TO_MEMOQ_LANG = {
    'en': 'eng', 'tr': 'tur', 'de': 'ger', 'fr': 'fre', 'es': 'spa',
    'it': 'ita', 'pt': 'por', 'pl': 'pol', 'ru': 'rus', 'ja': 'jpn',
    'zh': 'zho', 'ar': 'ara', 'ko': 'kor', 'nl': 'dut', 'sv': 'swe',
    'no': 'nor', 'da': 'dan', 'fi': 'fin', 'el': 'gre', 'he': 'heb',
    'th': 'tha', 'vi': 'vie', 'bg': 'bul', 'ro': 'rum', 'cs': 'cze',
    'sk': 'slo', 'uk': 'ukr', 'et': 'est', 'lv': 'lav', 'lt': 'lit',
    'hu': 'hun', 'hr': 'hrv', 'sl': 'slv', 'mt': 'mlt', 'ga': 'gle',
    'af': 'afr', 'bn': 'ben', 'hi': 'hin',
}

def convert_detected_lang(detected_code: str) -> str:
    """Convert auto-detected ISO code to memoQ 3-letter code."""
    if not detected_code:
        return detected_code
    parts = detected_code.split('-')
    if len(parts) == 2:
        base = ISO_TO_MEMOQ_LANG.get(parts[0], parts[0])
        return f"{base}-{parts[1].upper()}"
    return ISO_TO_MEMOQ_LANG.get(detected_code, detected_code)
