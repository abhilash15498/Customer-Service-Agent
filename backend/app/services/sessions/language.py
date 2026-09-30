import re
from typing import Any, Dict, List, Optional, Set, Tuple
from app.core.dynamic_config import dynamic_config


class EntityLock:
    """Stores protected entities that must never be altered or corrupted during NLP translation."""
    def __init__(self, raw_text: str, entity_type: str, placeholder: str):
        self.raw_text = raw_text
        self.entity_type = entity_type
        self.placeholder = placeholder


class LanguageDetectionResult:
    def __init__(
        self,
        primary_language: str,
        confidence: float,
        is_mixed_language: bool,
        detected_languages: List[str],
        is_transliterated: bool,
        protected_entities: List[EntityLock],
        sanitized_text: str,
        low_confidence: bool
    ):
        self.primary_language = primary_language
        self.confidence = confidence
        self.is_mixed_language = is_mixed_language
        self.detected_languages = detected_languages
        self.is_transliterated = is_transliterated
        self.protected_entities = protected_entities
        self.sanitized_text = sanitized_text
        self.low_confidence = low_confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_language": self.primary_language,
            "confidence": self.confidence,
            "is_mixed_language": self.is_mixed_language,
            "detected_languages": self.detected_languages,
            "is_transliterated": self.is_transliterated,
            "low_confidence": self.low_confidence,
            "protected_entity_count": len(self.protected_entities)
        }


