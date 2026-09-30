(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        const card = document.getElementById("roadmap-card");
        const list = card && card.querySelector(".roadmap-list");
        if (!list) {
            return;
        }

        const csrfToken = document
            .querySelector('meta[name="financial-csrf-token"]')
            ?.getAttribute("content");
        const message = document.getElementById("roadmap-message");
        const undoButton = document.getElementById("roadmap-undo-button");
        const bar = document.getElementById("roadmap-bar");
        const count = document.getElementById("roadmap-count");

        function showMessage(text, type) {
            message.textContent = text || "";
            message.className = text ? `calendar-message ${type || "info"}` : "";
        }

        function setBusy(busy) {
            card.querySelectorAll(".hito-listo, #roadmap-undo-button").forEach(
                function (button) {
                    button.disabled = busy;
                }
            );
        }

        function setExpanded(step, expanded) {
            step
                .querySelector(".roadmap-step-toggle")
                .setAttribute("aria-expanded", expanded ? "true" : "false");
            step.querySelector(".roadmap-step-panel").hidden = !expanded;
        }

        function setState(step, state) {
            step.classList.remove("done", "current", "upcoming");
            step.classList.add(state);
        }

        function focusCurrent() {
            const current = list.querySelector(".roadmap-step.current");
            if (current) {
                current.querySelector(".hito-listo").focus();
            }
        }

        function updateProgress(result) {
            bar.setAttribute("aria-valuenow", result.percentage);
            bar.querySelector(".barra-progreso").style.width = `${result.percentage}%`;
            count.textContent = `${result.completed} de ${result.total} trámites completados`;
            undoButton.hidden = result.completed === 0;
        }

        async function roadmapRequest(url) {
            const response = await fetch(url, {
                method: "POST",
                headers: { "X-CSRF-Token": csrfToken || "" },
            });
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
                throw new Error(
                    (payload && typeof payload.detail === "string" && payload.detail)
                    || "No se pudo actualizar tu roadmap"
                );
            }
            return payload;
        }

        function advance(result) {
            const current = list.querySelector(".roadmap-step.current");
            const next = current.nextElementSibling;

            setState(current, "done");
            setExpanded(current, false);
            if (next) {
                setState(next, "current");
                setExpanded(next, true);
            }
            updateProgress(result);
        }

        function goBack(result) {
            const current = list.querySelector(".roadmap-step.current");
            const doneSteps = list.querySelectorAll(".roadmap-step.done");
            const lastDone = doneSteps[doneSteps.length - 1];

            if (current) {
                setState(current, "upcoming");
                setExpanded(current, false);
            }
            setState(lastDone, "current");
            setExpanded(lastDone, true);
            updateProgress(result);
        }

        list.addEventListener("click", async function (event) {
            const toggle = event.target.closest(".roadmap-step-toggle");
            if (toggle) {
                const step = toggle.closest(".roadmap-step");
                setExpanded(step, toggle.getAttribute("aria-expanded") !== "true");
                return;
            }

            const doneButton = event.target.closest(".hito-listo");
            if (!doneButton) {
                return;
            }

            const step = doneButton.closest(".roadmap-step");
            const milestoneId = encodeURIComponent(step.dataset.milestoneId);
            setBusy(true);
            showMessage("Guardando…", "info");
            try {
                const result = await roadmapRequest(
                    `/portal/api/roadmap/milestones/${milestoneId}/done`
                );
                if (result.formalized) {
                    // El último paso convierte el perfil en formalizado: el
                    // servidor renderiza esa vista. Los botones quedan
                    // deshabilitados hasta recargar.
                    const total = list.children.length;
                    advance({ percentage: 100, completed: total, total: total });
                    showMessage("🎉 ¡Completaste el 100% de tu formalización!", "success");
                    window.setTimeout(function () {
                        window.location.reload();
                    }, 1800);
                    return;
                }
                setBusy(false);
                advance(result);
                focusCurrent();
                showMessage(`✅ Completaste: ${result.title}`, "success");
            } catch (error) {
                showMessage(error.message, "error");
                setBusy(false);
            }
        });

        undoButton.addEventListener("click", async function () {
            setBusy(true);
            showMessage("Guardando…", "info");
            try {
                const result = await roadmapRequest("/portal/api/roadmap/undo");
                setBusy(false);
                goBack(result);
                focusCurrent();
                showMessage(`↩️ Volviste a dejar pendiente: ${result.title}`, "success");
            } catch (error) {
                showMessage(error.message, "error");
                setBusy(false);
            }
        });
    });
})();
