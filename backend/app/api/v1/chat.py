import re
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.deps import get_current_customer, get_current_user, get_db
from app.core.clock import Clock
from app.core.llm_provider import get_llm_provider
from app.core.masking import masker
from app.models.conversation import Conversation, ConversationSummary, Message
from app.models.sentiment import SentimentAnalysis
from app.models.user import User
from app.schemas.chat import (
    CitationRead,
    ConversationCreate,
    ConversationRead,
    ConversationSummaryRead,
    MessageCreate,
    MessageRead,
    MessageResponse,
    SentimentRead,
)
from app.services.escalation.engine import escalation_engine
from app.services.rag.generator import rag_generator
from app.services.sentiment.analyzer import sentiment_analyzer
from app.services.sentiment.tone import tone_adapter
from app.services.sessions.correction import correction_detector
from app.services.sessions.intent import intent_classifier
from app.services.sessions.language import language_processor
from app.services.sessions.manager import session_manager

router = APIRouter(prefix="/conversations", tags=["Chat & Conversations"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    conv_in: Optional[ConversationCreate] = None,
    current_user: User = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db)
):
    """Explicitly create a new isolated conversation session for the customer."""
    now = Clock.now()
    conv = Conversation(
        customer_id=current_user.id,
        status="ACTIVE",
        last_message_at=now,
        conversation_metadata=conv_in.metadata if conv_in and conv_in.metadata else {}
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


@router.get("", response_model=List[ConversationRead])
async def list_customer_conversations(
    current_user: User = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db)
):
    """List all conversations owned by the authenticated customer (Tenant isolation)."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.customer_id == current_user.id)
        .order_by(Conversation.last_message_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{id}", response_model=ConversationRead)
async def get_conversation(
    id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve conversation details with strict customer isolation check."""
    result = await db.execute(select(Conversation).where(Conversation.id == id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    # If customer, prevent accessing other users' conversations
    if current_user.role == "CUSTOMER" and conv.customer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Cannot view another customer's conversation"
        )

    return conv


@router.get("/{id}/messages", response_model=List[MessageRead])
async def get_conversation_messages(
    id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve all messages in a conversation with customer isolation check."""
    await get_conversation(id, current_user, db)  # Validates access
    messages = await session_manager.load_conversation_context(db, id)
    return messages


@router.post("/{id}/messages", response_model=MessageResponse)
async def post_message(
    id: str,
    message_in: MessageCreate,
    current_user: User = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db)
):
    """
    Post a customer message into an active conversation.
    Applies PII masking, loads rolling context, queries configurable LLM provider,
    and returns assistant response.
    """
    now = Clock.now()
    conv = await session_manager.get_or_create_conversation(db, current_user.id, conversation_id=id)

    # 1. Mask sensitive customer data (Credit cards, passwords, etc.)
    masked_user_content = masker.mask_text(message_in.content)

    # 2. Language Detection & Entity Locking
    lang_res = language_processor.detect_language(message_in.content)

    # 3. Entity Tracking across turns
    meta = dict(conv.conversation_metadata or {})
    confirmed_entities = dict(meta.get("confirmed_entities", {}))
    for lock in lang_res.protected_entities:
        if lock.entity_type == "ORDER_ID":
            val = getattr(lock, "entity_value", None)
            if val:
                clean_id = val.strip()
            else:
                clean_id = re.sub(r"(?i)^(?:order|ordr|oder|order\s*#?|#|order\s*id:?|commande|pedido|bestellung)\s*[:#\-]?", "", lock.raw_text).strip()
            if clean_id:
                confirmed_entities["order_id"] = clean_id
        elif lock.entity_type == "AMOUNT":
            val = getattr(lock, "entity_value", None)
            clean_amt = (val or lock.raw_text).replace(",", "").strip()
            clean_amt = re.sub(r"(?i)[₹$€rs\.inrupeesdollars\s]", "", clean_amt).strip()
            try:
                confirmed_entities["amount"] = float(clean_amt)
            except ValueError:
                pass

    # 4. Self-Correction Detection (Scenario 62)
    corr_res = correction_detector.detect_correction(message_in.content, active_entities=confirmed_entities)
    if corr_res.has_correction:
        if corr_res.field and corr_res.updated_value:
            confirmed_entities[corr_res.field] = corr_res.updated_value
        meta["confirmed_entities"] = confirmed_entities
        hist = list(meta.get("correction_history", []))
        hist.append(corr_res.model_dump())
        meta["correction_history"] = hist
        conv.conversation_metadata = meta

    meta["confirmed_entities"] = confirmed_entities
    conv.conversation_metadata = meta

    # 5. Intent Classification & Multi-Request Decomposition (Scenarios 60, 61)
    intent_res = intent_classifier.analyze_intent(message_in.content)

    # 6. Load rolling context (up to 10 messages) for conversational sentiment
    history = await session_manager.load_conversation_context(db, conv.id)
    past_customer_msgs = [m.content for m in history if m.sender_role == "CUSTOMER"]

    # 7. Context-aware Sentiment, Sarcasm & Risk Analysis
    sentiment_res = sentiment_analyzer.analyze_message(
        message=message_in.content,
        recent_history=past_customer_msgs
    )
    # Compound intent elevation: if intent classifier flags high-risk (e.g. duplicate payment)
    if intent_res.requires_escalation and not sentiment_res.risk_type:
        sentiment_res.risk_type = intent_res.primary_intent
        sentiment_res.urgency = "critical"

    # 8. Store customer message
    user_msg = Message(
        conversation_id=conv.id,
        sender_role="CUSTOMER",
        content=message_in.content,
        masked_content=masked_user_content,
        language_code=lang_res.primary_language,
        is_transliterated=lang_res.is_transliterated,
        created_at=now
    )
    db.add(user_msg)
    await db.flush()

    # 9. Store Sentiment Analysis Record
    sentiment_record = SentimentAnalysis(
        message_id=user_msg.id,
        sentiment=sentiment_res.sentiment,
        confidence=sentiment_res.confidence,
        urgency=sentiment_res.urgency,
        sarcasm=sentiment_res.sarcasm,
        risk_type=sentiment_res.risk_type,
        raw_scores=sentiment_res.raw_scores,
        created_at=now
    )
    db.add(sentiment_record)

    # 10. Generate AI response or deterministic safe clarification
    citations_data = []
    if lang_res.low_confidence and lang_res.clarification_prompt:
        # Scenario 59: Low language confidence clarification
        assistant_text = lang_res.clarification_prompt
    elif corr_res.has_correction and corr_res.confirmation_message:
        # Scenario 62: Self-correction confirmation
        assistant_text = corr_res.confirmation_message
    elif intent_res.low_confidence and intent_res.clarification_prompt:
        # Scenario 60: Low intent confidence clarification
        assistant_text = intent_res.clarification_prompt
    else:
        # Standard RAG / Conversational answering
        rag_result = await rag_generator.answer_query(
            db=db,
            query=user_msg.content,
            user_role=current_user.role,
            top_k=3
        )
        if not rag_result.refused and rag_result.citations:
            assistant_text = rag_result.answer
            citations_data = [
                CitationRead(
                    document=c["document"],
                    version=c["version"],
                    section=c.get("section")
                )
                for c in rag_result.citations
            ]
        else:
            # Fallback to standard conversational response with tone adaptation
            llm = get_llm_provider()
            tone_instruction = tone_adapter.get_system_directive(
                sentiment=sentiment_res.sentiment,
                risk_type=sentiment_res.risk_type
            )
            # Inject restored summary if session was restored (Scenario 64)
            summary_context = ""
            if conv.status == "RESTORED" and conv.summaries:
                summary_context = f"\n[Restored Session Summary: {conv.summaries[-1].summary_text}]\n"

            system_prompt = (
                "You are an empathetic, professional AI customer service assistant. "
                "Provide direct, helpful assistance based strictly on verified policy and facts.\n"
                f"{summary_context}"
                f"{tone_instruction}"
            )
            llm_messages = [{"role": "user" if m.sender_role == "CUSTOMER" else "assistant", "content": m.masked_content} for m in history]
            llm_messages.append({"role": "user", "content": user_msg.masked_content})
            assistant_text = await llm.generate_response(llm_messages, system_prompt=system_prompt)

    masked_assistant_content = masker.mask_text(assistant_text)

    # 11. Store assistant message
    asst_msg = Message(
        conversation_id=conv.id,
        sender_role="ASSISTANT",
        content=assistant_text,
        masked_content=masked_assistant_content,
        language_code=lang_res.primary_language,
        created_at=Clock.now()
    )
    db.add(asst_msg)

    # Update conversation last activity timestamp
    conv.last_message_at = Clock.now()

    # 12. Evaluate Deterministic Escalation Triggers
    esc_decision = escalation_engine.evaluate_triggers(
        sentiment_res=sentiment_res,
        user_message=message_in.content
    )
    esc_result = None
    if esc_decision.should_escalate:
        esc_result = await escalation_engine.execute_escalation(
            db=db,
            conversation=conv,
            decision=esc_decision,
            summary_text=message_in.content
        )

    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(asst_msg)

    user_msg_read = MessageRead(
        id=user_msg.id,
        conversation_id=user_msg.conversation_id,
        sender_role=user_msg.sender_role,
        content=user_msg.content,
        masked_content=user_msg.masked_content,
        language_code=user_msg.language_code,
        is_transliterated=user_msg.is_transliterated,
        created_at=user_msg.created_at,
        sentiment=SentimentRead(
            sentiment=sentiment_res.sentiment,
            confidence=sentiment_res.confidence,
            urgency=sentiment_res.urgency,
            sarcasm=sentiment_res.sarcasm,
            risk_type=sentiment_res.risk_type
        )
    )

    is_escalated = esc_result.escalated if esc_result else bool(sentiment_res.risk_type)
    esc_reason = esc_result.reason if esc_result else sentiment_res.risk_type
    ticket_id = esc_result.ticket_id if esc_result else None

    return MessageResponse(
        user_message=user_msg_read,
        assistant_message=MessageRead.model_validate(asst_msg),
        escalated=is_escalated,
        escalation_reason=esc_reason,
        ticket_id=ticket_id,
        citations=citations_data,
        detected_intents=intent_res.all_intents,
        is_compound_intent=intent_res.is_compound,
        language_detected=lang_res.primary_language,
        is_transliterated=lang_res.is_transliterated,
        is_mixed_language=lang_res.is_mixed_language,
        confirmed_entities=confirmed_entities
    )


@router.get("/{id}/summary", response_model=ConversationSummaryRead)
async def get_conversation_summary(
    id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve or generate conversation summary with masked context."""
    conv = await get_conversation(id, current_user, db)

    # Check for existing summary
    result = await db.execute(
        select(ConversationSummary)
        .where(ConversationSummary.conversation_id == conv.id)
        .order_by(ConversationSummary.created_at.desc())
    )
    summary = result.scalars().first()

    if not summary:
        # Generate initial summary from messages
        messages = await session_manager.load_conversation_context(db, conv.id)
        msg_texts = [f"{m.sender_role}: {m.masked_content}" for m in messages]
        combined = "\n".join(msg_texts) if msg_texts else "No messages in conversation."
        summary_text = masker.mask_text(f"Customer conversation summary:\n{combined}")

        summary = ConversationSummary(
            conversation_id=conv.id,
            summary_text=summary_text,
            entities={"customer_name": current_user.name}
        )
        db.add(summary)
        await db.commit()
        await db.refresh(summary)

    return summary
