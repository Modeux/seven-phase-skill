-- fixture: minor
-- kind: account
-- id: f1c70000-0000-7000-8000-000000000002
-- email: fx-minor@deepweave.local
-- adult_status: minor
-- credits: permanent_point=1000 diamond=100
-- state: answered the age gate as a minor; funded so a block is the age rule, not an empty wallet
SELECT pg_temp.fx_user('f1c70000-0000-7000-8000-000000000002', 'fx-minor@deepweave.local', 'fx_minor', 'FX Minor',
                       '+8613900001002', 'minor');
SELECT pg_temp.fx_grant('f1c70000-0000-7000-8000-000000000002', 'permanent_point', 1000, 'minor');
SELECT pg_temp.fx_grant('f1c70000-0000-7000-8000-000000000002', 'diamond', 100, 'minor');
