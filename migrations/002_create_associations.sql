-- Unified associations table for linking related entities:
-- - Concept to concept (collection → variable, collection → citation)
-- - Concept to KMS term (collection → instrument, collection → platform)
CREATE TABLE IF NOT EXISTS associations (
    left_type VARCHAR(50) NOT NULL,
    left_id VARCHAR(100) NOT NULL,
    right_type VARCHAR(50) NOT NULL,
    right_id VARCHAR(100) NOT NULL,
    PRIMARY KEY (left_id, right_id)
);

-- Index for reverse lookups (right → left)
CREATE INDEX IF NOT EXISTS idx_associations_right
    ON associations(right_id, left_id);

-- Index for filtering by types
CREATE INDEX IF NOT EXISTS idx_associations_left_type
    ON associations(left_type, left_id);

CREATE INDEX IF NOT EXISTS idx_associations_right_type
    ON associations(right_type, right_id);
