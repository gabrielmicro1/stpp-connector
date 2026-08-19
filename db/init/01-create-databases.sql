-- Runs once on first boot of the postgres volume (docker-entrypoint-initdb.d).
CREATE DATABASE rfff_seed;
CREATE DATABASE jobs;
CREATE DATABASE wdp;
