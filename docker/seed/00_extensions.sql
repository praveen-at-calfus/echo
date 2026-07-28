-- Runs before the seed dump (docker-entrypoint-initdb.d executes files alphabetically).
-- Must exist before the seed's CREATE TABLE for `embeddings` (a `vector` column).
CREATE EXTENSION IF NOT EXISTS vector;
