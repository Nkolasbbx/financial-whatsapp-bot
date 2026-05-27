-- ══════════════════════════════════════════════
-- FinancIAl - Schema para Supabase
-- Ejecutar en: Supabase Dashboard → SQL Editor
-- ══════════════════════════════════════════════

-- Tabla de usuarios/emprendedores
CREATE TABLE IF NOT EXISTS users (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    phone TEXT UNIQUE NOT NULL,
    rubro TEXT,
    rubro_raw TEXT,
    comuna TEXT,
    inicio_sii TEXT,                          -- 'si', 'no', 'no_sabe'
    onboarding_step TEXT DEFAULT '0',         -- '0','1','2','3','done'
    roadmap JSONB DEFAULT '[]'::jsonb,        -- Array de hitos
    conversation_history JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Index para búsqueda rápida por teléfono
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);

-- Row Level Security (opcional pero recomendado)
ALTER TABLE users ENABLE ROW LEVEL SECURITY;

-- Política: el service key puede hacer todo
CREATE POLICY "Service key full access" ON users
    FOR ALL
    USING (true)
    WITH CHECK (true);
