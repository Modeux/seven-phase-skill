-- fixture: char_general
-- kind: character
-- id: f1c70002-0000-7000-8000-000000000001
-- requires: creator
-- is_nsfw: false
-- state: public all-ages character owned by fx-creator
SELECT pg_temp.fx_character('f1c70002-0000-7000-8000-000000000001', 'f1c70000-0000-7000-8000-000000000001', 'FX General', FALSE);
