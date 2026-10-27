from pathlib import Path

from scripts.apply_sql import split_sql


def test_semicolons_inside_strings_comments_and_dollar_quotes_do_not_split():
    sql = """
    -- a comment; with a semicolon
    SELECT 'a;b';
    /* block; comment */
    CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $$
    BEGIN
        RETURN 1;   -- inner; semicolons
    END $$;
    SELECT 'it''s; fine';
    """
    parts = split_sql(sql)
    assert len(parts) == 3
    assert parts[1].rstrip().endswith("END $$")


def test_comment_only_chunks_are_dropped():
    assert split_sql("-- nothing here\n;\nSELECT 1;") == ["SELECT 1"]


def test_the_real_schema_file_splits_into_balanced_statements():
    sql = (Path(__file__).resolve().parents[2] / "database" / "simple.sql").read_text()
    parts = split_sql(sql)
    assert len(parts) > 100
    assert all(p.count("$$") % 2 == 0 for p in parts)
