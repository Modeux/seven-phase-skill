-- fixture: char_nsfw
-- kind: character
-- id: f1c70002-0000-7000-8000-000000000002
-- requires: creator
-- is_nsfw: true
-- state: public 18+ character owned by fx-creator
SELECT pg_temp.fx_character('f1c70002-0000-7000-8000-000000000002', 'f1c70000-0000-7000-8000-000000000001', 'FX Mature', TRUE);
