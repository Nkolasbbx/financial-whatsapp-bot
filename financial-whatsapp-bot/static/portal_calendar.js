(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        const container = document.getElementById("business-calendar");
        if (!container) {
            return;
        }

        const shared = window.FinancialCalendar;
        const messageElement = document.getElementById("calendar-message");

        function showMessage(message, type) {
            if (shared) {
                shared.setMessage(messageElement, message, type);
            } else if (messageElement) {
                messageElement.textContent = message || "";
                messageElement.className = message ? `calendar-message ${type || "info"}` : "";
            }
        }

        if (!window.FullCalendar || !shared) {
            showMessage(
                "No se pudo cargar el calendario visual. Recarga la página para intentarlo nuevamente.",
                "error"
            );
            return;
        }

        const csrfToken = document
            .querySelector('meta[name="financial-csrf-token"]')
            ?.getAttribute("content");
        const defaultHour = container.dataset.defaultHour || "09:00";
        const requestSettings = {
            csrfToken: csrfToken,
            sessionExpiredMessage: "Tu sesión venció. Solicita un nuevo acceso desde WhatsApp.",
        };

        const backdrop = document.getElementById("calendar-modal-backdrop");
        const form = document.getElementById("calendar-event-form");
        const modalTitle = document.getElementById("calendar-modal-title");
        const eventIdInput = document.getElementById("calendar-event-id");
        const descriptionInput = document.getElementById("calendar-description");
        const eventAtInput = document.getElementById("calendar-event-at");
        const reminderInput = document.getElementById("calendar-reminder-days");
        const formError = document.getElementById("calendar-form-error");
        const saveButton = document.getElementById("calendar-save-button");
        const deleteButton = document.getElementById("calendar-delete-button");
        const completeButton = document.getElementById("calendar-complete-button");
        const createButton = document.getElementById("calendar-create-button");
        const closeButton = document.getElementById("calendar-modal-close");
        const readonlyInformation = document.getElementById("calendar-readonly-information");
        const readonlyHeading = document.getElementById("calendar-readonly-heading");
        const readonlyDescription = document.getElementById("calendar-readonly-description");
        const readonlyFacts = document.getElementById("calendar-readonly-facts");
        const readonlyLink = document.getElementById("calendar-readonly-link");
        const descriptionField = descriptionInput.closest(".calendar-field");
        const eventAtField = eventAtInput.closest(".calendar-field");
        const reminderField = reminderInput.closest(".calendar-field");

        let calendar;

        const modal = shared.bindModal({
            backdrop: backdrop,
            closeButton: closeButton,
            focusElement: function () {
                return descriptionField.hidden ? closeButton : descriptionInput;
            },
            onClose: function () {
                setFormError("");
            },
        });

        function setFormError(message) {
            formError.textContent = message || "";
        }

        function setEditable(enabled) {
            descriptionInput.disabled = !enabled;
            eventAtInput.disabled = !enabled;
            reminderInput.disabled = !enabled;
            saveButton.hidden = !enabled;
        }

        function setFieldsVisibility(options) {
            descriptionField.hidden = Boolean(options.hideMain);
            eventAtField.hidden = Boolean(options.hideMain);
            reminderField.hidden = Boolean(options.hideReminder);
        }

        function renderFacts(facts) {
            readonlyFacts.replaceChildren();
            facts.forEach(function (fact) {
                if (!fact.value) {
                    return;
                }
                const wrapper = document.createElement("div");
                const term = document.createElement("dt");
                const detail = document.createElement("dd");
                term.textContent = fact.label;
                detail.textContent = fact.value;
                wrapper.append(term, detail);
                readonlyFacts.append(wrapper);
            });
            readonlyFacts.hidden = readonlyFacts.childElementCount === 0;
        }

        function openCreateModal(dateText) {
            form.reset();
            eventIdInput.value = "";
            descriptionInput.value = "";
            eventAtInput.value = shared.defaultDateTime(dateText || null, defaultHour);
            reminderInput.value = "0";
            modalTitle.textContent = "Nueva fecha importante";
            deleteButton.hidden = true;
            completeButton.hidden = true;
            readonlyInformation.hidden = true;
            setFieldsVisibility({});
            setEditable(true);
            setFormError("");
            modal.open();
        }

        function openCommunityModal(properties) {
            // Convocatoria del municipio (HdU019): solo lectura y con sus
            // datos propios, para distinguirla de las fechas personales.
            modalTitle.textContent = properties.category
                ? `Convocatoria comunitaria · ${properties.category}`
                : "Convocatoria comunitaria";
            readonlyHeading.textContent = properties.title || properties.description;
            readonlyDescription.textContent = properties.details || "";
            renderFacts([
                { label: "Categoría", value: properties.category },
                { label: "Fecha", value: shared.formatDate(properties.event_at) },
                { label: "Hora", value: shared.formatTime(properties.event_at) },
                { label: "Ubicación", value: properties.location },
                { label: "Organiza", value: properties.organizer },
            ]);
            if (properties.link) {
                readonlyLink.href = properties.link;
                readonlyLink.textContent = "Ver enlace de la actividad";
                readonlyLink.hidden = false;
            } else {
                readonlyLink.removeAttribute("href");
                readonlyLink.hidden = true;
            }
        }

        function openEditModal(info) {
            const event = info.event;
            const properties = event.extendedProps;
            const isActive = properties.status === "active";
            const isTaxEvent = properties.source === "tributaria";
            const isFundEvent = properties.source === "fondo";
            const isCommunityEvent = properties.source === "comunitaria";
            const isReadOnlyEvent = isTaxEvent || isFundEvent || isCommunityEvent;

            form.reset();
            eventIdInput.value = event.id;
            descriptionInput.value = properties.description || event.title;
            eventAtInput.value = shared.chileInputValue(properties.event_at || event.start);
            reminderInput.value = String(properties.reminder_days_before || 0);
            deleteButton.hidden = isReadOnlyEvent || !isActive;
            completeButton.hidden = isReadOnlyEvent || !isActive;
            setFieldsVisibility({
                hideMain: isCommunityEvent,
                hideReminder: isReadOnlyEvent,
            });
            setEditable(!isReadOnlyEvent && isActive);
            readonlyInformation.hidden = !isReadOnlyEvent;
            renderFacts([]);

            if (isCommunityEvent) {
                openCommunityModal(properties);
            } else {
                if (isTaxEvent) {
                    modalTitle.textContent = "Fecha tributaria importante";
                } else if (isFundEvent) {
                    modalTitle.textContent = "Cierre de fondo concursable";
                } else {
                    modalTitle.textContent = isActive
                        ? "Editar fecha importante"
                        : "Fecha completada";
                }
                readonlyHeading.textContent = isFundEvent
                    ? "Información de la convocatoria"
                    : "Información del trámite";
                readonlyDescription.textContent = isReadOnlyEvent
                    ? properties.details || "Consulta el sitio oficial para conocer más información."
                    : "";
                if (isReadOnlyEvent && properties.link) {
                    readonlyLink.href = properties.link;
                    readonlyLink.textContent = isFundEvent
                        ? "Revisar convocatoria"
                        : "Abrir sitio oficial";
                    readonlyLink.hidden = false;
                } else {
                    readonlyLink.removeAttribute("href");
                    readonlyLink.hidden = true;
                }
            }
            setFormError("");
            modal.open();
        }

        function toCalendarEvent(event) {
            const isTaxEvent = event.source === "tributaria";
            const isFundEvent = event.source === "fondo";
            const isCommunityEvent = event.source === "comunitaria";
            const overdue =
                event.status === "active" && new Date(event.event_at) < new Date();
            let className = "calendar-event-active";
            if (isTaxEvent) {
                className = "calendar-event-tax";
            } else if (isFundEvent) {
                className = "calendar-event-fund";
            } else if (isCommunityEvent) {
                className = "calendar-event-community";
            } else if (event.status === "completed") {
                className = "calendar-event-completed";
            } else if (overdue) {
                className = "calendar-event-overdue";
            }
            return {
                id: event.id,
                title: event.description,
                start: event.event_at,
                allDay: Boolean(event.all_day),
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
                const response = await fetch(
                    `/portal/api/calendar/events?${params.toString()}`
                );
                const payload = await response.json();

                if (!response.ok) {
                    throw new Error(shared.detailMessage(payload?.detail));
                }

                successCallback(payload.map(toCalendarEvent));
                showMessage("", "info");
            } catch (error) {
                failureCallback(error);
                showMessage(error.message, "error");
            }
        }

        async function saveEvent(event) {
            event.preventDefault();
            setFormError("");

            const eventId = eventIdInput.value;
            const description = descriptionInput.value.trim();
            const eventAt = eventAtInput.value;
            const reminderDays = Number(reminderInput.value);

            if (!description || !eventAt) {
                setFormError("Completa la descripción y la fecha.");
                return;
            }

            saveButton.disabled = true;
            try {
                await shared.jsonRequest(
                    eventId
                        ? `/portal/api/calendar/events/${encodeURIComponent(eventId)}`
                        : "/portal/api/calendar/events",
                    {
                        method: eventId ? "PATCH" : "POST",
                        body: JSON.stringify({
                            description: description,
                            event_at: eventAt,
                            reminder_days_before: reminderDays,
                        }),
                    },
                    requestSettings
                );
                modal.close();
                calendar.refetchEvents();
                showMessage(
                    eventId
                        ? "Fecha actualizada correctamente."
                        : "Fecha guardada correctamente.",
                    "success"
                );
            } catch (error) {
                setFormError(error.message);
            } finally {
                saveButton.disabled = false;
            }
        }

        async function deleteEvent() {
            const eventId = eventIdInput.value;
            if (!eventId || !window.confirm("¿Quieres eliminar esta fecha?")) {
                return;
            }

            deleteButton.disabled = true;
            try {
                await shared.jsonRequest(
                    `/portal/api/calendar/events/${encodeURIComponent(eventId)}`,
                    { method: "DELETE" },
                    requestSettings
                );
                modal.close();
                calendar.refetchEvents();
                showMessage("Fecha eliminada del calendario.", "success");
            } catch (error) {
                setFormError(error.message);
            } finally {
                deleteButton.disabled = false;
            }
        }

        async function completeEvent() {
            const eventId = eventIdInput.value;
            if (!eventId) {
                return;
            }

            completeButton.disabled = true;
            try {
                await shared.jsonRequest(
                    `/portal/api/calendar/events/${encodeURIComponent(eventId)}/complete`,
                    { method: "POST" },
                    requestSettings
                );
                modal.close();
                calendar.refetchEvents();
                showMessage("Fecha marcada como completada.", "success");
            } catch (error) {
                setFormError(error.message);
            } finally {
                completeButton.disabled = false;
            }
        }

        calendar = new FullCalendar.Calendar(
            container,
            Object.assign(shared.baseOptions(), {
                events: loadEvents,
                dateClick: function (info) {
                    openCreateModal(info.dateStr.slice(0, 10));
                },
                eventClick: openEditModal,
            })
        );

        calendar.render();
        form.addEventListener("submit", saveEvent);
        createButton.addEventListener("click", function () {
            openCreateModal(null);
        });
        deleteButton.addEventListener("click", deleteEvent);
        completeButton.addEventListener("click", completeEvent);
    });
})();
