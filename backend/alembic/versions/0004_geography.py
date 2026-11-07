"""Nullable geography, version-bound map cache and retained simulation baseline."""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    for column in [
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("coordinate_system", sa.String(16), nullable=True),
        sa.Column("address", sa.String(200), nullable=True),
        sa.Column("coordinate_source", sa.String(32), nullable=True),
        sa.Column("coordinate_updated_at", sa.DateTime(timezone=True), nullable=True),
    ]:
        op.add_column("nodes", column)
    for column in [
        sa.Column("ready", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("node_versions", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("simulation_matrix", sa.JSON(), nullable=True),
        sa.Column("simulation_routing_version", sa.String(64), nullable=True),
    ]:
        op.add_column("routing_graphs", column)
    op.execute(
        sa.text(
            "UPDATE routing_graphs SET simulation_matrix = matrix, "
            "simulation_routing_version = routing_version WHERE source = 'SIMULATION'"
        )
    )
    op.create_table(
        "map_route_cache",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "cache_key"),
    )
    op.create_index("ix_map_route_cache_tenant_id", "map_route_cache", ["tenant_id"])


def downgrade():
    op.drop_table("map_route_cache")
    for column in ("simulation_routing_version", "simulation_matrix", "node_versions", "ready"):
        op.drop_column("routing_graphs", column)
    for column in (
        "coordinate_updated_at",
        "coordinate_source",
        "address",
        "coordinate_system",
        "latitude",
        "longitude",
    ):
        op.drop_column("nodes", column)
