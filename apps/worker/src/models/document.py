from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    storage_key: Mapped[str] = mapped_column(String, nullable=False)

    status: Mapped[str] = mapped_column(String, nullable=False)
