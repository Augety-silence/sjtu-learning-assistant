"""Minimal SQLAlchemy hook for the default SQLite desktop runtime."""

hiddenimports = [
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    "sqlalchemy.ext.baked",
    "sqlalchemy.sql.default_comparator",
]
excludedimports = ["sqlalchemy.testing"]
