-- Run this once in the Supabase dashboard SQL Editor:
-- https://supabase.com/dashboard/project/ycvajatqtknwcbnlvevb/sql/new

CREATE TABLE IF NOT EXISTS user_templates (
    id            TEXT        PRIMARY KEY,
    template_name TEXT        NOT NULL DEFAULT '',
    ui_json       JSONB       NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
