// FinancIAl — panel municipal (/admin)
// Los <input type="date"> del formulario de documentos abren el
// calendario nativo al hacer click en cualquier parte del recuadro,
// no solo en el ícono. showPicker() no existe en navegadores viejos;
// si falta, el input sigue funcionando con su comportamiento normal.
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll('input[type="date"]').forEach((input) => {
        input.addEventListener("click", () => {
            if (typeof input.showPicker === "function") {
                try {
                    input.showPicker();
                } catch (error) {
                    // Ignorar: por ejemplo el input está disabled o readonly.
                }
            }
        });
    });
});

// Listado de emprendedores: el filtro de hito se ajusta sin esperar a enviar el
// formulario. Cada rubro tiene sus propios hitos, y los formalizados no tienen
// ninguno pendiente. Sin JS el servidor aplica las mismas reglas al filtrar.
document.addEventListener("DOMContentLoaded", () => {
    const selectHito = document.querySelector("select[data-opciones-por-rubro]");
    if (!selectHito) return;

    const { rubro: selectRubro, estado: selectEstado } = selectHito.form.elements;
    const opcionesPorRubro = JSON.parse(selectHito.dataset.opcionesPorRubro);

    const actualizarHito = () => {
        const opciones = opcionesPorRubro[selectRubro.value] || [];
        const sinHito = selectEstado.value === "formalizado";
        const elegido = selectHito.value;

        selectHito.length = 1; // Conserva solo la opción "Todos"
        opciones.forEach(([id, texto]) => selectHito.add(new Option(texto, id)));
        selectHito.value = !sinHito && opciones.some(([id]) => id === elegido) ? elegido : "";
        selectHito.disabled = sinHito;
    };

    selectRubro.addEventListener("change", actualizarHito);
    selectEstado.addEventListener("change", actualizarHito);
});
