import os
from typing import List, Optional, Tuple
from pydantic import BaseModel
from app.core.dynamic_config import dynamic_config


class FileValidationResult(BaseModel):
    is_valid: bool
    status: str  # "APPROVED" or "QUARANTINED"
    reasons: List[str] = []
    detected_mime: Optional[str] = None


class FileValidator:
    """
    Validates uploaded files against security policy, size constraints,
    magic byte signatures, and MIME-type integrity (Section 8.5 / Scenario 51).
    """

    MAGIC_SIGNATURES = {
        "image/png": [b"\x89PNG\r\n\x1a\n"],
        "image/jpeg": [b"\xff\xd8\xff"],
        "application/pdf": [b"%PDF-"],
    }

    DANGEROUS_HEADERS = [
        (b"MZ", "Windows executable (DOS/PE) header detected"),
        (b"\x7fELF", "Linux ELF executable header detected"),
        (b"#!", "Shell script shebang detected"),
    ]

    @classmethod
    def validate_file(
        cls,
        file_name: str,
        content: bytes,
        declared_mime: Optional[str] = None
    ) -> FileValidationResult:
        cfg = dynamic_config.get_config()
        policy = cfg.file_policy
        max_bytes = policy.get("max_file_size_mb", 15) * 1024 * 1024
        allowed_mimes = policy.get("allowed_mime_types", ["image/png", "image/jpeg", "application/pdf"])

        reasons = []

        # 1. Size Check
        file_size = len(content)
        if file_size == 0:
            reasons.append("Empty file uploaded (0 bytes)")
            return FileValidationResult(is_valid=False, status="QUARANTINED", reasons=reasons)

        if file_size > max_bytes:
            reasons.append(
                f"File size {file_size / (1024*1024):.2f}MB exceeds configured limit of {policy.get('max_file_size_mb', 15)}MB"
            )

        # 2. Check for dangerous executable headers
        for d_header, desc in cls.DANGEROUS_HEADERS:
            if content.startswith(d_header):
                reasons.append(desc)

        # 3. Detect actual MIME via Magic Bytes
        detected_mime = None
        for mime, sigs in cls.MAGIC_SIGNATURES.items():
            for sig in sigs:
                if content.startswith(sig):
                    detected_mime = mime
                    break
            if detected_mime:
                break

        # Check extension against MIME
        ext = os.path.splitext(file_name.lower())[1]
        ext_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".pdf": "application/pdf",
        }
        expected_mime_from_ext = ext_map.get(ext)

        # If detected mime contradicts declared mime or extension
        if detected_mime and expected_mime_from_ext and detected_mime != expected_mime_from_ext:
            reasons.append(
                f"MIME type spoofing detected: Extension '{ext}' implies '{expected_mime_from_ext}' but header signature matches '{detected_mime}'"
            )

        # Check if MIME type is permitted in dynamic config
        effective_mime = detected_mime or declared_mime or expected_mime_from_ext
        if effective_mime not in allowed_mimes:
            reasons.append(
                f"MIME type '{effective_mime}' is not in allowed types: {allowed_mimes}"
            )

        is_valid = len(reasons) == 0
        return FileValidationResult(
            is_valid=is_valid,
            status="APPROVED" if is_valid else "QUARANTINED",
            reasons=reasons,
            detected_mime=effective_mime or "application/octet-stream"
        )


file_validator = FileValidator()
