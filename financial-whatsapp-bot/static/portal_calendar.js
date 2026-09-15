(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        const container = document.getElementById("business-calendar");
        if (!container) {
            return;
        }

        if (!window.FullCalendar) {
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

        let calendar;

        function showMessage(message, type) {
            const element = document.getElementById("calendar-message");
            if (!element) {
                return;
            }
            element.textContent = message || "";
            element.className = message
                ? `calendar-message ${type || "info"}`
                : "";
        }

        function setFormError(message) {
            formError.textContent = message || "";
        }

        function detailMessage(detail) {
            if (Array.isArray(detail)) {
                return detail
                    .map(function (item) {
                        return item.msg || "Dato inválido";
                    })
                    .join(". ");
            }
            return detail || "No se pudo completar la operación";
        }

        async function calendarRequest(url, options) {
            const requestOptions = Object.assign({}, options || {});
            requestOptions.headers = Object.assign(
                {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken || "",
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
                if (response.status === 401) {
                    throw new Error(
                        "Tu sesión venció. Solicita un nuevo acceso desde WhatsApp."
                    );
                }
                throw new Error(detailMessage(payload?.detail));
            }

            return payload;
        }

        function chileInputValue(value) {
            const date = value instanceof Date ? value : new Date(value);
            const parts = new Intl.DateTimeFormat("sv-SE", {
                timeZone: "America/Santiago",
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

        function defaultDateTime(selectedDate) {
            if (selectedDate) {
                const candidate = `${selectedDate}T${defaultHour}`;
                const currentLocal = chileInputValue(new Date());
                if (candidate > currentLocal) {
                    return candidate;
                }
            }

            const nextHour = new Date(Date.now() + 60 * 60 * 1000);
            nextHour.setMinutes(0, 0, 0);
            return chileInputValue(nextHour);
        }

        function setEditable(enabled) {
            descriptionInput.disabled = !enabled;
            eventAtInput.disabled = !enabled;
            reminderInput.disabled = !enabled;
            saveButton.hidden = !enabled;
        }

        function openCreateModal(dateText) {
            form.reset();
            eventIdInput.value = "";
            descriptionInput.value = "";
            eventAtInput.value = defaultDateTime(dateText || null);
            reminderInput.value = "0";
            modalTitle.textContent = "Nueva fecha importante";
            deleteButton.hidden = true;
            completeButton.hidden = true;
            setEditable(true);
            setFormError("");
            openModal();
        }

        function openEditModal(info) {
            const event = info.event;
            const properties = event.extendedProps;
            const isActive = properties.status === "active";

            form.reset();
            eventIdInput.value = event.id;
            descriptionInput.value = properties.description || event.title;
            eventAtInput.value = chileInputValue(properties.event_at || event.start);
            reminderInput.value = String(properties.reminder_days_before || 0);
            modalTitle.textContent = isActive
                ? "Editar fecha importante"
                : "Fecha completada";
            deleteButton.hidden = !isActive;
            completeButton.hidden = !isActive;
            setEditable(isActive);
            setFormError("");
            openModal();
        }

        function openModal() {
            backdrop.classList.add("visible");
            backdrop.setAttribute("aria-hidden", "false");
            document.body.classList.add("calendar-modal-open");
            window.setTimeout(function () {
                descriptionInput.focus();
            }, 0);
        }

        function closeModal() {
            backdrop.classList.remove("visible");
            backdrop.setAttribute("aria-hidden", "true");
            document.body.classList.remove("calendar-modal-open");
            setFormError("");
        }

        function toCalendarEvent(event) {
            const overdue =
                event.status === "active" && new Date(event.event_at) < new Date();
            return {
                id: event.id,
                title: event.description,
                start: event.event_at,
                classNames: [
                    event.status === "completed"
                        ? "calendar-event-completed"
                        : overdue
                            ? "calendar-event-overdue"
                            : "calendar-event-active",
                ],
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
                    throw new Error(detailMessage(payload?.detail));
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
                await calendarRequest(
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
                    }
                );
                closeModal();
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
                await calendarRequest(
                    `/portal/api/calendar/events/${encodeURIComponent(eventId)}`,
                    { method: "DELETE" }
                );
                closeModal();
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
                await calendarRequest(
                    `/portal/api/calendar/events/${encodeURIComponent(eventId)}/complete`,
                    { method: "POST" }
                );
                closeModal();
                calendar.refetchEvents();
                showMessage("Fecha marcada como completada.", "success");
            } catch (error) {
                setFormError(error.message);
            } finally {
                completeButton.disabled = false;
            }
        }

        calendar = new FullCalendar.Calendar(container, {
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
            events: loadEvents,
            dateClick: function (info) {
                openCreateModal(info.dateStr.slice(0, 10));
            },
            eventClick: openEditModal,
        });

        calendar.render();
        form.addEventListener("submit", saveEvent);
        createButton.addEventListener("click", function () {
            openCreateModal(null);
        });
        closeButton.addEventListener("click", closeModal);
        deleteButton.addEventListener("click", deleteEvent);
        completeButton.addEventListener("click", completeEvent);
        backdrop.addEventListener("click", function (event) {
            if (event.target === backdrop) {
                closeModal();
            }
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && backdrop.classList.contains("visible")) {
                closeModal();
            }
        });
    });
})();
