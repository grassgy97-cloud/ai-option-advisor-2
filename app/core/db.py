from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import DATABASE_URL

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_connection():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT current_database(), current_user;"))
        return result.fetchone()