from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATA_DIR

DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DATA_DIR / 'vibe.db'}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema():
    """Lightweight migration for pre-existing databases.

    create_all() only creates missing tables, so a database made before
    a column existed needs ALTER TABLE. This is idempotent: it checks
    PRAGMA table_info first and only adds what is missing. It also
    backfills a username for every existing user that has none (from
    the email local-part, uniquified), so no data is wiped.
    """
    from sqlalchemy import inspect, text

    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.begin() as conn:
        if "username" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(20)"))
        if "avatar_path" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN avatar_path VARCHAR(1024)"))
        conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users(username)")
        )

    # Disappearing messages: per-conversation timer + per-message expiry.
    conv_cols = {c["name"] for c in inspect(engine).get_columns("conversations")}
    with engine.begin() as conn:
        if "disappearing_seconds" not in conv_cols:
            conn.execute(
                text("ALTER TABLE conversations ADD COLUMN disappearing_seconds INTEGER NOT NULL DEFAULT 0")
            )
        if "is_group" not in conv_cols:
            conn.execute(
                text("ALTER TABLE conversations ADD COLUMN is_group INTEGER NOT NULL DEFAULT 0")
            )
        if "name" not in conv_cols:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN name VARCHAR(100)"))
    msg_cols = {c["name"] for c in inspect(engine).get_columns("messages")}
    with engine.begin() as conn:
        if "expires_at" not in msg_cols:
            conn.execute(text("ALTER TABLE messages ADD COLUMN expires_at DATETIME"))

    # Backfill usernames for rows that have none.
    # Local import: helpers imports models, which imports this module.
    from . import helpers, models

    db = SessionLocal()
    try:
        missing = (
            db.query(models.User).filter(models.User.username.is_(None)).all()
        )
        if not missing:
            return
        for user in missing:
            local_part = (user.email or "").split("@")[0]
            user.username = helpers.generate_unique_username(db, local_part)
        db.commit()
    finally:
        db.close()
