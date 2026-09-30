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

    // Lista de emprendedores: "Ver más" revela EMPRENDEDORES_PASO filas
    // ocultas por click (acumulativo, sin recargar la página) hasta que no
    // quede ninguna, momento en que el botón desaparece.
    document.querySelectorAll(".admin-table-toggle").forEach((button) => {
        const tabla = document.getElementById(button.getAttribute("aria-controls"));
        if (!tabla) return;

        const paso = parseInt(button.dataset.paso, 10) || 10;

        const actualizarBoton = () => {
            const ocultas = tabla.querySelectorAll(".admin-table-row-hidden:not(.is-visible)");
            if (ocultas.length === 0) {
                button.remove();
                return;
            }
            button.textContent = `Ver ${Math.min(paso, ocultas.length)} más`;
        };

        button.addEventListener("click", () => {
            const ocultas = tabla.querySelectorAll(".admin-table-row-hidden:not(.is-visible)");
            Array.from(ocultas)
                .slice(0, paso)
                .forEach((fila) => fila.classList.add("is-visible"));
            actualizarBoton();
        });
    });
});
