-- Prueba de integración para PostgreSQL/Supabase DESPUÉS de la migración.
-- Usar una BD local/de pruebas y rol postgres. Los fixtures se revierten.
-- Si un editor interrumpe tras un error, ejecutar ROLLBACK en esa sesión.
BEGIN;

DO $$
DECLARE
    base public.formalization_cost_catalog%ROWTYPE;
    fixture public.formalization_cost_catalog%ROWTYPE;
BEGIN
    IF (SELECT count(*) FROM public.formalization_cost_catalog
        WHERE version = 1 AND code IN (
            'C01','C07','C12','C13','C16','C17','C21','C22','C23','C24'
        )) <> 10 THEN
        RAISE EXCEPTION 'Falta parte de la carga inicial';
    END IF;

    SELECT * INTO STRICT base FROM public.formalization_cost_catalog
        WHERE code = 'C01' AND version = 1;
    IF base.amount_min <> 0 OR base.amount_max <> 0 OR base.review_status <> 'reviewed' THEN
        RAISE EXCEPTION 'C01 no conserva su gratuidad respaldada';
    END IF;
    IF EXISTS (SELECT 1 FROM public.formalization_cost_catalog
        WHERE code IN ('C07','C12','C13','C16','C17','C21','C22','C23','C24')
          AND version = 1 AND (amount_min IS NOT NULL OR amount_max IS NOT NULL)) THEN
        RAISE EXCEPTION 'Un costo pendiente de la carga inicial tiene un importe';
    END IF;

    IF NOT (SELECT relrowsecurity FROM pg_class
        WHERE oid = 'public.formalization_cost_catalog'::regclass) THEN
        RAISE EXCEPTION 'RLS no está habilitada';
    END IF;
    IF has_table_privilege('anon', 'public.formalization_cost_catalog', 'SELECT')
        OR has_table_privilege('authenticated', 'public.formalization_cost_catalog', 'SELECT')
        OR has_function_privilege('anon', 'public.get_formalization_cost_candidates(text,text,text,date)', 'EXECUTE')
        OR has_function_privilege('authenticated', 'public.get_formalization_cost_candidates(text,text,text,date)', 'EXECUTE')
    THEN
        RAISE EXCEPTION 'Acceso indebido desde anon/authenticated';
    END IF;
    IF NOT has_function_privilege('service_role', 'public.get_formalization_cost_candidates(text,text,text,date)', 'EXECUTE')
        OR NOT has_table_privilege('service_role', 'public.formalization_cost_catalog', 'SELECT')
        OR has_table_privilege('service_role', 'public.formalization_cost_catalog', 'DELETE')
        OR has_column_privilege('service_role', 'public.formalization_cost_catalog', 'amount_min', 'UPDATE')
    THEN
        RAISE EXCEPTION 'Privilegios inesperados para service_role';
    END IF;

    -- Fixtures ficticios, sin precio real ni permanencia fuera de esta prueba.
    fixture := base;
    fixture.id := gen_random_uuid();
    fixture.code := 'C90';
    fixture.title := 'FIXTURE SOLO PRUEBAS';
    fixture.activity_code := 'textil_confeccion';
    fixture.comuna_code := 'el_bosque';
    fixture.location_type := 'local';
    INSERT INTO public.formalization_cost_catalog SELECT (fixture).*;

    IF NOT EXISTS (SELECT 1 FROM public.get_formalization_cost_candidates(
        'textil_confeccion', 'el_bosque', 'local', DATE '2026-10-15') WHERE code = 'C90')
        OR EXISTS (SELECT 1 FROM public.get_formalization_cost_candidates(
        'alimentos_elaboracion', 'recoleta', 'vivienda', DATE '2026-10-15') WHERE code = 'C90')
        OR NOT EXISTS (SELECT 1 FROM public.get_formalization_cost_candidates(
        NULL, NULL, NULL, DATE '2026-10-15') WHERE code = 'C90')
    THEN
        RAISE EXCEPTION 'Fallo del filtrado por alcance/contexto desconocido';
    END IF;

    fixture := base;
    fixture.id := gen_random_uuid(); fixture.code := 'C91';
    fixture.valid_from := DATE '2030-01-01';
    INSERT INTO public.formalization_cost_catalog SELECT (fixture).*;
    fixture := base;
    fixture.id := gen_random_uuid(); fixture.code := 'C92';
    fixture.valid_from := DATE '2026-10-01'; fixture.valid_until := DATE '2026-10-15';
    INSERT INTO public.formalization_cost_catalog SELECT (fixture).*;
    fixture := base;
    fixture.id := gen_random_uuid(); fixture.code := 'C93'; fixture.active := false;
    INSERT INTO public.formalization_cost_catalog SELECT (fixture).*;
    IF EXISTS (SELECT 1 FROM public.get_formalization_cost_candidates(
        NULL, NULL, NULL, DATE '2026-10-15') WHERE code IN ('C91','C92','C93'))
        OR NOT EXISTS (SELECT 1 FROM public.get_formalization_cost_candidates(
        NULL, NULL, NULL, DATE '2026-10-14') WHERE code = 'C92')
    THEN
        RAISE EXCEPTION 'Fallo de vigencia semiabierta/inactividad';
    END IF;

    fixture := base;
    fixture.id := gen_random_uuid(); fixture.code := 'C94';
    fixture.pricing_method := 'pending'; fixture.calculation_code := 'manual_review';
    fixture.review_status := 'pending';
    BEGIN
        INSERT INTO public.formalization_cost_catalog SELECT (fixture).*;
        RAISE EXCEPTION 'Se permitió precio cero para método pendiente';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    fixture := base;
    fixture.id := gen_random_uuid(); fixture.code := 'C94';
    fixture.amount_min := -1;
    BEGIN
        INSERT INTO public.formalization_cost_catalog SELECT (fixture).*;
        RAISE EXCEPTION 'Se permitió un monto negativo';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    fixture := base;
    fixture.id := gen_random_uuid(); fixture.code := 'C94';
    fixture.conditions := '{}'::jsonb;
    BEGIN
        INSERT INTO public.formalization_cost_catalog SELECT (fixture).*;
        RAISE EXCEPTION 'Se permitieron condiciones sin estructura';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    fixture := base;
    fixture.id := gen_random_uuid(); fixture.code := 'C94';
    fixture.source_refs := '[]'::jsonb;
    BEGIN
        INSERT INTO public.formalization_cost_catalog SELECT (fixture).*;
        RAISE EXCEPTION 'Se permitió un concepto sin referencias';
    EXCEPTION WHEN check_violation THEN NULL;
    END;

    BEGIN
        UPDATE public.formalization_cost_catalog SET title = 'CAMBIO NO PERMITIDO'
            WHERE code = 'C90';
        RAISE EXCEPTION 'Se permitió reescribir una versión';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    fixture := base;
    fixture.id := gen_random_uuid(); fixture.version := 2;
    fixture.code := 'C90'; fixture.activity_code := 'textil_confeccion';
    fixture.comuna_code := 'el_bosque'; fixture.location_type := 'local';
    BEGIN
        INSERT INTO public.formalization_cost_catalog SELECT (fixture).*;
        RAISE EXCEPTION 'Se permitieron dos versiones activas del mismo alcance';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    UPDATE public.formalization_cost_catalog SET active = false WHERE code = 'C90';
    INSERT INTO public.formalization_cost_catalog SELECT (fixture).*;
    IF (SELECT count(*) FROM public.get_formalization_cost_candidates(
        'textil_confeccion', 'el_bosque', 'local', DATE '2026-10-15') WHERE code = 'C90') <> 1
    THEN
        RAISE EXCEPTION 'No se seleccionó solo la versión activa';
    END IF;
    BEGIN
        PERFORM public.get_formalization_cost_candidates('', 'recoleta', 'vivienda');
        RAISE EXCEPTION 'Se aceptó un código vacío';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    RAISE NOTICE 'Comprobaciones del catálogo superadas; los fixtures se revierten.';
END;
$$;

ROLLBACK;
