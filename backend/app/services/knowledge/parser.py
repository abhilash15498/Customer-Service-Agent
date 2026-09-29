import hashlib
import re
from typing import Any, Dict, List, Optional
from app.core.llm_provider import get_llm_provider


class DocumentChunk:
    def __init__(
        self,
        index: int,
        content: str,
        section: Optional[str] = None,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.index = index
        self.content = content
        self.section = section or "General"
        self.embedding = embedding or []
        self.metadata = metadata or {}


class DocumentParser:
    """
    Parses, splits, and computes vector embeddings for knowledge base articles.
    Computes SHA-256 content hashes for duplicate detection.
    """

    @classmethod
    def compute_sha256(cls, content: str) -> str:
        """Computes deterministic SHA-256 hash of document content."""
        return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()

    @classmethod
    async def parse_and_chunk(
        cls,
        title: str,
        content: str,
        chunk_size: int = 500,
        chunk_overlap: int = 50
    ) -> List[DocumentChunk]:
        """
        Splits document by Markdown headings / double newlines, creates overlapping chunks,
        and generates vector embeddings via configurable provider.
        """
        llm = get_llm_provider()
        chunks: List[DocumentChunk] = []

        # Split into sections based on Markdown headers (# or ##) or paragraphs
        sections = re.split(r"\n(?=#{1,3}\s+)", content)
        chunk_idx = 0

        for section_text in sections:
            section_text = section_text.strip()
            if not section_text:
                continue

            # Extract section heading
            heading_match = re.match(r"^(#{1,3}\s+)([^\n]+)", section_text)
            section_title = heading_match.group(2).strip() if heading_match else "General"

            # Clean content by removing the heading from body if desired, or keep intact
            paragraphs = section_text.split("\n\n")
            current_buffer = ""

            for p in paragraphs:
                p = p.strip()
                if not p:
                    continue

                if len(current_buffer) + len(p) > chunk_size and current_buffer:
                    # Finalize current chunk
                    embedding = await llm.generate_embedding(current_buffer)
                    chunks.append(
                        DocumentChunk(
                            index=chunk_idx,
                            content=current_buffer,
                            section=section_title,
                            embedding=embedding,
                            metadata={"doc_title": title, "section": section_title}
                        )
                    )
                    chunk_idx += 1
                    # Overlap: keep tail of buffer
                    overlap_len = min(chunk_overlap, len(current_buffer))
                    current_buffer = current_buffer[-overlap_len:] + " " + p
                else:
                    if current_buffer:
                        current_buffer += "\n\n" + p
                    else:
                        current_buffer = p

            if current_buffer.strip():
                embedding = await llm.generate_embedding(current_buffer)
                chunks.append(
                    DocumentChunk(
                        index=chunk_idx,
                        content=current_buffer,
                        section=section_title,
                        embedding=embedding,
                        metadata={"doc_title": title, "section": section_title}
                    )
                )
                chunk_idx += 1

        return chunks


document_parser = DocumentParser()
