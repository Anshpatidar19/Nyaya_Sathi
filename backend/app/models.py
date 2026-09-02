import datetime

from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    preferred_language = Column(String, default="en")
    state = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    queries = relationship("QueryLog", back_populates="user")


class QueryLog(Base):
    """Every question a user asks, and the answer that was returned.

    Kept so a user can see their own history, and so we can see which
    questions the retrieval layer is weak on.
    """

    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question = Column(Text, nullable=False)
    answer_title = Column(String, nullable=True)
    answer_body = Column(Text, nullable=True)
    citations_json = Column(Text, nullable=True)  # JSON-encoded list of {title, source, docid}
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="queries")
