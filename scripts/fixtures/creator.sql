-- fixture: creator
-- kind: account
-- id: f1c70000-0000-7000-8000-000000000001
-- email: fx-creator@deepweave.local
-- adult_status: adult
-- credits: permanent_point=5000 diamond=600
-- state: adult who answered the age gate, email and phone verified, owns the fixture characters
SELECT pg_temp.fx_user('f1c70000-0000-7000-8000-000000000001', 'fx-creator@deepweave.local', 'fx_creator', 'FX Creator',
                       '+8613900001001', 'adult');
SELECT pg_temp.fx_grant('f1c70000-0000-7000-8000-000000000001', 'permanent_point', 5000, 'creator');
SELECT pg_temp.fx_grant('f1c70000-0000-7000-8000-000000000001', 'diamond', 600, 'creator');
