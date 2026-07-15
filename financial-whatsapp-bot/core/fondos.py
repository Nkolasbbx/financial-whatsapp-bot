def simulate_funds(user: dict) -> str:
    """Simula el proceso de postulación a fondos y muestra resultados."""
    is_formal= user.get("inicio_sii") == "si"
    rubro = user.get("rubro", "otro")
    funds = [
        {
            "name": "💰 Capital Semilla - SERCOTEC",
            "reqs": [
                ("Persona natural mayor de 18 años", True),
                ("Inicio de actividades en SII", is_formal),
                ("Antigüedad menor a 2 años", True),
                ("Ventas menores a 2.400 UF/año", True),
                ("Capacitación en gestión empresarial", False),
            ]
        },
        {
            "name": "🐝 Capital Abeja - SERCOTEC",
            "reqs": [
                ("Mujer emprendedora", None),
                ("Mayor de 18 años", True),
                ("Inicio de actividades en SII", is_formal),
                ("Ventas menores a 5.000 UF/año", True),
            ]
        },
        {
            "name": "📈 Crece - SERCOTEC",
            "reqs": [
                ("Inicio de actividades > 6 meses", is_formal if is_formal else False),
                ("Ventas entre 200 y 5.000 UF/año", None),
                ("Patente municipal al día", None),
            ]
        },
    ]
 
    lines = ["🎯 *Simulación de Fondos Concursables*\n"]
    lines.append(f"Basado en tu perfil: _{rubro}_ | {'Formalizado' if is_formal else 'No formalizado'}\n")

    for fund in funds:
        met = sum(1 for _, v in fund["reqs"] if v is True)
        total = len(fund["reqs"])
        pct = round((met / total) * 100)
 
        lines.append(f"\n*{fund['name']}*")
        lines.append(f"Compatibilidad: {pct}%")
 
        for req_text, req_met in fund["reqs"]:
            if req_met is True:
                lines.append(f"  ✅ {req_text}")
            elif req_met is False:
                lines.append(f"  ❌ {req_text}")
            else:
                lines.append(f"  ⚠️ {req_text} _(necesito más info)_")
 
    lines.append("\n💡 *¿Quieres que te ayude a cumplir los requisitos que te faltan?* Escribe el nombre del fondo que te interesa.")
 
    return "\n".join(lines)