class LanguageProcessor:
    """
    Multilingual intelligence supporting:
    - English ('en')
    - Kannada ('kn')
    - Hindi ('hi')
    - Spanish ('es')
    - Mixed-language / code-switching
    - Strict entity preservation (order IDs, currency amounts, dates, phone numbers)
    """

    # Unicode ranges
    KANNADA_RANGE = re.compile(r"[\u0C80-\u0CFF]")
    DEVANAGARI_RANGE = re.compile(r"[\u0900-\u097F]")  # Hindi
    LATIN_RANGE = re.compile(r"[A-Za-z]")

    # Common transliteration tokens (Romanized Hindi/Kannada)
    TRANSLITERATION_TOKENS = {
        # Hindi transliteration
        "mera", "meri", "mere", "order", "kahan", "hai", "nahi", "aaya", "hua",
        "paise", "kat", "gaye", "karo", "jaldi", "batao", "kripya", "madad",
        # Kannada transliteration
        "nanna", "nange", "namaskara", "innu", "bandilla", "enu", "madodu",
        "beku", "agilla", "hogi", "banni", "dayavittu", "sahaya"
    }

    # Entity patterns that MUST NEVER be corrupted
    ORDER_REGEX = re.compile(r"(?i)\b(?:order\s*#?|#)\s*([a-zA-Z0-9_-]{4,20})\b")
    CURRENCY_REGEX = re.compile(r"(?i)(?:₹|rs\.?|inr|\$|eur|€)\s*([\d,]+(?:\.\d{1,2})?)|\b([\d,]+(?:\.\d{1,2})?)\s*(?:rupees?|inr|dollars?)\b")
    DATE_REGEX = re.compile(r"\b(?:\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
    PHONE_REGEX = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")

    @classmethod
    def lock_entities(cls, text: str) -> Tuple[str, List[EntityLock]]:
        """
        Extracts and locks critical entities (order numbers, currency values, dates)
        using unique placeholders so translation or tokenization cannot alter them.
        """
        locks: List[EntityLock] = []
        locked_text = text

        def replace_with_lock(match: re.Match, entity_type: str) -> str:
            raw = match.group(0)
            idx = len(locks)
            placeholder = f"__ENTITY_LOCK_{entity_type.upper()}_{idx}__"
            locks.append(EntityLock(raw_text=raw, entity_type=entity_type, placeholder=placeholder))
            return placeholder

        locked_text = cls.ORDER_REGEX.sub(lambda m: replace_with_lock(m, "ORDER_ID"), locked_text)
        locked_text = cls.CURRENCY_REGEX.sub(lambda m: replace_with_lock(m, "AMOUNT"), locked_text)
        locked_text = cls.DATE_REGEX.sub(lambda m: replace_with_lock(m, "DATE"), locked_text)
        locked_text = cls.PHONE_REGEX.sub(lambda m: replace_with_lock(m, "PHONE"), locked_text)

        return locked_text, locks

    @classmethod
    def unlock_entities(cls, text: str, locks: List[EntityLock]) -> str:
        """Restores locked entities back to their exact original characters."""
        restored = text
        for lock in locks:
            restored = restored.replace(lock.placeholder, lock.raw_text)
        return restored

    @classmethod
    def detect_language(cls, text: str) -> LanguageDetectionResult:
        if not text or not text.strip():
            return LanguageDetectionResult(
                primary_language="en",
                confidence=1.0,
                is_mixed_language=False,
                detected_languages=["en"],
                is_transliterated=False,
                protected_entities=[],
                sanitized_text="",
                low_confidence=False
            )

        locked_text, locks = cls.lock_entities(text)

        detected_scripts = set()
        if cls.KANNADA_RANGE.search(locked_text):
            detected_scripts.add("kn")
        if cls.DEVANAGARI_RANGE.search(locked_text):
            detected_scripts.add("hi")
        if cls.LATIN_RANGE.search(locked_text):
            detected_scripts.add("latin")

        # Check for transliterated words in latin text
        words = set(re.findall(r"\b[a-zA-Z]+\b", locked_text.lower()))
        translit_matches = words.intersection(cls.TRANSLITERATION_TOKENS)
        is_transliterated = len(translit_matches) > 0

        # Determine language breakdown
        detected_languages: List[str] = []
        if "kn" in detected_scripts:
            detected_languages.append("kn")
        if "hi" in detected_scripts:
            detected_languages.append("hi")

        COMMON_ENGLISH_WORDS = {"what", "should", "i", "do", "the", "a", "is", "was", "order", "amount", "debited", "charged", "my", "hello", "please", "help", "can"}

        if "latin" in detected_scripts:
            if is_transliterated:
                # Transliterated Indic
                if any(w in {"nanna", "nange", "innu", "bandilla"} for w in translit_matches):
                    detected_languages.append("kn-Latn")
                else:
                    detected_languages.append("hi-Latn")
                # Also check if English words co-occur (Code-switching / mixed language)
                if words.intersection(COMMON_ENGLISH_WORDS):
                    detected_languages.append("en")
            else:
                # Check for Spanish markers if applicable (e.g. ¿, ¡, gracias, cancelar)
                if any(w in {"gracias", "pedido", "cancelar", "reembolso", "ayuda", "por", "favor"} for w in words):
                    detected_languages.append("es")
                else:
                    detected_languages.append("en")

        is_mixed = len(detected_languages) > 1 or (len(detected_scripts) > 1 and "latin" in detected_scripts)

        # Primary language decision
        if "kn" in detected_scripts:
            primary_lang = "kn"
            conf = 0.95
        elif "hi" in detected_scripts:
            primary_lang = "hi"
            conf = 0.95
        elif "es" in detected_languages:
            primary_lang = "es"
            conf = 0.90
        elif is_transliterated:
            primary_lang = detected_languages[0] if detected_languages else "en"
            conf = 0.85
        else:
            primary_lang = "en"
            conf = 0.92

        # Check configurable threshold
        cfg_threshold = dynamic_config.get_config().sentiment_thresholds.get("low_confidence_threshold", 0.70)
        low_confidence = conf < cfg_threshold

        return LanguageDetectionResult(
            primary_language=primary_lang,
            confidence=conf,
            is_mixed_language=is_mixed,
            detected_languages=detected_languages or ["en"],
            is_transliterated=is_transliterated,
            protected_entities=locks,
            sanitized_text=locked_text,
            low_confidence=low_confidence
        )


language_processor = LanguageProcessor()
