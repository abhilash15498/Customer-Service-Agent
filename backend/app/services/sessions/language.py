import re
from typing import Any, Dict, List, Optional, Set, Tuple
from app.core.dynamic_config import dynamic_config


class EntityLock:
    """Stores protected entities that must never be altered or corrupted during NLP translation."""
    def __init__(self, raw_text: str, entity_type: str, placeholder: str, entity_value: Optional[str] = None):
        self.raw_text = raw_text
        self.entity_type = entity_type
        self.placeholder = placeholder
        self.entity_value = entity_value or raw_text


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
        low_confidence: bool,
        clarification_prompt: Optional[str] = None
    ):
        self.primary_language = primary_language
        self.confidence = confidence
        self.is_mixed_language = is_mixed_language
        self.detected_languages = detected_languages
        self.is_transliterated = is_transliterated
        self.protected_entities = protected_entities
        self.sanitized_text = sanitized_text
        self.low_confidence = low_confidence
        self.clarification_prompt = clarification_prompt

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_language": self.primary_language,
            "confidence": self.confidence,
            "is_mixed_language": self.is_mixed_language,
            "detected_languages": self.detected_languages,
            "is_transliterated": self.is_transliterated,
            "low_confidence": self.low_confidence,
            "clarification_prompt": self.clarification_prompt,
            "protected_entity_count": len(self.protected_entities)
        }


