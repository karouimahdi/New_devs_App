from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any
from zoneinfo import ZoneInfo

CENTS = Decimal('0.01')

def _to_money_str(value: Decimal) -> str:
    """Round a DB-exact Decimal to currency precision exactly once."""
    return str(value.quantize(CENTS, rounding=ROUND_HALF_UP))

async def calculate_monthly_revenue(property_id: str, tenant_id: str, month: int, year: int) -> Dict[str, Any]:
    """
    Calculates revenue for a specific month.

    A reservation counts toward the month of its check-in date in the property's local timezone, regardless of UTC or the caller's timezone. 
    For example, a check-in at 2024-02-29 23:30 UTC in Europe/Paris is March 1 at 00:30 local time, so it counts toward March.
    """
    from app.core.database_pool import db_pool
    from sqlalchemy import text

    await db_pool.initialize()
    if not db_pool.session_factory:
        raise Exception("Database pool not available")

    async with db_pool.get_session() as session:
        tz_result = await session.execute(
            text("SELECT timezone FROM properties WHERE id = :property_id AND tenant_id = :tenant_id"),
            {"property_id": property_id, "tenant_id": tenant_id},
        )
        tz_row = tz_result.fetchone()
        if not tz_row:
            raise Exception(f"Unknown property {property_id} for tenant {tenant_id}")
        tz = ZoneInfo(tz_row.timezone)

        # Month boundary in the property's local time, passed to Postgres
        # as timezone-aware values so the comparison against the
        # timestamptz column happens on the correct absolute instants.
        start_date = datetime(year, month, 1, tzinfo=tz)
        end_date = datetime(year + 1, 1, 1, tzinfo=tz) if month == 12 else datetime(year, month + 1, 1, tzinfo=tz)

        query = text("""
            SELECT SUM(total_amount) AS total, COUNT(*) AS count
            FROM reservations
            WHERE property_id = :property_id
              AND tenant_id = :tenant_id
              AND check_in_date >= :start_date
              AND check_in_date < :end_date
        """)
        result = await session.execute(query, {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "start_date": start_date,
            "end_date": end_date,
        })
        row = result.fetchone()

        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": _to_money_str(Decimal(str(row.total))) if row and row.total is not None else "0.00",
            "currency": "USD",
            "count": row.count if row else 0,
        }

async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates revenue from database.
    """
    try:
        # Import database pool
        from app.core.database_pool import DatabasePool
        
        # Initialize pool if needed
        db_pool = DatabasePool()
        await db_pool.initialize()
        
        if db_pool.session_factory:
            async with db_pool.get_session() as session:
                # Use SQLAlchemy text for raw SQL
                from sqlalchemy import text
                
                query = text("""
                    SELECT 
                        property_id,
                        SUM(total_amount) as total_revenue,
                        COUNT(*) as reservation_count
                    FROM reservations 
                    WHERE property_id = :property_id AND tenant_id = :tenant_id
                    GROUP BY property_id
                """)
                
                result = await session.execute(query, {
                    "property_id": property_id, 
                    "tenant_id": tenant_id
                })
                row = result.fetchone()
                
                if row:
                    total_revenue = Decimal(str(row.total_revenue))
                    return {
                        "property_id": property_id,
                        "tenant_id": tenant_id,
                        "total": _to_money_str(total_revenue),
                        "currency": "USD",
                        "count": row.reservation_count
                    }
                else:
                    # No reservations found for this property
                    return {
                        "property_id": property_id,
                        "tenant_id": tenant_id,
                        "total": "0.00",
                        "currency": "USD",
                        "count": 0
                    }
        else:
            raise Exception("Database pool not available")
            
    except Exception as e:
        print(f"Database error for {property_id} (tenant: {tenant_id}): {e}")
        
        # Create property-specific mock data for testing when DB is unavailable
        # This ensures each property shows different figures
        mock_data = {
            'prop-001': {'total': '1000.00', 'count': 3},
            'prop-002': {'total': '4975.50', 'count': 4}, 
            'prop-003': {'total': '6100.50', 'count': 2},
            'prop-004': {'total': '1776.50', 'count': 4},
            'prop-005': {'total': '3256.00', 'count': 3}
        }
        
        mock_property_data = mock_data.get(property_id, {'total': '0.00', 'count': 0})
        
        return {
            "property_id": property_id,
            "tenant_id": tenant_id, 
            "total": mock_property_data['total'],
            "currency": "USD",
            "count": mock_property_data['count']
        }
