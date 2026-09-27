(function () {
    "use strict";

    const CATEGORY_LABELS = {
        ventas: "Ventas",
        servicios: "Servicios",
        aportes_capital: "Aportes o capital",
        otros_ingresos: "Otros ingresos",
        insumos_mercaderia: "Insumos y mercadería",
        transporte: "Transporte",
        arriendo_servicios: "Arriendo y servicios",
        permisos_tramites: "Permisos y trámites",
        marketing: "Marketing",
        equipamiento: "Equipamiento",
        otros_gastos: "Otros gastos",
    };
    const CATEGORIES_BY_TYPE = {
        income: ["ventas", "servicios", "aportes_capital", "otros_ingresos"],
        expense: ["insumos_mercaderia", "transporte", "arriendo_servicios",
            "permisos_tramites", "marketing", "equipamiento", "otros_gastos"],
    };

    const clpFormatter = new Intl.NumberFormat("es-CL", {
        style: "currency",
        currency: "CLP",
        maximumFractionDigits: 0,
    });

    const dateFormatter = new Intl.DateTimeFormat("es-CL", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
    });

    function formatCurrency(value) {
        return clpFormatter.format(Number(value) || 0);
    }

    function formatDate(value) {
        const parts = String(value || "").split("-").map(Number);
        if (parts.length !== 3 || parts.some(Number.isNaN)) {
            return value || "";
        }
        return dateFormatter.format(new Date(parts[0], parts[1] - 1, parts[2]));
    }

    function categoryLabel(category) {
        return CATEGORY_LABELS[category] || String(category || "Sin categoría")
            .replaceAll("_", " ");
    }

    function errorDetail(payload, fallback = "No se pudo cargar el resumen financiero") {
        if (Array.isArray(payload?.detail)) {
            return "Revisa el monto, la fecha y los campos obligatorios del movimiento.";
        }
        return payload?.detail || fallback;
    }

    document.addEventListener("DOMContentLoaded", function () {
        const dashboard = document.getElementById("finance-dashboard");
        if (!dashboard) {
            return;
        }

        const monthInput = document.getElementById("finance-month");
        const status = document.getElementById("finance-status");
        const content = document.getElementById("finance-content");
        const empty = document.getElementById("finance-empty");
        const details = document.getElementById("finance-details");
        const entry = document.getElementById("finance-entry");
        const form = document.getElementById("finance-form");
        const fields = document.getElementById("finance-fields");
        const typeInput = document.getElementById("finance-type");
        const amountInput = document.getElementById("finance-amount");
        const categoryInput = document.getElementById("finance-category");
        const dateInput = document.getElementById("finance-date");
        const descriptionInput = document.getElementById("finance-description");
        const saveButton = document.getElementById("finance-save");
        const saveStatus = document.getElementById("finance-save-status");
        const incomeButton = document.getElementById("finance-add-income");
        const expenseButton = document.getElementById("finance-add-expense");
        const csrfToken = document.querySelector('meta[name="financial-csrf-token"]')?.content;
        let saving = false;
        let pendingSubmission = null;
        let loadSequence = 0;
        const netCard = document
            .getElementById("finance-net-total")
            .closest(".finance-summary-card");

        function setStatus(message, type) {
            status.textContent = message || "";
            status.className = `finance-status${type ? ` ${type}` : ""}`;
            status.hidden = !message;
        }

        function setSaveStatus(message, type) {
            saveStatus.textContent = message;
            saveStatus.className = `finance-status${type ? ` ${type}` : ""}`;
            saveStatus.hidden = !message;
        }

        function updateMovementType() {
            const isIncome = typeInput.value === "income";
            categoryInput.replaceChildren();
            CATEGORIES_BY_TYPE[typeInput.value].forEach((category) => {
                categoryInput.add(new Option(categoryLabel(category), category));
            });
            document.getElementById("finance-entry-title").textContent = isIncome
                ? "Registrar ingreso" : "Registrar gasto";
            saveButton.textContent = isIncome ? "Guardar ingreso" : "Guardar gasto";
            descriptionInput.placeholder = isIncome
                ? "Ej. Venta de productos" : "Ej. Compra de insumos";
        }

        function openEntry(type) {
            typeInput.value = type;
            updateMovementType();
            entry.hidden = false;
            setSaveStatus("");
            amountInput.focus();
        }

        incomeButton.addEventListener("click", () => openEntry("income"));
        expenseButton.addEventListener("click", () => openEntry("expense"));
        typeInput.addEventListener("change", updateMovementType);
        descriptionInput.addEventListener("input", () => descriptionInput.setCustomValidity(""));
        document.getElementById("finance-cancel").addEventListener("click", () => {
            const returnButton = typeInput.value === "income" ? incomeButton : expenseButton;
            form.reset();
            descriptionInput.setCustomValidity("");
            pendingSubmission = null;
            entry.hidden = true;
            setSaveStatus("");
            returnButton.focus();
        });

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (saving) return;
            descriptionInput.setCustomValidity(descriptionInput.value.trim()
                ? "" : "Escribe una descripción del movimiento.");
            if (!form.reportValidity()) return;
            const amount = Number(amountInput.value);
            if (!Number.isSafeInteger(amount) || amount <= 0) {
                setSaveStatus("Ingresa un monto mayor que cero, sin decimales.", "error");
                return;
            }
            const data = {
                movement_type: typeInput.value,
                amount,
                category: categoryInput.value,
                description: descriptionInput.value.trim(),
                occurred_on: dateInput.value,
            };
            const fingerprint = JSON.stringify(data);
            if (!pendingSubmission || pendingSubmission.fingerprint !== fingerprint) {
                pendingSubmission = { fingerprint, requestId: crypto.randomUUID() };
            }
            saving = true;
            fields.disabled = incomeButton.disabled = expenseButton.disabled = true;
            saveButton.textContent = "Guardando…";
            form.setAttribute("aria-busy", "true");
            setSaveStatus("");
            try {
                const response = await fetch("/portal/api/finances/movements", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Accept: "application/json",
                        "X-CSRF-Token": csrfToken || "",
                    },
                    body: JSON.stringify({ ...data, request_id: pendingSubmission.requestId }),
                });
                const payload = await response.json().catch(() => null);
                if (!response.ok) {
                    if (response.status === 401) {
                        throw new Error("Tu sesión venció. Solicita un nuevo acceso desde WhatsApp.");
                    }
                    throw new Error(errorDetail(payload, "No pudimos confirmar el guardado. Reintenta con los mismos datos."));
                }
                pendingSubmission = null;
                form.reset();
                entry.hidden = true;
                monthInput.value = data.occurred_on.slice(0, 7);
                const label = data.movement_type === "income" ? "Ingreso" : "Gasto";
                setSaveStatus(`${label} de ${formatCurrency(amount)} registrado correctamente.`, "success");
                const refreshed = await loadDashboard(monthInput.value);
                if (!refreshed) {
                    setSaveStatus(`${label} guardado. No pudimos actualizar el resumen; vuelve a seleccionar el mes.`, "success");
                }
            } catch (error) {
                setSaveStatus(error instanceof TypeError
                    ? "No pudimos confirmar el guardado por un problema de conexión. Reintenta con los mismos datos."
                    : error.message, "error");
            } finally {
                saving = false;
                fields.disabled = incomeButton.disabled = expenseButton.disabled = false;
                form.removeAttribute("aria-busy");
                saveButton.textContent = typeInput.value === "income" ? "Guardar ingreso" : "Guardar gasto";
                if (entry.hidden) {
                    (data.movement_type === "income" ? incomeButton : expenseButton).focus();
                }
            }
        });

        function renderCategories(containerId, categories, type) {
            const container = document.getElementById(containerId);
            container.replaceChildren();

            if (!categories.length) {
                const message = document.createElement("p");
                message.className = "finance-category-empty";
                message.textContent = type === "income"
                    ? "No hay ingresos registrados."
                    : "No hay gastos registrados.";
                container.appendChild(message);
                return;
            }

            const maximum = Math.max(...categories.map((item) => Number(item.total) || 0), 1);
            categories.forEach((item) => {
                const row = document.createElement("div");
                row.className = `finance-category-row ${type}`;

                const heading = document.createElement("div");
                heading.className = "finance-category-heading";

                const label = document.createElement("span");
                label.textContent = categoryLabel(item.category);
                const total = document.createElement("span");
                total.textContent = formatCurrency(item.total);
                heading.append(label, total);

                const track = document.createElement("div");
                track.className = "finance-category-track";
                const bar = document.createElement("div");
                bar.className = "finance-category-bar";
                bar.style.width = `${Math.max(4, Math.round((Number(item.total) / maximum) * 100))}%`;
                track.appendChild(bar);

                row.append(heading, track);
                container.appendChild(row);
            });
        }

        function renderMovements(movements) {
            const list = document.getElementById("finance-movement-list");
            list.replaceChildren();

            movements.forEach((movement) => {
                const row = document.createElement("article");
                row.className = `finance-movement-row ${movement.movement_type}`;

                const information = document.createElement("div");
                const description = document.createElement("div");
                description.className = "finance-movement-description";
                description.textContent = movement.description;
                const metadata = document.createElement("div");
                metadata.className = "finance-movement-meta";
                metadata.textContent = `${categoryLabel(movement.category)} · ${formatDate(movement.occurred_on)}`;
                information.append(description, metadata);

                const amount = document.createElement("div");
                amount.className = "finance-movement-amount";
                amount.textContent = `${movement.movement_type === "income" ? "+" : "−"} ${formatCurrency(movement.amount)}`;

                row.append(information, amount);
                list.appendChild(row);
            });
        }

        function renderDashboard(data) {
            document.getElementById("finance-income-total").textContent =
                formatCurrency(data.income_total);
            document.getElementById("finance-expense-total").textContent =
                formatCurrency(data.expense_total);
            document.getElementById("finance-net-total").textContent =
                formatCurrency(data.net_total);
            document.getElementById("finance-movement-count").textContent =
                String(data.movement_count);

            netCard.classList.toggle("positive", Number(data.net_total) >= 0);
            netCard.classList.toggle("negative", Number(data.net_total) < 0);

            const hasMovements = Number(data.movement_count) > 0;
            empty.hidden = hasMovements;
            details.hidden = !hasMovements;

            if (hasMovements) {
                renderCategories(
                    "finance-income-categories",
                    data.income_categories || [],
                    "income"
                );
                renderCategories(
                    "finance-expense-categories",
                    data.expense_categories || [],
                    "expense"
                );
                renderMovements(data.movements || []);
            }

            content.hidden = false;
            setStatus("");
        }

        async function loadDashboard(month) {
            const sequence = ++loadSequence;
            setStatus("Cargando movimientos…");
            content.hidden = true;

            try {
                const response = await fetch(
                    `/portal/api/finances/dashboard?month=${encodeURIComponent(month)}`,
                    { headers: { Accept: "application/json" } }
                );
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
                    throw new Error(errorDetail(payload));
                }

                if (sequence !== loadSequence) return false;
                renderDashboard(payload);
                const url = new URL(window.location.href);
                url.searchParams.set("tab", "finanzas");
                url.searchParams.set("month", month);
                window.history.replaceState({}, "", url);
                return true;
            } catch (error) {
                if (sequence === loadSequence) {
                    setStatus(error.message || "No se pudo cargar el resumen financiero", "error");
                }
                return false;
            }
        }

        monthInput.addEventListener("change", function () {
            if (monthInput.value) {
                setSaveStatus("");
                loadDashboard(monthInput.value);
            }
        });

        loadDashboard(dashboard.dataset.month || monthInput.value);
    });
})();
