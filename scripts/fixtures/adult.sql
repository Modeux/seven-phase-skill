-- fixture: adult
-- kind: account
-- id: f1c70000-0000-7000-8000-000000000003
-- email: fx-adult@deepweave.local
-- adult_status: adult
-- credits: permanent_point=10000 diamond=1000
-- state: ordinary adult viewer with enough points and diamonds for chat and generation
SELECT pg_temp.fx_user('f1c70000-0000-7000-8000-000000000003', 'fx-adult@deepweave.local', 'fx_adult', 'FX Adult',
                       '+8613900001003', 'adult');
SELECT pg_temp.fx_grant('f1c70000-0000-7000-8000-000000000003', 'permanent_point', 10000, 'adult');
SELECT pg_temp.fx_grant('f1c70000-0000-7000-8000-000000000003', 'diamond', 1000, 'adult');
