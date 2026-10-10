"""Tests for new datasource dialects: kingbase, gaussdb, clickhouse."""

from app.core.prompt import dialect_rules
from app.core.sql.dialect import Dialect


class TestNewDialectNames:
    """Test that new dialects are correctly named and recognized."""

    def test_dialect_enum_values(self):
        """All new dialects should be enum values."""
        assert hasattr(Dialect, "KINGBASE")
        assert hasattr(Dialect, "GAUSSDB")
        assert hasattr(Dialect, "CLICKHOUSE")

    def test_dialect_from_name_kingbase(self):
        """Dialect.from_name should recognize 'kingbase'."""
        assert Dialect.from_name("kingbase") == Dialect.KINGBASE

    def test_dialect_from_name_gaussdb(self):
        """Dialect.from_name should recognize 'gaussdb'."""
        assert Dialect.from_name("gaussdb") == Dialect.GAUSSDB

    def test_dialect_from_name_clickhouse(self):
        """Dialect.from_name should recognize 'clickhouse'."""
        assert Dialect.from_name("clickhouse") == Dialect.CLICKHOUSE


class TestDialectSqlglotMapping:
    """Test sqlglot mapping for dialects."""

    def test_kingbase_maps_to_postgres(self):
        """Kingbase should map to postgres for sqlglot."""
        assert Dialect.KINGBASE.sqlglot == "postgres"

    def test_gaussdb_maps_to_postgres(self):
        """GaussDB should map to postgres for sqlglot."""
        assert Dialect.GAUSSDB.sqlglot == "postgres"

    def test_clickhouse_maps_to_clickhouse(self):
        """ClickHouse should map to clickhouse for sqlglot."""
        assert Dialect.CLICKHOUSE.sqlglot == "clickhouse"

    def test_mysql_unchanged(self):
        """MySQL mapping should not change."""
        assert Dialect.MYSQL.sqlglot == "mysql"

    def test_postgres_unchanged(self):
        """PostgreSQL mapping should not change."""
        assert Dialect.POSTGRES.sqlglot == "postgres"


class TestDialectPromptRules:
    """Test that dialect rules can be loaded and rendered."""

    def test_kingbase_rules_render(self):
        """Kingbase dialect rules should render without error."""
        rules = dialect_rules("kingbase")
        assert rules is not None
        assert len(rules) > 0
        assert "KingbaseES" in rules or "double quotes" in rules

    def test_gaussdb_rules_render(self):
        """GaussDB dialect rules should render without error."""
        rules = dialect_rules("gaussdb")
        assert rules is not None
        assert len(rules) > 0
        assert "GaussDB" in rules or "double quotes" in rules

    def test_clickhouse_rules_render(self):
        """ClickHouse dialect rules should render without error."""
        rules = dialect_rules("clickhouse")
        assert rules is not None
        assert len(rules) > 0
        assert "ClickHouse" in rules or "backticks" in rules

    def test_mysql_rules_still_render(self):
        """MySQL rules should still work."""
        rules = dialect_rules("mysql")
        assert rules is not None
        assert len(rules) > 0

    def test_postgresql_rules_still_render(self):
        """PostgreSQL rules should still work."""
        rules = dialect_rules("postgresql")
        assert rules is not None
        assert len(rules) > 0


class TestDialectPromptNames:
    """Test prompt_name property in DialectAdapter."""

    def test_kingbase_prompt_name(self):
        """Kingbase should have 'kingbase' as prompt name."""
        from app.core.sql.dialect import DialectAdapter

        adapter = DialectAdapter(Dialect.KINGBASE)
        assert adapter.prompt_name == "kingbase"

    def test_gaussdb_prompt_name(self):
        """GaussDB should have 'gaussdb' as prompt name."""
        from app.core.sql.dialect import DialectAdapter

        adapter = DialectAdapter(Dialect.GAUSSDB)
        assert adapter.prompt_name == "gaussdb"

    def test_clickhouse_prompt_name(self):
        """ClickHouse should have 'clickhouse' as prompt name."""
        from app.core.sql.dialect import DialectAdapter

        adapter = DialectAdapter(Dialect.CLICKHOUSE)
        assert adapter.prompt_name == "clickhouse"


class TestDialectEngineNames:
    """Test engine_name property in DialectAdapter."""

    def test_kingbase_engine_name(self):
        """Kingbase should have 'KingbaseES' as engine name."""
        from app.core.sql.dialect import DialectAdapter

        adapter = DialectAdapter(Dialect.KINGBASE)
        assert adapter.engine_name == "KingbaseES"

    def test_gaussdb_engine_name(self):
        """GaussDB should have 'GaussDB' as engine name."""
        from app.core.sql.dialect import DialectAdapter

        adapter = DialectAdapter(Dialect.GAUSSDB)
        assert adapter.engine_name == "GaussDB"

    def test_clickhouse_engine_name(self):
        """ClickHouse should have 'ClickHouse' as engine name."""
        from app.core.sql.dialect import DialectAdapter

        adapter = DialectAdapter(Dialect.CLICKHOUSE)
        assert adapter.engine_name == "ClickHouse"


class TestDialectTypeNames:
    """Test type_name property for different dialects."""

    def test_clickhouse_types_differ_from_postgres(self):
        """ClickHouse should use different type names."""
        from app.core.sql.dialect import DialectAdapter

        ch_adapter = DialectAdapter(Dialect.CLICKHOUSE)
        pg_adapter = DialectAdapter(Dialect.POSTGRES)

        # ClickHouse uses Int64, Float64, String, etc.
        assert ch_adapter.type_name("integer") == "Int64"
        assert ch_adapter.type_name("number") == "Float64"
        assert ch_adapter.type_name("text") == "String"

        # PostgreSQL uses BIGINT, DOUBLE PRECISION, TEXT, etc.
        assert pg_adapter.type_name("integer") == "BIGINT"
        assert pg_adapter.type_name("number") == "DOUBLE PRECISION"
        assert pg_adapter.type_name("text") == "TEXT"

    def test_kingbase_types_match_postgres(self):
        """Kingbase types should match PostgreSQL."""
        from app.core.sql.dialect import DialectAdapter

        kb_adapter = DialectAdapter(Dialect.KINGBASE)
        pg_adapter = DialectAdapter(Dialect.POSTGRES)

        assert kb_adapter.type_name("integer") == pg_adapter.type_name("integer")
        assert kb_adapter.type_name("number") == pg_adapter.type_name("number")
        assert kb_adapter.type_name("text") == pg_adapter.type_name("text")

    def test_gaussdb_types_match_postgres(self):
        """GaussDB types should match PostgreSQL."""
        from app.core.sql.dialect import DialectAdapter

        gd_adapter = DialectAdapter(Dialect.GAUSSDB)
        pg_adapter = DialectAdapter(Dialect.POSTGRES)

        assert gd_adapter.type_name("integer") == pg_adapter.type_name("integer")
        assert gd_adapter.type_name("number") == pg_adapter.type_name("number")
        assert gd_adapter.type_name("text") == pg_adapter.type_name("text")
