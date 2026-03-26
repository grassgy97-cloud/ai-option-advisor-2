import json
from sqlalchemy import text
from app.core.db import SessionLocal


def run_simple_backtest(strategy_type: str, underlying_id: str, params: dict):
    session = SessionLocal()
    try:
        min_parity_dev = params.get("min_parity_deviation", 0.01)
        min_term_slope = params.get("min_term_slope", 0.02)

        if strategy_type == "parity_arb":
            query_sql = text("""
                SELECT parity_deviation
                FROM option_factor_snapshot
                WHERE underlying_id = :underlying_id
                  AND parity_deviation IS NOT NULL
                  AND ABS(parity_deviation) >= :threshold
            """)
            rows = session.execute(
                query_sql,
                {"underlying_id": underlying_id, "threshold": min_parity_dev}
            ).fetchall()

            values = [float(r[0]) for r in rows]
            metric_name = "parity_deviation"

        elif strategy_type == "calendar_arb":
            query_sql = text("""
                SELECT term_slope
                FROM option_factor_snapshot
                WHERE underlying_id = :underlying_id
                  AND term_slope IS NOT NULL
                  AND ABS(term_slope) >= :threshold
            """)
            rows = session.execute(
                query_sql,
                {"underlying_id": underlying_id, "threshold": min_term_slope}
            ).fetchall()

            values = [float(r[0]) for r in rows]
            metric_name = "term_slope"

        else:
            values = []
            metric_name = "unknown"

        sample_count = len(values)
        positive_count = len([x for x in values if x > 0])
        hit_ratio = (positive_count / sample_count) if sample_count > 0 else 0.0
        avg_value = (sum(values) / sample_count) if sample_count > 0 else 0.0
        max_drawdown = min(values) if values else 0.0

        insert_job_sql = text("""
            INSERT INTO backtest_job (strategy_type, underlying_id, param_json, status, note)
            VALUES (:strategy_type, :underlying_id, CAST(:param_json AS jsonb), :status, :note)
            RETURNING job_id;
        """)

        job_row = session.execute(
            insert_job_sql,
            {
                "strategy_type": strategy_type,
                "underlying_id": underlying_id,
                "param_json": json.dumps(params, ensure_ascii=False),
                "status": "finished",
                "note": "simple sample backtest"
            }
        ).fetchone()

        job_id = job_row[0]

        summary = {
            "metric_name": metric_name,
            "sample_count": sample_count,
            "positive_count": positive_count,
            "hit_ratio": hit_ratio,
            "avg_value": avg_value
        }

        insert_result_sql = text("""
            INSERT INTO backtest_result (
                job_id, sample_count, win_rate, avg_return, max_drawdown,
                avg_holding_days, summary_json
            ) VALUES (
                :job_id, :sample_count, :win_rate, :avg_return, :max_drawdown,
                :avg_holding_days, CAST(:summary_json AS jsonb)
            )
            RETURNING result_id;
        """)

        result_row = session.execute(
            insert_result_sql,
            {
                "job_id": job_id,
                "sample_count": sample_count,
                "win_rate": hit_ratio,
                "avg_return": avg_value,
                "max_drawdown": max_drawdown,
                "avg_holding_days": 1.0,
                "summary_json": json.dumps(summary, ensure_ascii=False)
            }
        ).fetchone()

        session.commit()

        return {
            "job_id": job_id,
            "result_id": result_row[0],
            "strategy_type": strategy_type,
            "underlying_id": underlying_id,
            "metric_name": metric_name,
            "sample_count": sample_count,
            "hit_ratio": hit_ratio,
            "avg_value": avg_value,
            "max_drawdown": max_drawdown,
            "summary": summary
        }

    finally:
        session.close()