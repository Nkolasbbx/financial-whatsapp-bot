// FinancIAl — convocatorias comunitarias del panel municipal (HdU019).
// Reutiliza el calendario del panel del emprendedor (calendar_shared.js y
// portal_calendar.css): clic en un día para crear y en una convocatoria
// para editarla o cancelarla.
(function () {
    "use strict";

    function bindCopyButtons() {
        document.querySelectorAll("[data-copy-target]").forEach(function (button) {
            button.addEventListener("click", async function () {
                const input = document.getElementById(button.dataset.copyTarget);
                if (!input) {
                    return;
                }
                try {
                    await navigator.clipboard.writeText(input.value);
                } catch (_error) {
                    input.select();
                    document.execCommand("copy");
                }
                const original = button.textContent;
                button.textContent = "Copiado";
                window.setTimeout(function () {
                    button.textContent = original;
                }, 2000);
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        bindCopyButtons();

        const container = document.getElementById("community-calendar");
        if (!container) {
            return;
        }

        const shared = window.FinancialCalendar;
        const messageElement = document.getElementById("calendar-message");
        if (!window.FullCalendar || !shared) {
            if (messageElement) {
                messageElement.textContent =
                    "No se pudo cargar el calendario. Recarga la página para intentarlo nuevamente.";
                messageElement.className = "calendar-message error";
            }
            return;
        }

        const csrfToken = document
            .querySelector('meta[name="financial-csrf-token"]')
            ?.getAttribute("content");
        const defaultHour = container.dataset.defaultHour || "09:00";
        const recipients = Number(container.dataset.recipients || 0);
        const googleEnabled = container.dataset.google === "on";
        const requestSettings = {
            csrfToken: csrfToken,
            sessionExpiredMessage: "Tu sesión venció. Vuelve a iniciar sesión.",
        };

        const backdrop = document.getElementById("community-modal-backdrop");
        // .admin-container crea su propio contexto de apilamiento (z-index 1):
        // dentro de él, el modal quedaría bajo la barra superior fija.
        document.body.append(backdrop);
        const form = document.getElementById("community-event-form");
        const modalTitle = document.getElementById("community-modal-title");
        const eventIdInput = document.getElementById("community-event-id");
        const titleInput = document.getElementById("community-title");
        const categoryInput = document.getElementById("community-category");
        const descriptionInput = document.getElementById("community-description");
        const comunaInput = document.getElementById("community-comuna");
        const eventAtInput = document.getElementById("community-event-at");
        const noticeAtInput = document.getElementById("community-notice-at");
        const noticeHint = document.getElementById("community-notice-hint");
        const locationInput = document.getElementById("community-location");
        const linkInput = document.getElementById("community-link");
        const statusInfo = document.getElementById("community-status-info");
        const formError = document.getElementById("community-form-error");
        const saveButton = document.getElementById("community-save-button");
        const cancelButton = document.getElementById("community-cancel-button");
        const createButton = document.getElementById("community-create-button");
        const editableInputs = [
            titleInput,
            categoryInput,
            descriptionInput,
            eventAtInput,
            noticeAtInput,
            locationInput,
            linkInput,
        ];

        let calendar;
        let currentEvent = null;

        const modal = shared.bindModal({
            backdrop: backdrop,
            closeButton: document.getElementById("community-modal-close"),
            focusElement: function () {
                return titleInput.disabled ? null : titleInput;
            },
            onClose: function () {
                setFormErrors([]);
            },
        });

        function showMessage(message, type) {
            shared.setMessage(messageElement, message, type);
        }

        function setFormErrors(errors) {
            formError.replaceChildren();
            if (!errors || errors.length === 0) {
                return;
            }
            if (errors.length === 1) {
                formError.textContent = errors[0];
                return;
            }
            const list = document.createElement("ul");
            errors.forEach(function (message) {
                const item = document.createElement("li");
                item.textContent = message;
                list.append(item);
            });
            formError.append(list);
        }

        function setEditable(enabled) {
            editableInputs.forEach(function (input) {
                input.disabled = !enabled;
            });
            saveButton.hidden = !enabled;
            noticeHint.hidden = !enabled;
        }

        function describeDate(value) {
            return `${shared.formatDate(value)} a las ${shared.formatTime(value)}`;
        }

        // Aviso sugerido: un día antes a la misma hora; si ese momento ya
        // pasó, la próxima hora en punto (nunca después de la actividad).
        function suggestedNotice(eventAtValue) {
            const [datePart, timePart] = eventAtValue.split("T");
            const previousDay = new Date(`${datePart}T00:00:00Z`);
            previousDay.setUTCDate(previousDay.getUTCDate() - 1);
            const candidate = `${previousDay.toISOString().slice(0, 10)}T${timePart}`;
            if (candidate > shared.chileInputValue(new Date())) {
                return candidate;
            }
            const nextHour = shared.defaultDateTime(null, defaultHour);
            return nextHour < eventAtValue ? nextHour : eventAtValue;
        }

        function addParagraph(text) {
            const paragraph = document.createElement("p");
            paragraph.textContent = text;
            statusInfo.append(paragraph);
        }

        function renderStatus(event) {
            statusInfo.replaceChildren();
            const deliveries = event.deliveries || { sent: 0, failed: 0, pending: 0 };
            const failed = deliveries.failed ? ` (${deliveries.failed} con error)` : "";

            if (event.notice_status === "cancelado") {
                addParagraph("Convocatoria cancelada: ya no se muestra a los emprendedores.");
                if (deliveries.sent) {
                    addParagraph(`El aviso alcanzó a enviarse a ${deliveries.sent} emprendedores${failed}.`);
                }
            } else if (event.notice_status === "enviado") {
                addParagraph(`Aviso enviado a ${deliveries.sent} emprendedores${failed}.`);
            } else if (event.notice_status === "enviando") {
                addParagraph(
                    `El aviso se está enviando: ${deliveries.sent} enviados${failed}. ` +
                    "Ya no se puede editar."
                );
            } else {
                addParagraph(`Aviso programado para el ${describeDate(event.notice_at)}.`);
            }

            if (googleEnabled) {
                const googleText = {
                    synced: "Copiada en Google Calendar.",
                    failed: "No se pudo copiar en Google Calendar; se reintentará automáticamente.",
                    pending: "Pendiente de copiar en Google Calendar.",
                }[event.google_sync_status];
                if (googleText) {
                    addParagraph(googleText);
                }
            }
            statusInfo.hidden = false;
        }

        function refreshNoticeHint() {
            noticeHint.textContent =
                "El aviso se envía por WhatsApp, dentro de los 15 minutos siguientes, a los " +
                `emprendedores de ${comunaInput.value} con recordatorios activos (hoy serían ${recipients}).`;
        }

        // Sin día elegido, la actividad se sugiere para mañana: así queda
        // margen para programar el aviso antes.
        function tomorrowDate() {
            return shared.chileInputValue(new Date(Date.now() + 86400000)).slice(0, 10);
        }

        function openCreateModal(dateText) {
            form.reset();
            currentEvent = null;
            eventIdInput.value = "";
            eventAtInput.value = shared.defaultDateTime(dateText || tomorrowDate(), defaultHour);
            noticeAtInput.value = suggestedNotice(eventAtInput.value);
            modalTitle.textContent = "Nueva convocatoria";
            cancelButton.hidden = true;
            statusInfo.hidden = true;
            setEditable(true);
            refreshNoticeHint();
            setFormErrors([]);
            modal.open();
        }

        function openEventModal(info) {
            const event = info.event.extendedProps;
            form.reset();
            currentEvent = event;
            eventIdInput.value = event.id;
            titleInput.value = event.title || "";
            categoryInput.value = event.category || "";
            descriptionInput.value = event.description || "";
            eventAtInput.value = shared.chileInputValue(event.event_at);
            noticeAtInput.value = shared.chileInputValue(event.notice_at);
            locationInput.value = event.location || "";
            linkInput.value = event.link || "";
            modalTitle.textContent = event.editable ? "Editar convocatoria" : "Convocatoria";
            cancelButton.hidden = !event.cancellable;
            setEditable(Boolean(event.editable));
            refreshNoticeHint();
            renderStatus(event);
            setFormErrors([]);
            modal.open();
        }

        function toCalendarEvent(event) {
            let className = "calendar-event-community";
            if (event.notice_status === "cancelado") {
                className = "calendar-event-community-cancelled";
            } else if (event.notice_status === "enviado" || event.notice_status === "enviando") {
                className = "calendar-event-community-sent";
            }
            return {
                id: event.id,
                title: event.title,
                start: event.event_at,
                classNames: [className],
                extendedProps: event,
            };
        }

        async function loadEvents(info, successCallback, failureCallback) {
            try {
                const params = new URLSearchParams({
                    start: info.startStr,
                    end: info.endStr,
                });
                const payload = await shared.jsonRequest(
                    `/admin/api/convocatorias?${params.toString()}`,
                    { method: "GET" },
                    requestSettings
                );
                successCallback(payload.map(toCalendarEvent));
            } catch (error) {
                failureCallback(error);
                showMessage(error.message, "error");
            }
        }

        async function saveEvent(event) {
            event.preventDefault();
            setFormErrors([]);

            const eventId = eventIdInput.value;
            const payload = {
                title: titleInput.value,
                category: categoryInput.value,
                description: descriptionInput.value,
                event_at: eventAtInput.value,
                notice_at: noticeAtInput.value,
                location: locationInput.value,
                link: linkInput.value,
            };

            saveButton.disabled = true;
            try {
                const saved = await shared.jsonRequest(
                    eventId
                        ? `/admin/api/convocatorias/${encodeURIComponent(eventId)}`
                        : "/admin/api/convocatorias",
                    {
                        method: eventId ? "PATCH" : "POST",
                        body: JSON.stringify(payload),
                    },
                    requestSettings
                );
                modal.close();
                calendar.refetchEvents();

                let message = eventId
                    ? "Convocatoria actualizada."
                    : `Convocatoria creada. El aviso se enviará el ${describeDate(saved.notice_at)} ` +
                      `a los emprendedores de ${saved.comuna} con recordatorios activos (hoy son ${recipients}).`;
                if (googleEnabled && saved.google_sync_status === "failed") {
                    message += " No se pudo copiar en Google Calendar; se reintentará automáticamente.";
                }
                showMessage(message, "success");
            } catch (error) {
                setFormErrors(Array.isArray(error.detail) && error.detail.every(function (item) {
                    return typeof item === "string";
                }) ? error.detail : [error.message]);
            } finally {
                saveButton.disabled = false;
            }
        }

        async function cancelEvent() {
            const eventId = eventIdInput.value;
            if (!eventId) {
                return;
            }
            const noticePending = currentEvent && currentEvent.notice_status === "programado";
            const confirmation =
                "¿Cancelar esta convocatoria? Dejará de verse en el calendario de los emprendedores" +
                (noticePending ? " y no se enviará el aviso." : ".");
            if (!window.confirm(confirmation)) {
                return;
            }

            cancelButton.disabled = true;
            try {
                await shared.jsonRequest(
                    `/admin/api/convocatorias/${encodeURIComponent(eventId)}/cancelar`,
                    { method: "POST" },
                    requestSettings
                );
                modal.close();
                calendar.refetchEvents();
                showMessage("Convocatoria cancelada.", "success");
            } catch (error) {
                setFormErrors([error.message]);
            } finally {
                cancelButton.disabled = false;
            }
        }

        // Al cambiar la fecha de la actividad en una convocatoria nueva, el
        // aviso se ajusta solo mientras el admin no lo haya tocado.
        let noticeTouched = false;
        noticeAtInput.addEventListener("input", function () {
            noticeTouched = true;
        });
        eventAtInput.addEventListener("change", function () {
            if (!eventIdInput.value && !noticeTouched && eventAtInput.value) {
                noticeAtInput.value = suggestedNotice(eventAtInput.value);
            }
        });

        calendar = new FullCalendar.Calendar(
            container,
            Object.assign(shared.baseOptions(), {
                events: loadEvents,
                dateClick: function (info) {
                    noticeTouched = false;
                    openCreateModal(info.dateStr.slice(0, 10));
                },
                eventClick: openEventModal,
            })
        );

        calendar.render();
        form.addEventListener("submit", saveEvent);
        cancelButton.addEventListener("click", cancelEvent);
        createButton.addEventListener("click", function () {
            noticeTouched = false;
            openCreateModal(null);
        });
    });
})();
