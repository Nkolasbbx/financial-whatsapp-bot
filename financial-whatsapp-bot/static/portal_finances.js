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

    function errorDetail(payload) {
        if (Array.isArray(payload?.detail)) {
            return payload.detail.map((item) => item.msg).join(". ");
        }
        return payload?.detail || "No se pudo cargar el resumen financiero";
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
        const netCard = document
            .getElementById("finance-net-total")
            .closest(".finance-summary-card");

        function setStatus(message, type) {
            status.textContent = message || "";
            status.className = `finance-status${type ? ` ${type}` : ""}`;
            status.hidden = !message;
        }

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

                renderDashboard(payload);
                const url = new URL(window.location.href);
                url.searchParams.set("tab", "finanzas");
                url.searchParams.set("month", month);
                window.history.replaceState({}, "", url);
            } catch (error) {
                setStatus(error.message || "No se pudo cargar el resumen financiero", "error");
            }
        }

        monthInput.addEventListener("change", function () {
            if (monthInput.value) {
                loadDashboard(monthInput.value);
            }
        });

        loadDashboard(dashboard.dataset.month || monthInput.value);
    });
})();
