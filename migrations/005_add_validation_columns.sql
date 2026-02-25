ALTER TABLE collections ADD COLUMN IF NOT EXISTS is_valid BOOLEAN;

ALTER TABLE collections ADD COLUMN IF NOT EXISTS validation_state JSONB;

ALTER TABLE collections ADD COLUMN IF NOT EXISTS schema_version VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_collections_valid
    ON collections(is_valid);

CREATE INDEX IF NOT EXISTS idx_collections_validation_state
    ON collections USING GIN (validation_state);

CREATE INDEX IF NOT EXISTS idx_collections_schema_version
    ON collections(schema_version);
