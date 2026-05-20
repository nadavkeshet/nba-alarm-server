-- Soccer schema for Clutch Alarm v3.
--
-- Idempotent (CREATE TABLE IF NOT EXISTS / IF NOT EXISTS on every clause).
-- Loaded by Server/main.py:_init_db() on every server start.
--
-- See clutch-alarm-v3-soccer-redesign.md §5c for the design rationale.

-- ---------------------------------------------------------------------------
-- Soccer alarms (one-shot or persistent, depending on alarm_type)

CREATE TABLE IF NOT EXISTS soccer_alarms (
    id              SERIAL          PRIMARY KEY,
    user_token      VARCHAR         NOT NULL,
    alarm_type      VARCHAR         NOT NULL,  -- 'drama_radar' | 'match' | 'late_drama' | 'knockout_et' | 'comeback' | 'hat_trick' | 'red_card' | 'big_chance' | 'penalty'
    match_id        VARCHAR,                   -- 365scores game id, nullable for radar-type alarms
    competition_id  VARCHAR,                   -- internal slug from competition_map.json
    player_id       VARCHAR,                   -- for hat-trick watch etc.
    threshold       REAL,                      -- Drama threshold (radar), goals (hat-trick), goal diff (late drama)
    config          JSONB           NOT NULL DEFAULT '{}'::jsonb,
    alert_type      VARCHAR         NOT NULL DEFAULT 'alarm',  -- 'alarm' | 'silent'
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    fired_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_soccer_alarms_active
    ON soccer_alarms (is_active) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_soccer_alarms_user
    ON soccer_alarms (user_token);

-- ---------------------------------------------------------------------------
-- Favorite players

CREATE TABLE IF NOT EXISTS soccer_favorites (
    user_token    VARCHAR     NOT NULL,
    player_id     VARCHAR     NOT NULL,
    player_name   VARCHAR,
    team_id       VARCHAR,
    config        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    alert_type    VARCHAR     NOT NULL DEFAULT 'alarm',
    paused        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_token, player_id)
);

-- ---------------------------------------------------------------------------
-- Favorite teams

CREATE TABLE IF NOT EXISTS soccer_favorite_teams (
    user_token       VARCHAR     NOT NULL,
    team_id          VARCHAR     NOT NULL,
    team_name        VARCHAR,
    competition_id   VARCHAR,
    drama_threshold  REAL        NOT NULL DEFAULT 7.0,
    alert_type       VARCHAR     NOT NULL DEFAULT 'alarm',
    paused           BOOLEAN     NOT NULL DEFAULT FALSE,
    notify_at_kickoff BOOLEAN    NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_token, team_id)
);

-- ---------------------------------------------------------------------------
-- Favorite competitions (drives Drama Radar scope per user)

CREATE TABLE IF NOT EXISTS soccer_favorite_competitions (
    user_token       VARCHAR     NOT NULL,
    competition_id   VARCHAR     NOT NULL,  -- slug: 'premier_league' | 'ucl' | ...
    drama_threshold  REAL        NOT NULL DEFAULT 7.0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_token, competition_id)
);

-- ---------------------------------------------------------------------------
-- Match cache (merged 365scores + Sofascore payload for in-progress matches)

CREATE TABLE IF NOT EXISTS soccer_match_cache (
    match_id         VARCHAR     PRIMARY KEY,
    sofascore_id     VARCHAR,
    competition_id   VARCHAR,
    status           VARCHAR,                 -- 'scheduled' | 'live' | 'ht' | 'ft' | 'et' | 'pens'
    minute           INTEGER,
    home_team_id     VARCHAR,
    away_team_id     VARCHAR,
    home_score       INTEGER,
    away_score       INTEGER,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw              JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_soccer_match_cache_status
    ON soccer_match_cache (status);

CREATE INDEX IF NOT EXISTS idx_soccer_match_cache_competition
    ON soccer_match_cache (competition_id);

-- ---------------------------------------------------------------------------
-- Drama snapshots (logged on every evaluator pass, used to tune weights)

CREATE TABLE IF NOT EXISTS drama_snapshots (
    id              SERIAL       PRIMARY KEY,
    match_id        VARCHAR      NOT NULL,
    sport           VARCHAR      NOT NULL,    -- 'basketball' | 'football'
    minute          INTEGER,
    state_score     REAL         NOT NULL DEFAULT 0,
    momentum_score  REAL         NOT NULL DEFAULT 0,
    events_score    REAL         NOT NULL DEFAULT 0,
    stakes_mult     REAL         NOT NULL DEFAULT 1,
    drama_total     REAL         NOT NULL,
    computed_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_drama_snapshots_match
    ON drama_snapshots (match_id);

CREATE INDEX IF NOT EXISTS idx_drama_snapshots_computed_at
    ON drama_snapshots (computed_at);

-- ---------------------------------------------------------------------------
-- Extend fired_alerts so the same match can fire on Drama once + on Red Card
-- once without being deduped to a single event.

ALTER TABLE fired_alerts ADD COLUMN IF NOT EXISTS signal_type VARCHAR;
