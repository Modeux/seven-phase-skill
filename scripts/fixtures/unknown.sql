-- fixture: unknown
-- kind: account
-- id: f1c70000-0000-7000-8000-000000000004
-- email: fx-unknown@deepweave.local
-- adult_status: unknown
-- credits: permanent_point=1000 diamond=100
-- state: has not answered the age gate yet; funded so the gate is what the walk sees
SELECT pg_temp.fx_user('f1c70000-0000-7000-8000-000000000004', 'fx-unknown@deepweave.local', 'fx_unknown', 'FX Unknown',
                       '+8613900001004', 'unknown');
SELECT pg_temp.fx_grant('f1c70000-0000-7000-8000-000000000004', 'permanent_point', 1000, 'unknown');
SELECT pg_temp.fx_grant('f1c70000-0000-7000-8000-000000000004', 'diamond', 100, 'unknown');
