"""Portable column types: pgvector on PostgreSQL, JSON elsewhere."""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator, TypeEngine


class EmbeddingVector(TypeDecorator):
    impl = JSON
    cache_ok = True

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect) -> TypeEngine:  # noqa: ANN001
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return [float(x) for x in value]


def cosine_distance(column, vector: list[float]):  # noqa: ANN001, ANN201
    """pgvector cosine distance (`<=>`) usable on EmbeddingVector columns (PostgreSQL only)."""
    from sqlalchemy import Float, bindparam

    return column.op("<=>", return_type=Float)(bindparam(None, vector, type_=Vector(len(vector))))
