from sqlalchemy import text
from app.core.db import SessionLocal


def get_templates_by_ids(template_ids: list[str]) -> list[dict]:
    if not template_ids:
        return []

    session = SessionLocal()
    try:
        sql = text("""
            SELECT template_id, strategy_name, category, description
            FROM strategy_template
            WHERE template_id = ANY(:template_ids)
              AND status = 'active'
            ORDER BY template_id;
        """)
        rows = session.execute(sql, {"template_ids": template_ids}).fetchall()

        return [
            {
                "template_id": r[0],
                "strategy_name": r[1],
                "category": r[2],
                "description": r[3],
            }
            for r in rows
        ]
    finally:
        session.close()