import enum
from time import timezone
from sqlalchemy import (
    Boolean, Column, Integer, String, DateTime, JSON, Float, ForeignKey, Enum,Numeric,text
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy import Column, Integer, String, Text, Date, Enum
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import validates
from sqlalchemy.sql import func
import pytz
IST = pytz.timezone("Asia/Kolkata")



Base = declarative_base()

# class DocType(str, enum.Enum):
#     SCANNED_IMAGE = "scanned_image"
#     NORMAL       = "normal"

class Status(str, enum.Enum):
    UPLOADED = "uploaded"
    VERIFIED = "verified"
    INJECTED = "injected"
    
class Document(Base):
    __tablename__ = "documents"
    
    id            = Column(Integer, primary_key=True, index=True)
    filename      = Column(String, nullable=False)
    meta_data     = Column("metadata", JSON, nullable=False)
    uploaded_time = Column(DateTime(timezone=True), default=lambda: datetime.now(pytz.timezone("Asia/Kolkata"), nullable=False))
    version       = Column(Integer, nullable=False, default=1)
    doc_type      = Column(String, nullable=False)
    status        = Column(Enum(Status), nullable=False, default=Status.UPLOADED)
    file_id       = Column(String, nullable=True)
    injected_time = Column(DateTime(timezone=True), nullable=True)
    thumbnail_path = Column(String, nullable=True)

    # use FK to drive_folders.id, not folder_id
    folder_id = Column(Integer, ForeignKey("drive_folders.id",ondelete="CASCADE"), nullable=True)
    
    tokens = relationship("Token", back_populates="document",cascade="all, delete-orphan",passive_deletes=True)
    folder = relationship("DriveFolder", back_populates="documents")



class DriveFolder(Base):
    __tablename__ = "drive_folders"

    id = Column(Integer, primary_key=True, index=True)

    folder_id = Column(String, unique=True, index=True)   # Google Drive folder id
    parent_drive_id = Column(String, nullable=True)       # Google parent folder id
    
    name = Column(String, nullable=False)

    # parent-child relationship (DB internal)
    parent_id = Column(Integer, ForeignKey("drive_folders.id"), nullable=True)

    parent = relationship("DriveFolder", remote_side=[id], backref="children")


    documents = relationship(
        "Document",
        back_populates="folder",
        cascade="all, delete-orphan",
        passive_deletes=True
    )




class Token(Base):
    __tablename__ = "tokens"
    
    id             = Column(Integer, primary_key=True, index=True)
    document_id    = Column(Integer, ForeignKey("documents.id",ondelete="CASCADE"), nullable=False)
    total_token    = Column(Float, nullable=False)
    page_wise_token= Column(JSON, nullable=False)
    chunk_count    = Column(Integer, nullable=False)
    cost           = Column(Float, nullable=False)
    
    document = relationship("Document", back_populates="tokens")


class ChatUser(Base):
    __tablename__ = "chat_users"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    chat_id = Column(String(12), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(pytz.timezone("Asia/Kolkata")))
    last_message_at = Column(
    DateTime(timezone=True),
    default=lambda: datetime.now(pytz.timezone("Asia/Kolkata")),
    onupdate=lambda: datetime.now(pytz.timezone("Asia/Kolkata"))
)

    # New token usage fields
    total_tokens = Column(Integer, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_cost      = Column(Numeric(18, 8), nullable=False, server_default=text("0"))

    history = relationship("ChatUserHistory", back_populates="user", uselist=False)
    feedbacks = relationship("Feedback", back_populates="chat_user", cascade="all, delete-orphan")


class ChatUserHistory(Base):
    __tablename__ = "chat_user_histories"
    
    id         = Column(Integer, primary_key=True, index=True)
    chat_id    = Column(String(12), ForeignKey("chat_users.chat_id"), nullable=False, unique=True)
    chat_history = Column(
        MutableList.as_mutable(JSON),
        nullable=False,
        default=list
    )
    
    user = relationship("ChatUser", back_populates="history")



class LLMConfig(Base):
    __tablename__ = "llm_configs"

    id = Column(Integer, primary_key=True, index=True)
    temperature = Column(Float, server_default=text("0.0"), nullable=False)
    frequency_penalty = Column(Float, server_default=text("0.0"), nullable=False)
    presence_penalty = Column(Float, server_default=text("0.0"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=True)

    
    
class FeedbackChoice(str, enum.Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"

class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime(timezone=True), default=lambda: datetime.now(pytz.timezone("Asia/Kolkata")),nullable=False,index=True)
    description = Column(Text, nullable=True)
    message_id = Column(String(64), nullable=True)
    chat_id = Column(String(12), ForeignKey("chat_users.chat_id"), nullable=False)

    source = Column(JSON, nullable=True)
    feed_choice = Column(SAEnum(FeedbackChoice, name="feedback_choice", native_enum=False), nullable=False)

    chat_user = relationship("ChatUser", back_populates="feedbacks")
    
    @validates("feed_choice")
    def set_default_description(self, key, value):
        """Auto-apply description when feedback is positive and no description given."""
        if value == FeedbackChoice.POSITIVE and not self.description:
            self.description = "positive feedback"
        return value


class ConfigFlag(Base):
    __tablename__ = "config_flags"

    id = Column(Integer, primary_key=True, index=True)
    last_triggered_count = Column(Integer, default=0, nullable=False)
    status = Column(Boolean, default=False, nullable=False)

