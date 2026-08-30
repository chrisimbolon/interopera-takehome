// interopera-fim graph schema
// Run once against a fresh Neo4j instance before ingestion.

// --- Uniqueness constraints (also create supporting indexes) ---
CREATE CONSTRAINT position_id      IF NOT EXISTS FOR (p:Position)         REQUIRE p.instrument_id IS UNIQUE;
CREATE CONSTRAINT issuer_name      IF NOT EXISTS FOR (i:Issuer)           REQUIRE i.name IS UNIQUE;
CREATE CONSTRAINT assetclass_name  IF NOT EXISTS FOR (a:AssetClass)       REQUIRE a.name IS UNIQUE;
CREATE CONSTRAINT aggregate_name   IF NOT EXISTS FOR (g:Aggregate)        REQUIRE g.name IS UNIQUE;
CREATE CONSTRAINT riskmetric_name  IF NOT EXISTS FOR (m:RiskMetric)       REQUIRE m.name IS UNIQUE;
CREATE CONSTRAINT concap_name      IF NOT EXISTS FOR (c:ConcentrationCap) REQUIRE c.name IS UNIQUE;
CREATE CONSTRAINT chunk_id         IF NOT EXISTS FOR (c:SourceChunk)      REQUIRE c.chunk_id IS UNIQUE;
CREATE CONSTRAINT doc_id           IF NOT EXISTS FOR (d:SourceDocument)   REQUIRE d.doc_id IS UNIQUE;

// --- Property existence constraints on provenance (every domain node must be sourced) ---
// Enforced additionally at the application layer (Gate 1 schema validator) since Neo4j Community
// does not support arbitrary node-existence constraints across labels; Enterprise deployments
// should add per-label EXISTS constraints here, e.g.:
// CREATE CONSTRAINT position_sourced IF NOT EXISTS FOR (p:Position) REQUIRE p.instrument_id IS NOT NULL;

// --- Example node creation (illustrative — real ingestion is programmatic via graph/ingest.py) ---
// A source document + chunk:
// CREATE (:SourceDocument {doc_id: 'guidelines', filename: 'sample_fund_guidelines.pdf', version: '2.1'});
// CREATE (:SourceChunk {chunk_id: 'chunk_sec2_hy', page: 3, text_summary: 'High Yield Bonds: 0-15%, max BB+, APAC only',
//                        ingestion_time: datetime(), extraction_confidence: 0.97});

// --- Relationship types used throughout (for reference; created by ingest.py) ---
// (:Position)-[:BELONGS_TO]->(:AssetClass)
// (:Position)-[:ISSUED_BY]->(:Issuer)
// (:Issuer)-[:ROLLS_UP_TO]->(:Issuer)
// (:AssetClass)-[:CONTRIBUTES_TO]->(:Aggregate)
// (:RiskMetric)-[:TRIGGERS]->(:BreachAction)-[:OWNED_BY]->(:Owner)
// (:ConcentrationCap)-[:APPLIES_TO]->(:Issuer | :AssetClass)
// (<any domain node>)-[:SOURCED_FROM]->(:SourceChunk)-[:PART_OF]->(:SourceDocument)
