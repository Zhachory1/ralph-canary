REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO flag_app;

CREATE TABLE flags (
    id UUID PRIMARY KEY,
    flag_key VARCHAR(128) NOT NULL UNIQUE,
    current_value BOOLEAN NOT NULL,
    current_version BIGINT NOT NULL CHECK (current_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE flag_history (
    id UUID PRIMARY KEY,
    flag_id UUID NOT NULL REFERENCES flags(id),
    flag_key VARCHAR(128) NOT NULL,
    value BOOLEAN NOT NULL,
    version BIGINT NOT NULL CHECK (version >= 1),
    actor_id TEXT NOT NULL,
    request_id UUID NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('create', 'update')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (flag_id, version)
);
CREATE INDEX ix_flag_history_flag_version ON flag_history (flag_id, version DESC);

CREATE FUNCTION reject_flag_history_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'flag history is append-only';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER flag_history_is_append_only
    BEFORE UPDATE OR DELETE ON flag_history
    FOR EACH ROW EXECUTE FUNCTION reject_flag_history_mutation();

REVOKE ALL ON TABLE flags, flag_history FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE flags TO flag_app;
GRANT SELECT, INSERT ON TABLE flag_history TO flag_app;
