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
    ConversationCreate,
    ConversationRead,
    ConversationSummaryRead,
    MessageCreate,
    MessageRead,
    MessageResponse,
    SentimentRead,
)
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

    # 2. Store customer message
    user_msg = Message(
        conversation_id=conv.id,
        sender_role="CUSTOMER",
        content=message_in.content,
        masked_content=masked_user_content,
        created_at=now
    )
    db.add(user_msg)
    await db.flush()

    # 3. Load rolling context (up to 10 messages)
    history = await session_manager.load_conversation_context(db, conv.id)
    llm_messages = [{"role": "user" if m.sender_role == "CUSTOMER" else "assistant", "content": m.masked_content} for m in history]

    # 4. Generate AI response via configurable provider
    llm = get_llm_provider()
    system_prompt = (
        "You are an empathetic, professional AI customer service assistant. "
        "Provide direct, helpful assistance based strictly on verified policy and facts."
    )
    assistant_text = await llm.generate_response(llm_messages, system_prompt=system_prompt)
    masked_assistant_content = masker.mask_text(assistant_text)

    # 5. Store assistant message
    asst_msg = Message(
        conversation_id=conv.id,
        sender_role="ASSISTANT",
        content=assistant_text,
        masked_content=masked_assistant_content,
        created_at=Clock.now()
    )
    db.add(asst_msg)

    # Update conversation last activity timestamp
    conv.last_message_at = Clock.now()
    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(asst_msg)

    return MessageResponse(
        user_message=MessageRead.model_validate(user_msg),
        assistant_message=MessageRead.model_validate(asst_msg),
        escalated=False,
        citations=[]
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
