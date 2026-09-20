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
