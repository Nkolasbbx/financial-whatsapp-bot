// FinancIAl — /admin/documentos
// 1) Subida múltiple: una tarjeta por archivo con sus rubros, vigencia,
//    fuente opcional y (solo para el equipo FinancIAl) su ámbito.
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
    const ambitos = config.ambitos || null;

    function isPdf(file) {
        return file.name.toLowerCase().endsWith(".pdf");
    }

    // Misma normalización que core/ingestion.py::nombre_fuente_desde_archivo:
    // el servidor reconoce así una precarga que nadie editó.
    function sourceNameFromFile(name) {
        const clean = name.replace(/\.[^.]*$/, "").replace(/[-_]+/g, " ").trim().replace(/\s+/g, " ");
        return (clean.charAt(0).toUpperCase() + clean.slice(1)).slice(0, 200);
    }

    // Un .md no se precarga: lo escrito aquí tendría prioridad sobre su título.
    function emptyMeta(file) {
        return {
            rubros: ["general"], desde: "", hasta: "", ambito: ambitos ? ambitos[0].value : "",
            fuente: isPdf(file) ? sourceNameFromFile(file.name) : "", url: "",
        };
    }

    function fieldValue(name) {
        return (form.elements[name] || {}).value || "";
    }

    function formatSize(bytes) {
        return bytes >= 1048576
            ? (bytes / 1048576).toFixed(1) + " MB"
            : Math.max(1, Math.round(bytes / 1024)) + " KB";
    }

    function readMeta() {
        meta = files.map((_, i) => ({
            rubros: Array.from(form.querySelectorAll(`input[name="rubros_${i}"]:checked`)).map((c) => c.value),
            desde: fieldValue(`vigencia_desde_${i}`),
            hasta: fieldValue(`vigencia_hasta_${i}`),
            ambito: fieldValue(`ambito_${i}`),
            fuente: fieldValue(`fuente_${i}`),
            url: fieldValue(`fuente_url_${i}`),
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

    function textField(name, label, value, type, placeholder) {
        const field = el("input", { type: type, name: name, maxlength: type === "url" ? "500" : "200", placeholder: placeholder });
        field.value = value;
        return el("label", {}, [document.createTextNode(label), field]);
    }

    function ambitoField(i, value) {
        const select = el("select", { name: `ambito_${i}`, required: "required" });
        ambitos.forEach((a) => {
            const option = el("option", { value: a.value, text: a.label });
            option.selected = a.value === value;
            select.appendChild(option);
        });
        return el("label", {}, [document.createTextNode("Ámbito"), select]);
    }

    function render() {
        cards.replaceChildren();
        files.forEach((file, i) => {
            const m = meta[i] || emptyMeta(file);
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
                ].concat(
                    ambitos ? [ambitoField(i, m.ambito)] : [],
                    [
                        el("fieldset", {}, [el("legend", { text: "Rubros aplicables" })].concat(rubros)),
                        dateField(`vigencia_desde_${i}`, "Vigente desde (opcional)", m.desde),
                        dateField(`vigencia_hasta_${i}`, "Vigente hasta (opcional — sin fecha no vence)", m.hasta),
                        textField(`fuente_${i}`, "Nombre de la fuente (opcional)", m.fuente, "text", isPdf(file) ? "Ej. Ordenanza de patentes 2026" : "Si lo dejas vacío, se usa el título del documento"),
                        textField(`fuente_url_${i}`, "URL de la fuente (opcional)", m.url, "url", "https://…"),
                    ]
                ))
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
                meta.push(emptyMeta(f));
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
