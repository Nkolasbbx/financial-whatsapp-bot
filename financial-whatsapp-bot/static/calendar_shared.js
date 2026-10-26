// FinancIAl — piezas comunes del calendario (FullCalendar) que comparten el
// panel del emprendedor (portal_calendar.js) y las convocatorias del panel
// municipal (admin_convocatorias.js, HdU019). Se carga antes que ellos.
(function () {
    "use strict";

    const TIME_ZONE = "America/Santiago";

    // Valor para <input type="datetime-local"> en hora de Chile.
    function chileInputValue(value) {
        const date = value instanceof Date ? value : new Date(value);
        const parts = new Intl.DateTimeFormat("sv-SE", {
            timeZone: TIME_ZONE,
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            hourCycle: "h23",
        }).formatToParts(date);
        const values = {};
        parts.forEach(function (part) {
            values[part.type] = part.value;
        });
        return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
    }

    // Fecha sugerida al crear: el día elegido a la hora por defecto o, si
    // ya pasó, la próxima hora en punto.
    function defaultDateTime(selectedDate, defaultHour) {
        if (selectedDate) {
            const candidate = `${selectedDate}T${defaultHour || "09:00"}`;
            const currentLocal = chileInputValue(new Date());
            if (candidate > currentLocal) {
                return candidate;
            }
        }

        const nextHour = new Date(Date.now() + 60 * 60 * 1000);
        nextHour.setMinutes(0, 0, 0);
        return chileInputValue(nextHour);
    }

    // FastAPI responde `detail` como texto, como lista de errores de
    // Pydantic ({msg}) o, en las convocatorias, como lista de textos.
    function detailMessage(detail) {
        if (Array.isArray(detail)) {
            return detail
                .map(function (item) {
                    return typeof item === "string" ? item : item.msg || "Dato inválido";
                })
                .join(". ")
                .replace(/\.\./g, ".");
        }
        return detail || "No se pudo completar la operación";
    }

    // fetch con JSON y token CSRF. Si falla, lanza un Error cuyo `detail`
    // conserva la respuesta original (por ejemplo, la lista de errores).
    async function jsonRequest(url, options, settings) {
        const config = settings || {};
        const requestOptions = Object.assign({}, options || {});
        requestOptions.headers = Object.assign(
            {
                "Content-Type": "application/json",
                "X-CSRF-Token": config.csrfToken || "",
            },
            requestOptions.headers || {}
        );

        const response = await fetch(url, requestOptions);
        let payload = null;
        try {
            payload = await response.json();
        } catch (_error) {
            payload = null;
        }

        if (!response.ok) {
            const error = new Error(
                response.status === 401 && config.sessionExpiredMessage
                    ? config.sessionExpiredMessage
                    : detailMessage(payload?.detail)
            );
            error.status = response.status;
            error.detail = payload?.detail;
            throw error;
        }

        return payload;
    }

    function setMessage(element, message, type) {
        if (!element) {
            return;
        }
        element.textContent = message || "";
        element.className = message ? `calendar-message ${type || "info"}` : "";
    }

    // Abre y cierra un modal: botón de cierre, clic en el fondo y Escape.
    function bindModal(options) {
        const backdrop = options.backdrop;

        function open() {
            backdrop.classList.add("visible");
            backdrop.setAttribute("aria-hidden", "false");
            document.body.classList.add("calendar-modal-open");
            window.setTimeout(function () {
                const target =
                    typeof options.focusElement === "function"
                        ? options.focusElement()
                        : options.focusElement;
                if (target) {
                    target.focus();
                }
            }, 0);
        }

        function close() {
            backdrop.classList.remove("visible");
            backdrop.setAttribute("aria-hidden", "true");
            document.body.classList.remove("calendar-modal-open");
            if (options.onClose) {
                options.onClose();
            }
        }

        options.closeButton.addEventListener("click", close);
        backdrop.addEventListener("click", function (event) {
            if (event.target === backdrop) {
                close();
            }
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && backdrop.classList.contains("visible")) {
                close();
            }
        });

        return { open: open, close: close };
    }

    // Opciones comunes de FullCalendar (idioma, barra y vista según ancho).
    function baseOptions() {
        return {
            initialView: window.innerWidth < 640 ? "listMonth" : "dayGridMonth",
            locale: "es",
            firstDay: 1,
            height: "auto",
            dayMaxEvents: true,
            nowIndicator: true,
            headerToolbar: {
                left: "prev,next today",
                center: "title",
                right: "dayGridMonth,listMonth",
            },
            buttonText: {
                today: "Hoy",
                month: "Mes",
                list: "Lista",
            },
        };
    }

    function formatDate(value) {
        return new Intl.DateTimeFormat("es-CL", {
            timeZone: TIME_ZONE,
            weekday: "long",
            day: "numeric",
            month: "long",
            year: "numeric",
        }).format(new Date(value));
    }

    function formatTime(value) {
        return new Intl.DateTimeFormat("es-CL", {
            timeZone: TIME_ZONE,
            hour: "2-digit",
            minute: "2-digit",
            hourCycle: "h23",
        }).format(new Date(value));
    }

    window.FinancialCalendar = {
        TIME_ZONE: TIME_ZONE,
        baseOptions: baseOptions,
        bindModal: bindModal,
        chileInputValue: chileInputValue,
        defaultDateTime: defaultDateTime,
        detailMessage: detailMessage,
        formatDate: formatDate,
        formatTime: formatTime,
        jsonRequest: jsonRequest,
        setMessage: setMessage,
    };
})();
