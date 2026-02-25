CREATE TABLE IF NOT EXISTS enrichment_log (
    id SERIAL PRIMARY KEY,
    concept_id VARCHAR(100) NOT NULL,
    concept_type VARCHAR(20) NOT NULL,
    field_path TEXT NOT NULL,
    action VARCHAR(50) NOT NULL,
    old_value JSONB,
    new_value JSONB,
    error TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    FOREIGN KEY (concept_id) REFERENCES collections(concept_id) ON DELETE CASCADE
);

-- Index for looking up enrichment history for a concept
CREATE INDEX IF NOT EXISTS idx_enrichment_log_concept
    ON enrichment_log(concept_id);

-- Index for time-based queries
CREATE INDEX IF NOT EXISTS idx_enrichment_log_created
    ON enrichment_log(created_at);

-- Index for filtering by action type
CREATE INDEX IF NOT EXISTS idx_enrichment_log_action
    ON enrichment_log(action);
