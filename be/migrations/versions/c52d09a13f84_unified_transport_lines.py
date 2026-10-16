"""Expose existing full paths and direct segments through one line catalog."""
from alembic import op
import sqlalchemy as sa

revision = 'c52d09a13f84'
down_revision = 'a39f04d72816'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('path_plan_legs', sa.Column('travel_override_minutes', sa.Integer(), nullable=True))
    op.create_check_constraint('ck_path_plan_legs_travel', 'path_plan_legs',
                               'travel_override_minutes BETWEEN 1 AND 525600')
    connection = op.get_bind()
    rows = connection.execute(sa.text('''
        SELECT r.id, r.origin_station_id, r.destination_station_id, r.enabled,
               left(o.name || ' → ' || d.name, 100) AS name
        FROM transport_routes r JOIN stations o ON o.id=r.origin_station_id
        JOIN stations d ON d.id=r.destination_station_id
        WHERE NOT EXISTS (
            SELECT 1 FROM path_plan_legs l WHERE l.route_id=r.id
            AND (SELECT count(*) FROM path_plan_legs x WHERE x.plan_id=l.plan_id)=1)
        ORDER BY r.id
    ''')).mappings().all()
    for row in rows:
        line_id = connection.execute(sa.text('''
            INSERT INTO path_plans (code,name,enabled,origin_station_id,destination_station_id)
            VALUES (:code,:name,:enabled,:origin_station_id,:destination_station_id) RETURNING id
        '''), dict(row) | {'code': f'L_SEG_{row["id"]}'}).scalar_one()
        connection.execute(sa.text('INSERT INTO path_plan_legs (plan_id,position,route_id) VALUES (:p,0,:r)'),
                           {'p': line_id, 'r': row['id']})


def downgrade():
    raise RuntimeError('Unified line configuration must be preserved; restore the pre-upgrade backup')
