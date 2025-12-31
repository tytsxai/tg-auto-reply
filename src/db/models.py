"""数据库模型定义"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    """用户表 - 存储 Telegram 用户信息"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String(100))
    first_name = Column(String(100))

    # 托管状态
    is_active = Column(Boolean, default=False)  # 是否启用托管
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联
    credentials = relationship("UserCredential", back_populates="user", uselist=False)
    settings = relationship("UserSettings", back_populates="user", uselist=False)
    logs = relationship("MessageLog", back_populates="user")


class UserCredential(Base):
    """用户凭证表 - 加密存储 Telegram API 凭证"""

    __tablename__ = "user_credentials"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)

    # 加密存储的凭证
    api_id_encrypted = Column(Text)
    api_hash_encrypted = Column(Text)
    phone_encrypted = Column(Text)
    session_string_encrypted = Column(Text)  # Telethon session

    # 状态
    is_logged_in = Column(Boolean, default=False)
    last_login = Column(DateTime)

    user = relationship("User", back_populates="credentials")


class UserSettings(Base):
    """用户设置表"""

    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)

    # AI 回复设置
    ai_enabled = Column(Boolean, default=True)
    ai_prompt = Column(Text, default="你是一个友好的助手，帮助用户回复消息。保持简洁自然。")
    reply_delay_seconds = Column(Integer, default=3)  # 模拟打字延迟

    # 过滤设置
    whitelist_only = Column(Boolean, default=False)  # 仅回复白名单
    blacklist_enabled = Column(Boolean, default=True)
    auto_reply_groups = Column(Boolean, default=False)  # 是否回复群组

    user = relationship("User", back_populates="settings")


class MessageLog(Base):
    """消息日志表 - 透明记录所有操作"""

    __tablename__ = "message_logs"
    __table_args__ = (
        Index("idx_message_logs_user_chat_created_at", "user_id", "chat_id", "created_at"),
        Index("idx_message_logs_user_created_at", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))

    # 消息信息
    chat_id = Column(Integer)
    chat_title = Column(String(200))
    sender_name = Column(String(200))
    original_message = Column(Text)
    ai_reply = Column(Text)

    # 状态
    status = Column(String(20))  # sent, failed, skipped
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="logs")


class ContactList(Base):
    """联系人名单 (白名单/黑名单)"""

    __tablename__ = "contact_lists"
    __table_args__ = (
        Index("idx_contact_lists_user_type_contact", "user_id", "list_type", "contact_id"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    contact_id = Column(Integer)  # Telegram user/chat ID
    contact_name = Column(String(200))
    list_type = Column(String(10))  # whitelist / blacklist
    created_at = Column(DateTime, default=datetime.utcnow)
