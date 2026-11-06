-- fixture: zero
-- kind: account
-- id: f1c70000-0000-7000-8000-000000000005
-- email: fx-zero@deepweave.local
-- adult_status: adult
-- credits:
-- state: adult with no points and no diamonds, for insufficient-credit paths
SELECT pg_temp.fx_user('f1c70000-0000-7000-8000-000000000005', 'fx-zero@deepweave.local', 'fx_zero', 'FX Zero',
                       '+8613900001005', 'adult');
