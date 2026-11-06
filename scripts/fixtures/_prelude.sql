-- Helpers every fixture calls; dbq.py runs this and the chosen fixtures in one transaction, so a schema change rolls
-- the whole apply back instead of seeding part of it.
--
-- INVARIANT: credits only through INSERT INTO point_transactions; fn_point_tx_update_balance writes the balance and
-- trg_user_balance_guard rejects anything else.
-- INVARIANT: the grant gate is WHERE NOT EXISTS, never ON CONFLICT DO NOTHING. The balance trigger is BEFORE INSERT,
-- so it would add the amount and only then have the conflict skip the row: every rerun would inflate the balance
-- while the ledger stayed put.
-- WHY password_hash is a literal: it is the hash db/seeds/dev_seed.sql uses for deepweave-demo-123, the walk_lib.mjs
-- default; copying it from a demo user at runtime would make every account depend on the dev seed having run.

CREATE FUNCTION pg_temp.fx_user(p_id uuid, p_email text, p_username text, p_display text, p_phone text,
                                p_adult text) RETURNS void LANGUAGE plpgsql AS $fx$
BEGIN
    INSERT INTO users (id, email, username, display_name, password_hash, role, email_verified, is_active, timezone,
                       metadata, preferred_character_gender, preferred_character_gender_updated_at)
    VALUES (p_id, p_email, p_username, p_display,
            '$argon2id$v=19$m=65536,t=2,p=4$KzKERjldq7p6158Sji1R3w$plkNVyXt1Vufus9sWZsLHSm+AzM28C01v1uEY0W9p4I',
            'user', TRUE, TRUE, 'Asia/Shanghai', '{"seeded":true,"test_account":true,"fixture":true}'::jsonb,
            -- WHY set: a NULL preference opens the gender prompt over the first page of every walk; real walk seeds
            -- copied it from the test user for this reason.
            'female', TIMESTAMPTZ '2026-03-01 09:00:00+08')
    ON CONFLICT (id) DO UPDATE SET
        email = EXCLUDED.email, username = EXCLUDED.username, display_name = EXCLUDED.display_name,
        password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, email_verified = EXCLUDED.email_verified,
        is_active = EXCLUDED.is_active, timezone = EXCLUDED.timezone, metadata = EXCLUDED.metadata;

    -- WHY through the request row: trg_apply_user_verification_request sets users.phone and phone_verified, the same
    -- path as production. The id is derived so a rerun hits the conflict instead of binding again.
    INSERT INTO user_verification_requests (id, user_id, target, target_type, purpose, status, verified_at, applied_at,
                                            requested_ip, requested_user_agent, metadata)
    VALUES (('f1c70001' || substr(p_id::text, 9))::uuid, p_id, p_phone, 'phone', 'bind', 'applied', now(), now(),
            '127.0.0.1', 'seed-script', '{"seeded":true,"source":"dbq_fixture"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO community_user_settings (user_id, adult_status, adult_answered_at)
    VALUES (p_id, p_adult, CASE WHEN p_adult = 'unknown' THEN NULL ELSE TIMESTAMPTZ '2026-03-01 09:00:00+08' END)
    ON CONFLICT (user_id) DO UPDATE SET
        adult_status = EXCLUDED.adult_status, adult_answered_at = EXCLUDED.adult_answered_at;
END
$fx$;

CREATE FUNCTION pg_temp.fx_grant(p_user uuid, p_currency text, p_amount int, p_fixture text) RETURNS void
LANGUAGE plpgsql AS $fx$
BEGIN
    INSERT INTO point_transactions (user_id, currency_type, tx_type, amount, description, reference_type, idempotency_key)
    SELECT p_user, p_currency, 'admin_adjustment', p_amount, 'dbq fixture grant', 'admin_adjustment',
           'fixture:' || p_fixture || ':' || p_currency
    WHERE NOT EXISTS (SELECT 1 FROM point_transactions
                      WHERE idempotency_key = 'fixture:' || p_fixture || ':' || p_currency);
END
$fx$;

CREATE FUNCTION pg_temp.fx_character(p_id uuid, p_creator uuid, p_name text, p_nsfw boolean) RETURNS void
LANGUAGE plpgsql AS $fx$
BEGIN
    -- INVARIANT: DO NOTHING, never DO UPDATE: is_nsfw is immutable (trg_characters_is_nsfw_immutable), and the
    -- post-check reports a row whose flag differs rather than quietly keeping it.
    INSERT INTO characters (id, creator_id, name, tagline, description, visibility, system_prompt, persona,
                            initial_scene, is_nsfw, is_active, avatar_url, banner_url)
    VALUES (p_id, p_creator, p_name, p_name || ' tagline', p_name || ' description', 'public',
            'You are ' || p_name || '. Stay in character.',
            '{"personality":["calm"],"speaking_style":"plain","greeting":"Hello."}'::jsonb,
            '{"summary":"A quiet room.","greeting":"Hello."}'::jsonb,
            p_nsfw, TRUE,
            'https://images.unsplash.com/photo-1517841905240-472988babdf9?auto=format&fit=crop&w=900&q=80',
            'https://images.unsplash.com/photo-1517841905240-472988babdf9?auto=format&fit=crop&w=1400&q=80')
    ON CONFLICT (id) DO NOTHING;
END
$fx$;
