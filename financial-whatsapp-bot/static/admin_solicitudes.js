// Actualiza la conversación de una solicitud sin recargar la página.
// Consulta /admin/solicitudes/<id>/hilo cada pocos segundos y reemplaza solo el
// hilo: lo que se está escribiendo y la cita elegida con "Responder" no se tocan.
(function () {
    var watch = document.getElementById("hilo-watch");
    if (!watch) return;

    var requestId = watch.dataset.request;
    var status = watch.dataset.status;
    var sig = watch.dataset.sig;
    var hilo = document.getElementById("hilo");
    var ventana = document.getElementById("ventana-valor");
    var aviso = document.getElementById("nuevo-mensaje");
    var INTERVALO_MS = 5000;
    var temporizadorAviso = null;
    var intervalo = null;

    function mostrarAviso(texto) {
        if (!aviso) return;
        aviso.textContent = texto;
        aviso.hidden = false;
        clearTimeout(temporizadorAviso);
        temporizadorAviso = setTimeout(function () { aviso.hidden = true; }, 6000);
    }

    function estadoCambio() {
        clearInterval(intervalo);
        if (!aviso) return;
        aviso.hidden = false;
        aviso.textContent = "La solicitud cambió de estado. ";
        var enlace = document.createElement("a");
        enlace.href = window.location.pathname;
        enlace.textContent = "Recargar";
        aviso.appendChild(enlace);
    }

    function actualizar() {
        if (document.visibilityState !== "visible") return;

        var url = "/admin/solicitudes/" + encodeURIComponent(requestId) + "/hilo";
        var responder = new URLSearchParams(window.location.search).get("responder");
        if (responder) url += "?responder=" + encodeURIComponent(responder);

        fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
            .then(function (respuesta) {
                if (respuesta.status === 401) { window.location.href = "/admin/login"; return null; }
                return respuesta.ok ? respuesta.json() : null;
            })
            .then(function (datos) {
                if (!datos) return;
                if (ventana && datos.ventana_html) ventana.innerHTML = datos.ventana_html;
                if (datos.status !== status) { estadoCambio(); return; }
                if (datos.sig !== sig) {
                    sig = datos.sig;
                    if (hilo) {
                        var alFinal = hilo.scrollHeight - hilo.scrollTop - hilo.clientHeight < 40;
                        hilo.innerHTML = datos.hilo_html;
                        if (alFinal) hilo.scrollTop = hilo.scrollHeight;
                    }
                    mostrarAviso("💬 Mensaje nuevo");
                }
            })
            .catch(function () { /* sin red: se reintenta en el próximo ciclo */ });
    }

    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "visible") actualizar();
    });

    if (hilo) hilo.scrollTop = hilo.scrollHeight;
    intervalo = setInterval(actualizar, INTERVALO_MS);
})();
