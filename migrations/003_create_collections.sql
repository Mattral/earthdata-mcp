-- Enable PostGIS extension for spatial operations
CREATE EXTENSION IF NOT EXISTS postgis;

-- Collections metadata table
CREATE TABLE IF NOT EXISTS collections (
    concept_id VARCHAR(100) PRIMARY KEY,

    -- Temporal extent
    temporal_start TIMESTAMPTZ,
    temporal_end TIMESTAMPTZ,
    is_ongoing BOOLEAN DEFAULT FALSE,

    -- Spatial extent
    spatial_extent GEOMETRY(GEOMETRY, 4326),
    is_global BOOLEAN DEFAULT FALSE,

    -- Raw CMR metadata (immutable)
    metadata JSONB NOT NULL,

    -- Enriched version (derived from metadata, schema compliant)
    enriched_metadata JSONB NOT NULL
);

-- Temporal filtering
CREATE INDEX IF NOT EXISTS idx_collections_temporal
    ON collections (temporal_start, temporal_end);

CREATE INDEX IF NOT EXISTS idx_collections_ongoing
    ON collections (is_ongoing);

-- Spatial filtering
CREATE INDEX IF NOT EXISTS idx_collections_spatial
    ON collections USING GIST (spatial_extent);

CREATE INDEX IF NOT EXISTS idx_collections_global
    ON collections (is_global);

-- JSONB indexes
CREATE INDEX IF NOT EXISTS idx_collections_metadata
    ON collections USING GIN (metadata);

CREATE INDEX IF NOT EXISTS idx_collections_enriched
    ON collections USING GIN (enriched_metadata);
