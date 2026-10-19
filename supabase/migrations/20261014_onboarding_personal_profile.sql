-- HDU001: ampliación del perfil. Compatible con la migración aplicada manualmente.
-- Los NULL mantienen compatibles los usuarios existentes; el backend valida
-- los datos obligatorios. La dirección del negocio es opcional.
BEGIN;

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS nombre_completo text,
    ADD COLUMN IF NOT EXISTS rut text,
    ADD COLUMN IF NOT EXISTS email text,
    ADD COLUMN IF NOT EXISTS direccion_negocio text,
    ADD COLUMN IF NOT EXISTS profile_completion_step text;

COMMIT;
