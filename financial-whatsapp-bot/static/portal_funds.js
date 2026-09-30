(function () {
    "use strict";

    const STATUS_ICONS = { met: "✅", failed: "❌", unknown: "⚠️" };
    const currencyFormatter = new Intl.NumberFormat("es-CL", {
        style: "currency",
        currency: "CLP",
        maximumFractionDigits: 0,
    });

    document.addEventListener("DOMContentLoaded", function () {
        const dashboard = document.getElementById("funds-dashboard");
        if (!dashboard) {
            return;
        }

        const csrfToken = document
            .querySelector('meta[name="financial-csrf-token"]')
            ?.getAttribute("content");
        const statusElement = document.getElementById("funds-status");
        const list = document.getElementById("funds-list");
        const message = document.getElementById("funds-message");
        const editButton = document.getElementById("funds-edit-button");
        const backdrop = document.getElementById("funds-modal-backdrop");
        const form = document.getElementById("funds-form");
        const fieldsContainer = document.getElementById("funds-fields");
        const formError = document.getElementById("funds-form-error");
        const saveButton = document.getElementById("funds-save-button");
        const cancelButton = document.getElementById("funds-cancel-button");
        const closeButton = document.getElementById("funds-modal-close");

        let fields = [];
        let lastFocused = null;

        function element(tag, className, text) {
            const node = document.createElement(tag);
            if (className) {
                node.className = className;
            }
            if (text !== undefined && text !== null) {
                node.textContent = text;
            }
            return node;
        }

        function showMessage(text, type) {
            message.textContent = text || "";
            message.className = text ? `calendar-message ${type || "info"}` : "";
        }

        function showStatus(text, isError) {
            statusElement.hidden = !text;
            statusElement.textContent = text || "";
            statusElement.className = isError ? "finance-status error" : "finance-status";
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

        async function fundsRequest(url, options) {
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

        function formatDate(isoDate) {
            const [year, month, day] = isoDate.split("-");
            return `${day}/${month}/${year}`;
        }

        function fundMeta(fund) {
            const parts = [];
            if (fund.entity) {
                parts.push(fund.entity);
            }
            if (fund.max_amount) {
                parts.push(`Hasta ${currencyFormatter.format(fund.max_amount)}`);
            }
            if (fund.closing_date) {
                const days = fund.days_remaining;
                const remaining = days === 0
                    ? "cierra hoy"
                    : `${days} ${days === 1 ? "día" : "días"}`;
                parts.push(`Cierra el ${formatDate(fund.closing_date)} (${remaining})`);
            }
            return parts.join(" · ");
        }

        function renderRequirement(requirement) {
            const item = element("li", `fund-requirement ${requirement.status}`);
            const icon = element("span", "fund-requirement-icon", STATUS_ICONS[requirement.status]);
            icon.setAttribute("aria-hidden", "true");
            item.appendChild(icon);

            const body = element("div", "fund-requirement-body");
            const title = element("strong", null, requirement.text);
            body.appendChild(title);

            const statusLabel = {
                met: "Cumplido",
                failed: requirement.blocking ? "Excluyente, no cumplido" : "Por resolver",
                unknown: "Por confirmar",
            }[requirement.status];
            body.appendChild(element("span", "fund-requirement-status", statusLabel));

            if (requirement.status !== "met") {
                if (requirement.recommendation) {
                    body.appendChild(
                        element("p", "fund-requirement-note", `💡 ${requirement.recommendation}`)
                    );
                }
                if (requirement.estimated_time) {
                    body.appendChild(
                        element("p", "fund-requirement-note", `⏱️ Tiempo estimado: ${requirement.estimated_time}`)
                    );
                }
                if (requirement.urgency) {
                    body.appendChild(element("p", "fund-requirement-note", requirement.urgency));
                }
            }
            item.appendChild(body);
            return item;
        }

        function renderFund(fund) {
            const card = element("article", `fund-card${fund.blocked ? " blocked" : ""}`);

            const header = element("div", "fund-card-header");
            const emoji = element("span", "fund-emoji", fund.emoji);
            emoji.setAttribute("aria-hidden", "true");
            header.appendChild(emoji);

            const titleBlock = element("div", "fund-title");
            titleBlock.appendChild(element("h2", null, fund.name));
            titleBlock.appendChild(element("p", "fund-meta", fundMeta(fund)));
            header.appendChild(titleBlock);

            const percentage = element("strong", "fund-percentage", `${fund.percentage}%`);
            percentage.setAttribute("aria-label", `Compatibilidad ${fund.percentage}%`);
            header.appendChild(percentage);
            card.appendChild(header);

            const track = element("div", "barra-fondo");
            const bar = element("div", "barra-progreso");
            bar.style.width = `${fund.percentage}%`;
            track.appendChild(bar);
            card.appendChild(track);

            card.appendChild(
                element(
                    "p",
                    "fund-counts",
                    `✅ ${fund.met} cumplidos · ❌ ${fund.failed} por resolver · ⚠️ ${fund.unknown} por confirmar`
                )
            );

            if (fund.blocked) {
                card.appendChild(
                    element(
                        "p",
                        "fund-blocked-note",
                        "⛔ Tienes un requisito excluyente no cumplido para este fondo."
                    )
                );
            }

            const details = element("details", "fund-details");
            details.appendChild(element("summary", null, "Ver checklist de requisitos"));
            const requirements = element("ul", "fund-requirements");
            fund.requirements.forEach(function (requirement) {
                requirements.appendChild(renderRequirement(requirement));
            });
            details.appendChild(requirements);
            card.appendChild(details);

            if (fund.link) {
                const link = element("a", "fund-link", "Ver bases oficiales");
                link.href = fund.link;
                link.target = "_blank";
                link.rel = "noopener noreferrer";
                card.appendChild(link);
            }
            return card;
        }

        function render(payload) {
            fields = payload.fields || [];
            editButton.disabled = fields.length === 0;
            list.replaceChildren();

            if (!payload.funds || payload.funds.length === 0) {
                showStatus(
                    "No encontramos fondos vigentes compatibles con tu perfil en este momento. Vuelve a consultar más adelante."
                );
                return;
            }

            showStatus("");
            payload.funds.forEach(function (fund) {
                list.appendChild(renderFund(fund));
            });
        }

        async function load() {
            showStatus("Cargando evaluación…");
            try {
                render(await fundsRequest("/portal/api/funds"));
            } catch (error) {
                showStatus(error.message, true);
            }
        }

        function renderField(field) {
            const label = element("label", "calendar-field");
            label.appendChild(element("span", null, field.label));
            if (field.question) {
                label.appendChild(element("small", "funds-field-question", field.question));
            }

            let input;
            if (field.type === "number") {
                input = document.createElement("input");
                input.type = "number";
                input.min = "0";
                input.step = "any";
                input.inputMode = "decimal";
                input.placeholder = field.unit ? `Monto en ${field.unit}` : "Número";
                if (field.answered && field.value !== null) {
                    input.value = field.value;
                }
            } else {
                input = document.createElement("select");
                const empty = element("option", null, "Sin responder");
                empty.value = "";
                input.appendChild(empty);
                field.options.forEach(function (option) {
                    const node = element("option", null, option.title);
                    node.value = option.id;
                    input.appendChild(node);
                });
                input.value = field.selected || "";
            }
            input.name = field.key;
            input.dataset.initial = input.value;
            label.appendChild(input);
            return label;
        }

        function openModal() {
            lastFocused = document.activeElement;
            formError.textContent = "";
            fieldsContainer.replaceChildren();
            fields.forEach(function (field) {
                fieldsContainer.appendChild(renderField(field));
            });
            backdrop.classList.add("visible");
            backdrop.setAttribute("aria-hidden", "false");
            document.body.classList.add("calendar-modal-open");
            fieldsContainer.querySelector("input, select")?.focus();
        }

        function closeModal() {
            backdrop.classList.remove("visible");
            backdrop.setAttribute("aria-hidden", "true");
            document.body.classList.remove("calendar-modal-open");
            lastFocused?.focus();
        }

        function collectChanges() {
            const answers = {};
            fieldsContainer.querySelectorAll("input, select").forEach(function (input) {
                if (input.value === input.dataset.initial) {
                    return;
                }
                const field = fields.find(function (item) {
                    return item.key === input.name;
                });
                if (field.type === "number") {
                    answers[field.key] = input.value === "" ? null : Number(input.value);
                } else if (input.value !== "") {
                    answers[field.key] = input.value;
                }
            });
            return answers;
        }

        form.addEventListener("submit", async function (event) {
            event.preventDefault();
            formError.textContent = "";

            const invalid = Array.from(
                fieldsContainer.querySelectorAll('input[type="number"]')
            ).find(function (input) {
                return !input.checkValidity();
            });
            if (invalid) {
                formError.textContent = "Ingresa un número válido mayor o igual a 0.";
                invalid.focus();
                return;
            }

            const answers = collectChanges();
            if (Object.keys(answers).length === 0) {
                closeModal();
                return;
            }

            saveButton.disabled = true;
            try {
                const payload = await fundsRequest("/portal/api/funds/answers", {
                    method: "PUT",
                    body: JSON.stringify({ answers: answers }),
                });
                render(payload);
                closeModal();
                showMessage("✅ Actualizamos tus datos y recalculamos tu evaluación.", "success");
            } catch (error) {
                formError.textContent = error.message;
            } finally {
                saveButton.disabled = false;
            }
        });

        editButton.addEventListener("click", openModal);
        cancelButton.addEventListener("click", closeModal);
        closeButton.addEventListener("click", closeModal);
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

        load();
    });
})();
