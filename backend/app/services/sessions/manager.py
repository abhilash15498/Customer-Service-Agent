from datetime import timedelta
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.clock import Clock
from app.core.dynamic_config import dynamic_config
from app.models.conversation import Conversation, ConversationSummary, Message
from app.models.user import User


class SessionManager:
    """
    Guarantees tenant/customer isolation and enforces lifecycle policies:
    - 30-minute inactivity timeout
    - 24-hour conversation summary restoration window
    - Context window limits (10 messages)
    """

    @classmethod
    async def get_or_create_conversation(
        cls,
        db: AsyncSession,
        customer_id: str,
        conversation_id: Optional[str] = None
    ) -> Conversation:
        now = Clock.now()
        cfg = dynamic_config.get_config().session_management
        inactivity_limit = timedelta(minutes=cfg["inactivity_timeout_minutes"])
        restore_limit = timedelta(hours=cfg["summary_restoration_hours"])

        if conversation_id:
            query = select(Conversation).where(
                Conversation.id == conversation_id
            ).options(
                selectinload(Conversation.messages),
                selectinload(Conversation.summaries)
            )
            result = await db.execute(query)
            conv = result.scalar_one_or_none()

            if not conv:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conversation not found"
                )

            # Security rule: Customer session isolation
            if conv.customer_id != customer_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Unauthorized: Access to another customer's conversation is forbidden"
                )

            # Check inactivity timeout
            last_msg_at = conv.last_message_at
            if last_msg_at.tzinfo is None:
                from datetime import timezone
                last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)

            idle_duration = now - last_msg_at
            if idle_duration > inactivity_limit:
                conv.status = "IDLE"
                # If within 24 hours, session summary can be restored in a renewed conversation
                if idle_duration <= restore_limit:
                    # Mark existing as idle and allow continuation with summary
                    conv.status = "RESTORED"
                else:
                    # Past 24 hours -> start new session
                    new_conv = Conversation(customer_id=customer_id, status="ACTIVE", last_message_at=now)
                    db.add(new_conv)
                    await db.flush()
                    return new_conv

            conv.last_message_at = now
            return conv

        # No conversation_id provided: check if recent active conversation exists
        query = select(Conversation).where(
            Conversation.customer_id == customer_id,
            Conversation.status.in_(["ACTIVE", "RESTORED"])
        ).order_by(Conversation.last_message_at.desc())
        result = await db.execute(query)
        recent_conv = result.scalars().first()

        if recent_conv:
            recent_last_msg = recent_conv.last_message_at
            if recent_last_msg.tzinfo is None:
                from datetime import timezone
                recent_last_msg = recent_last_msg.replace(tzinfo=timezone.utc)
            if (now - recent_last_msg) <= inactivity_limit:
                recent_conv.last_message_at = now
                return recent_conv

        # Create fresh session
        new_conv = Conversation(customer_id=customer_id, status="ACTIVE", last_message_at=now)
        db.add(new_conv)
        await db.flush()
        return new_conv

    @classmethod
    async def load_conversation_context(
        cls,
        db: AsyncSession,
        conversation_id: str
    ) -> List[Message]:
        """Loads last N messages for context respecting configurable history limit."""
        cfg = dynamic_config.get_config().session_management
        limit = cfg.get("history_message_limit", 10)

        query = select(Message).where(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at.desc()).limit(limit)

        result = await db.execute(query)
        messages = list(result.scalars().all())
        messages.reverse()  # Return in chronological order
        return messages


session_manager = SessionManager()
