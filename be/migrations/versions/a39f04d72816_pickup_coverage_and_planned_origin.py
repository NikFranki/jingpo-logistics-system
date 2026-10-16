"""Separate pickup coverage and preserve shipment planned origins."""
from alembic import op
import sqlalchemy as sa

revision = 'a39f04d72816'
down_revision = 'd18a64b3902f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('station_service_areas', sa.Column('purpose', sa.String(16), nullable=False,
                                                   server_default='DELIVERY'))
    op.create_check_constraint('ck_service_areas_purpose', 'station_service_areas',
                               "purpose IN ('DELIVERY','PICKUP')")
    op.drop_index('uq_service_areas_enabled_region', table_name='station_service_areas')
    op.create_index('uq_service_areas_enabled_region', 'station_service_areas',
        ['purpose', 'province_id', sa.text('COALESCE(city_id, 0)'), sa.text('COALESCE(district_id, 0)')],
        unique=True, postgresql_where=sa.text('enabled'))
    op.add_column('shipments', sa.Column('planned_origin_station_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_shipments_planned_origin', 'shipments', 'stations',
                          ['planned_origin_station_id'], ['id'], ondelete='RESTRICT')


def downgrade():
    raise RuntimeError('Restore the pre-upgrade backup to preserve planned origins and pickup coverage')
