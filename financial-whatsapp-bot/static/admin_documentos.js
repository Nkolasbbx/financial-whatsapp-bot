// FinancIAl — /admin/documentos
// 1) Subida múltiple: una tarjeta por archivo con sus rubros y vigencia.
// 2) Estado en vivo: consulta los jobs en cola/procesando y actualiza la tabla.
(function () {
    "use strict";

    const form = document.getElementById("upload-form");
    if (!form) return;

    const config = JSON.parse(form.dataset.config);
    const input = document.getElementById("archivos");
    const cards = document.getElementById("file-cards");
    const submit = form.querySelector('button[type="submit"]');
    let files = [];
    let meta = [];

    function formatSize(bytes) {
        return bytes >= 1048576
            ? (bytes / 1048576).toFixed(1) + " MB"
            : Math.max(1, Math.round(bytes / 1024)) + " KB";
    }

    function readMeta() {
        meta = files.map((_, i) => ({
            rubros: Array.from(form.querySelectorAll(`input[name="rubros_${i}"]:checked`)).map((c) => c.value),
            desde: (form.elements[`vigencia_desde_${i}`] || {}).value || "",
            hasta: (form.elements[`vigencia_hasta_${i}`] || {}).value || "",
        }));
    }

    function syncInput() {
        const transfer = new DataTransfer();
        files.forEach((f) => transfer.items.add(f));
        input.files = transfer.files;
    }

    function el(tag, attrs, children) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([k, v]) => {
            if (k === "text") node.textContent = v;
            else node.setAttribute(k, v);
        });
        (children || []).forEach((c) => node.appendChild(c));
        return node;
    }

    function dateField(name, label, value) {
        const field = el("input", { type: "date", name: name });
        field.value = value;
        field.addEventListener("click", () => {
            if (typeof field.showPicker === "function") {
                try { field.showPicker(); } catch (e) { /* input deshabilitado o sin soporte */ }
            }
        });
        return el("label", {}, [document.createTextNode(label), field]);
    }

    function render() {
        cards.replaceChildren();
        files.forEach((file, i) => {
            const m = meta[i] || { rubros: ["general"], desde: "", hasta: "" };
            const remove = el("button", { type: "button", class: "admin-file-remove", text: "Quitar" });
            remove.addEventListener("click", () => {
                readMeta();
                files.splice(i, 1);
                meta.splice(i, 1);
                syncInput();
                render();
            });

            const rubros = config.rubros.map((r) => {
                const box = el("input", { type: "checkbox", name: `rubros_${i}`, value: r.value });
                box.checked = m.rubros.includes(r.value);
                return el("label", { class: "admin-checkbox" }, [box, document.createTextNode(" " + r.label)]);
            });

            cards.appendChild(
                el("div", { class: "admin-file-card" }, [
                    el("div", { class: "admin-file-card-head" }, [
                        el("strong", { text: file.name }),
                        el("span", { class: "subtitulo", text: formatSize(file.size) }),
                        remove,
                    ]),
                    el("fieldset", {}, [el("legend", { text: "Rubros aplicables" })].concat(rubros)),
                    dateField(`vigencia_desde_${i}`, "Vigente desde (opcional)", m.desde),
                    dateField(`vigencia_hasta_${i}`, "Vigente hasta (opcional — sin fecha no vence)", m.hasta),
                ])
            );
        });
        const total = files.reduce((acc, f) => acc + f.size, 0);
        submit.disabled = files.length === 0;
        document.getElementById("batch-summary").textContent = files.length
            ? `${files.length} archivo(s), ${formatSize(total)} en total (máximo ${config.maxFiles} archivos / ${config.maxBatchMb} MB)`
            : "";
    }

    input.addEventListener("change", () => {
        readMeta();
        const nuevos = Array.from(input.files);
        // Volver a elegir archivos agrega a la selección (sin duplicar por nombre).
        nuevos.forEach((f) => {
            if (!files.some((x) => x.name === f.name)) {
                files.push(f);
                meta.push({ rubros: ["general"], desde: "", hasta: "" });
            }
        });
        syncInput();
        render();
    });

    form.addEventListener("submit", () => {
        submit.disabled = true;
        submit.textContent = "Subiendo…";
    });

    submit.disabled = true;

    // --- Estado en vivo de los jobs pendientes ---
    const POLL_MS = 3000;
    const MAX_POLLS = 200; // ~10 min; después el admin puede recargar a mano
    const banner = document.getElementById("live-banner");
    let polls = 0;

    function pendingRows() {
        return Array.from(document.querySelectorAll("tr[data-job-id]"));
    }

    function addBanner(kind, text) {
        const item = el("div", { class: `admin-flash ${kind}`, role: "status", text: text });
        banner.appendChild(item);
    }

    async function poll() {
        const rows = pendingRows();
        if (!rows.length || polls++ >= MAX_POLLS) return;

        let terminaron = false;
        await Promise.all(rows.map(async (row) => {
            try {
                const res = await fetch(`/admin/documentos/estado/${row.dataset.jobId}`, { credentials: "same-origin" });
                if (res.status === 401) { window.location.href = "/admin/login"; return; }
                if (!res.ok) return;
                const job = await res.json();
                const estado = config.estados[job.status] || [job.status, "en-cola"];
                const badge = row.querySelector(".js-estado");
                badge.replaceChildren(el("span", { class: `admin-badge ${estado[1]}`, text: estado[0] }));
                if (job.chunks_written !== null && job.chunks_written !== undefined) {
                    row.querySelector(".js-chunks").textContent = job.chunks_written;
                }
                if (job.status === "done") {
                    row.removeAttribute("data-job-id");
                    terminaron = true;
                    addBanner("ok", `«${job.file_name}» fue ingerido (${job.chunks_written || 0} fragmentos).`);
                } else if (job.status === "failed") {
                    row.removeAttribute("data-job-id");
                    terminaron = true;
                    addBanner("error", `«${job.file_name}» falló: ${job.error_message || "error desconocido"}`);
                }
            } catch (e) {
                // Error de red transitorio: se reintenta en el próximo ciclo.
            }
        }));

        if (pendingRows().length) {
            setTimeout(poll, POLL_MS);
        } else if (terminaron) {
            // Recarga para que aparezca el botón Eliminar; el banner vivo se pierde,
            // así que se deja un mensaje en sessionStorage para mostrarlo tras recargar.
            try { sessionStorage.setItem("docs-live-banner", banner.innerHTML); } catch (e) { /* sin storage */ }
            setTimeout(() => window.location.reload(), 1200);
        }
    }

    try {
        const previo = sessionStorage.getItem("docs-live-banner");
        if (previo) {
            sessionStorage.removeItem("docs-live-banner");
            banner.innerHTML = previo;
        }
    } catch (e) { /* sin storage */ }

    if (pendingRows().length) setTimeout(poll, POLL_MS);
})();
