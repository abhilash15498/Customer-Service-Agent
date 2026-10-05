from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
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
    Guarantees multi-tenant customer isolation and enforces session lifecycle policies:
    - 30-minute inactivity timeout (transitions to IDLE / RESTORED) (Scenario 63)
    - 24-hour conversation summary restoration window (Scenario 64)
    - Beyond 24 hours: starts fresh session without carrying over stale conversation
    - Controlled summary context injection rather than blindly dumping entire raw message histories
    - Safe multi-device and simultaneous customer sessions (Scenario 65)
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

            # Security rule: Customer session isolation (Scenario 65)
            if conv.customer_id != customer_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Unauthorized: Access to another customer's conversation is forbidden"
                )

            # Check inactivity timeout (Scenario 63 & 64)
            last_msg_at = conv.last_message_at
            if last_msg_at.tzinfo is None:
                last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)

            idle_duration = now - last_msg_at
            if idle_duration > inactivity_limit:
                # If within 24 hours, restore session with controlled summary
                if idle_duration <= restore_limit:
                    conv.status = "RESTORED"
                    # Generate concise summary if not yet present
                    await cls.generate_or_get_summary(db, conv)
                else:
                    # Past 24 hours -> close old session and start fresh (Scenario 64)
                    conv.status = "CLOSED"
                    new_conv = Conversation(
                        customer_id=customer_id,
                        status="ACTIVE",
                        last_message_at=now,
                        conversation_metadata={"previous_conversation_id": conv.id}
                    )
                    db.add(new_conv)
                    await db.flush()
                    return new_conv

            conv.last_message_at = now
            return conv

        # No conversation_id provided: check if recent active or restored conversation exists
        query = select(Conversation).where(
            Conversation.customer_id == customer_id,
            Conversation.status.in_(["ACTIVE", "RESTORED"])
        ).order_by(Conversation.last_message_at.desc())
        result = await db.execute(query)
        recent_conv = result.scalars().first()

        if recent_conv:
            recent_last_msg = recent_conv.last_message_at
            if recent_last_msg.tzinfo is None:
                recent_last_msg = recent_last_msg.replace(tzinfo=timezone.utc)
            idle_duration = now - recent_last_msg

            if idle_duration <= inactivity_limit:
                recent_conv.last_message_at = now
                return recent_conv
            elif idle_duration <= restore_limit:
                # Restore within 24 hours
                recent_conv.status = "RESTORED"
                recent_conv.last_message_at = now
                await cls.generate_or_get_summary(db, recent_conv)
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
        """Loads last N messages for context respecting configurable history limit (default 10)."""
        cfg = dynamic_config.get_config().session_management
        limit = cfg.get("history_message_limit", 10)

        query = select(Message).where(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at.desc()).limit(limit)

        result = await db.execute(query)
        messages = list(result.scalars().all())
        messages.reverse()  # Chronological order
        return messages

    @classmethod
    async def generate_or_get_summary(
        cls,
        db: AsyncSession,
        conversation: Conversation
    ) -> ConversationSummary:
        """
        Generates or fetches concise structured summary for restored sessions (Scenario 64),
        avoiding feeding 50+ raw messages to the LLM.
        """
        # Check if summary already exists
        query = select(ConversationSummary).where(
            ConversationSummary.conversation_id == conversation.id
        ).order_by(ConversationSummary.created_at.desc())
        result = await db.execute(query)
        existing = result.scalars().first()
        if existing:
            return existing

        # Load historical messages to build compact summary
        msg_query = select(Message).where(
            Message.conversation_id == conversation.id
        ).order_by(Message.created_at.asc())
        msg_result = await db.execute(msg_query)
        all_msgs = list(msg_result.scalars().all())

        if not all_msgs:
            summary_text = "Session restored with no prior messages."
            entities = {}
        else:
            cust_msgs = [m.content for m in all_msgs if m.sender_role == "CUSTOMER"]
            first_inquiry = cust_msgs[0] if cust_msgs else "Inquiry"
            total_count = len(all_msgs)
            entities = conversation.conversation_metadata.get("confirmed_entities", {})
            summary_text = (
                f"Restored session ({total_count} prior messages). "
                f"Customer initial inquiry: '{first_inquiry[:120]}'. "
                f"Confirmed entities: {entities if entities else 'None'}."
            )

        summary = ConversationSummary(
            conversation_id=conversation.id,
            summary_text=summary_text,
            entities=entities or {},
            created_at=Clock.now()
        )
        db.add(summary)
        await db.flush()
        return summary


session_manager = SessionManager()
