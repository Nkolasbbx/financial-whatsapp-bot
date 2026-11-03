"""Cliente privado del backend; nunca usa la clave pública como respaldo."""


def get_backend_client():
    import dependencies

    client = getattr(dependencies, "supabase_admin", None)
    if client is not None:
        return client
    configured = any(getattr(dependencies, name, None) for name in (
        "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "supabase",
    ))
    if configured:
        raise RuntimeError("Supabase está configurado pero su cliente administrativo no está disponible")
    # Compatibilidad con el modo local, solo sin Supabase configurado.
    return None