class LanguageProcessor:
    """
    Enterprise Multilingual Intelligence supporting:
    - English ('en')
    - Kannada ('kn', 'kn-Latn')
    - Hindi ('hi', 'hi-Latn')
    - Spanish ('es')
    - French ('fr')
    - German ('de')
    - Mixed-language / Code-switching (Scenario 55)
    - Cross-turn language switching (Scenario 56)
    - Transliteration detection (Scenario 57)
    - Typo-tolerant entity preservation (Scenario 58)
    - Low-confidence language guard & clarification (Scenario 59)
    """

    # Unicode ranges
    KANNADA_RANGE = re.compile(r"[\u0C80-\u0CFF]")
    DEVANAGARI_RANGE = re.compile(r"[\u0900-\u097F]")  # Hindi
    LATIN_RANGE = re.compile(r"[A-Za-z]")

    # Common transliteration tokens (Romanized Hindi/Kannada)
    TRANSLITERATION_TOKENS = {
        # Hindi transliteration
        "mera", "meri", "mere", "kahan", "hai", "nahi", "aaya", "hua",
        "paise", "kat", "gaye", "karo", "jaldi", "batao", "kripya", "madad",
        "mujhe", "chahiye", "tha", "karo", "kyun", "kab", "tak", "gaya",
        # Kannada transliteration
        "nanna", "nange", "namaskara", "innu", "bandilla", "enu", "madodu",
        "beku", "agilla", "hogi", "banni", "dayavittu", "sahaya", "yavaga"
    }

    # Lexical dictionaries for European languages beyond English
    SPANISH_WORDS = {
        "gracias", "pedido", "cancelar", "reembolso", "ayuda", "por", "favor",
        "donde", "esta", "mi", "paquete", "problema", "factura", "tarjeta",
        "cobrado", "doble", "hola", "buenos", "dias", "tardes"
    }
    FRENCH_WORDS = {
        "bonjour", "merci", "commande", "remboursement", "probleme", "livraison",
        "aide", "sil", "vous", "plait", "retard", "facture", "payer", "carte",
        "annuler", "ou", "est", "mon", "colis"
    }
    GERMAN_WORDS = {
        "hallo", "danke", "bestellung", "ruckerstattung", "rechnung", "hilfe",
        "bitte", "problem", "paket", "wo", "ist", "meine", "lieferung",
        "stornieren", "doppelt", "abgebucht", "guten", "tag"
    }
    COMMON_ENGLISH_WORDS = {
        "what", "should", "i", "do", "the", "a", "is", "was", "order", "amount",
        "debited", "charged", "my", "hello", "please", "help", "can", "refund",
        "status", "where", "delivered", "shipping", "cancel", "payment", "card",
        "account", "issue", "problem", "delayed", "late", "twice", "need", "urgent"
    }

    # Typo-tolerant Entity Patterns that MUST NEVER be corrupted (Req 9.3, Scenario 58)
    ORDER_REGEX = re.compile(
        r"(?i)\b(?:order|ordr|oder|invoice|receipt|pedido|commande|bestellung)\s*(?:id|#|number|no\.?)?[ \t]*[:#\-]?[ \t]*(?:is[ \t]+)?([a-zA-Z0-9_\-]*\d[a-zA-Z0-9_\-]*)\b|#\s*([0-9]{4,10})\b|\bORD[-_]([a-zA-Z0-9]+)\b"
    )
    CURRENCY_REGEX = re.compile(
        r"(?i)(?:₹|rs\.?|inr|\$|eur|€)[ \t]*([\d,]+(?:\.\d{1,2})?)|\b([\d,]+(?:\.\d{1,2})?)[ \t]*(?:rupees?|inr|dollars?|euros?)\b"
    )
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
            groups = [g for g in match.groups() if g is not None]
            val = groups[0] if groups else raw
            idx = len(locks)
            placeholder = f"__ENTITY_LOCK_{entity_type.upper()}_{idx}__"
            locks.append(EntityLock(raw_text=raw, entity_type=entity_type, placeholder=placeholder, entity_value=val))
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
        """
        Detects primary language, mixed languages, transliteration, and confidence.
        Enforces low-confidence guards (Scenario 59).
        """
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

        # Extract words from raw text for linguistic inspection
        raw_words = set(re.findall(r"\b[a-zA-Z\u00C0-\u017F]+\b", locked_text.lower()))
        translit_matches = raw_words.intersection(cls.TRANSLITERATION_TOKENS)
        is_transliterated = len(translit_matches) > 0

        # Spanish, French, German matches
        es_matches = raw_words.intersection(cls.SPANISH_WORDS)
        fr_matches = raw_words.intersection(cls.FRENCH_WORDS)
        de_matches = raw_words.intersection(cls.GERMAN_WORDS)
        en_matches = raw_words.intersection(cls.COMMON_ENGLISH_WORDS)

        detected_languages: List[str] = []
        if "kn" in detected_scripts:
            detected_languages.append("kn")
        if "hi" in detected_scripts:
            detected_languages.append("hi")

        if "latin" in detected_scripts:
            if is_transliterated:
                if any(w in {"nanna", "nange", "innu", "bandilla", "madodu", "yavaga"} for w in translit_matches):
                    detected_languages.append("kn-Latn")
                else:
                    detected_languages.append("hi-Latn")
                if en_matches:
                    detected_languages.append("en")
            elif es_matches or "¿" in text or "¡" in text:
                detected_languages.append("es")
            elif fr_matches:
                detected_languages.append("fr")
            elif de_matches:
                detected_languages.append("de")
            elif en_matches:
                detected_languages.append("en")
            else:
                # Check if it has any recognizable tokens or if it is ambiguous gibberish
                if len(raw_words) > 0 and len(raw_words.intersection(cls.COMMON_ENGLISH_WORDS)) == 0:
                    pass
                else:
                    detected_languages.append("en")

        is_mixed = len(detected_languages) > 1 or (len(detected_scripts) > 1 and "latin" in detected_scripts)

        # Primary Language Decision & Confidence Scoring
        if "kn" in detected_scripts:
            primary_lang = "kn"
            conf = 0.96
        elif "hi" in detected_scripts:
            primary_lang = "hi"
            conf = 0.96
        elif "es" in detected_languages:
            primary_lang = "es"
            conf = 0.92
        elif "fr" in detected_languages:
            primary_lang = "fr"
            conf = 0.92
        elif "de" in detected_languages:
            primary_lang = "de"
            conf = 0.92
        elif is_transliterated:
            primary_lang = detected_languages[0] if detected_languages else "hi-Latn"
            conf = 0.88
        elif en_matches:
            primary_lang = "en"
            conf = 0.94
        else:
            # Ambiguous or unknown tokens (Scenario 59: Low language confidence)
            # Example: "asdf qwer zxcv 1234"
            primary_lang = "unknown"
            conf = 0.40

        # Dynamic config threshold check
        cfg_threshold = dynamic_config.get_config().sentiment_thresholds.get("low_confidence_threshold", 0.60)
        low_confidence = conf < cfg_threshold

        clarification_prompt = None
        if low_confidence:
            clarification_prompt = (
                "We could not clearly determine your preferred language. "
                "Could you please clarify your request, or choose from English, Hindi, Kannada, Spanish, French, or German?"
            )

        return LanguageDetectionResult(
            primary_language=primary_lang,
            confidence=conf,
            is_mixed_language=is_mixed,
            detected_languages=detected_languages or ([primary_lang] if primary_lang != "unknown" else []),
            is_transliterated=is_transliterated,
            protected_entities=locks,
            sanitized_text=locked_text,
            low_confidence=low_confidence,
            clarification_prompt=clarification_prompt
        )


language_processor = LanguageProcessor()
