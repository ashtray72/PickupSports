-- PickupSports database initialization.
-- Runs once, the first time the Docker volume is created (docker-entrypoint-initdb.d).
-- Schema objects are NOT created here; they come from Alembic migrations
-- (server/alembic). This file only prepares the databases and extensions.

-- Development database (created by POSTGRES_DB); make sure PostGIS is present.
CREATE EXTENSION IF NOT EXISTS postgis;

-- Separate database for the automated test suite.
CREATE DATABASE pickup_test;
\connect pickup_test
CREATE EXTENSION IF NOT EXISTS postgis;
