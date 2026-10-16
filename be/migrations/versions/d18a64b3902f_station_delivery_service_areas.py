"""Delivery service areas; independently deployable alongside server-time changes."""
from alembic import op
import sqlalchemy as sa

revision = 'd18a64b3902f'
down_revision = '30ec3dbeb8ac'
branch_labels = ('delivery_coverage',)
depends_on = None


def upgrade():
    op.create_table('station_service_areas',
        sa.Column('id', sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column('station_id', sa.BigInteger(), nullable=False),
        sa.Column('province_id', sa.BigInteger(), nullable=False),
        sa.Column('city_id', sa.BigInteger(), nullable=True),
        sa.Column('district_id', sa.BigInteger(), nullable=True),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['station_id'], ['stations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['province_id'], ['provinces.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['district_id'], ['districts.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['city_id','province_id'], ['cities.id','cities.province_id'],
            name='fk_service_areas_city_province', ondelete='RESTRICT'))
    op.create_index('ix_service_areas_station', 'station_service_areas', ['station_id'])
    op.create_index('uq_service_areas_enabled_region', 'station_service_areas',
        ['province_id', sa.text('COALESCE(city_id, 0)'), sa.text('COALESCE(district_id, 0)')],
        unique=True, postgresql_where=sa.text('enabled'))


def downgrade():
    raise RuntimeError('Service coverage must be preserved; restore the pre-upgrade backup')
