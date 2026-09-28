import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="CUSTOMER")  # CUSTOMER, AGENT, ADMIN
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    conversations = relationship("Conversation", back_populates="customer", cascade="all, delete-orphan")
    tickets = relationship("SupportTicket", back_populates="customer", cascade="all, delete-orphan")
    agent_profile = relationship("Agent", back_populates="user", uselist=False, cascade="all, delete-orphan")


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tier = Column(String(50), default="tier_1")
    is_available = Column(Boolean, default=True)
    max_workload = Column(Integer, default=5)
    current_workload = Column(Integer, default=0)

    # Relationships
    user = relationship("User", back_populates="agent_profile")
    skills = relationship("AgentSkill", back_populates="agent", cascade="all, delete-orphan")
    assignments = relationship("TicketAssignment", back_populates="agent")


class AgentSkill(Base):
    __tablename__ = "agent_skills"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    skill_name = Column(String(100), nullable=False)  # e.g., 'payments', 'kannada', 'technical'
    proficiency = Column(Integer, default=1)

    agent = relationship("Agent", back_populates="skills")
