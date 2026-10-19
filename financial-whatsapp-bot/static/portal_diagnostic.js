/* Ajustes de costos: sin registrar movimientos ni interpretar HTML del usuario. */
(() => {
    'use strict';
    const root = document.getElementById('diagnostic-app');
    if (!root) return;
    const navigation = document.querySelector('.portal-tabs'), activeTab = document.querySelector('.portal-tab.active');
    if (navigation && activeTab) navigation.scrollLeft = activeTab.offsetLeft - navigation.offsetLeft;
    const $ = id => document.getElementById(id);
    const status = $('diag-status'), content = $('diag-content'), form = $('diag-form');
    const save = $('diag-save'), reload = $('diag-reload');
    const coverage = $('diag-coverage');
    const money = value => value == null ? 'Pendiente' : new Intl.NumberFormat('es-CL', {style: 'currency', currency: 'CLP', maximumFractionDigits: 0}).format(value);
    const range = (min, max) => min === max ? money(max) : `${money(min)} – ${money(max)}`;
    let data = null, busy = false, conflicted = false, reviewDirty = false;
    const changed = new Set(), controls = new Map();
    function node(tag, text, cls) {
        const element = document.createElement(tag);
        if (text != null) element.textContent = text;
        if (cls) element.className = cls;
        return element;
    }
    function message(text, error = false) { status.textContent = text; status.className = error ? 'error' : ''; }
    async function api(options = {}) {
        const response = await fetch('/portal/api/diagnostic', {credentials: 'same-origin', cache: 'no-store', ...options});
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(typeof payload.detail === 'string' ? payload.detail : 'Revisa los montos y los campos obligatorios.');
            error.status = response.status;
            throw error;
        }
        return payload;
    }
    function updateDirty() {
        $('diag-dirty').textContent = changed.size ? `${changed.size} costo(s) con cambios. El resumen aún muestra el resultado guardado.` :
            reviewDirty ? 'Guarda la revisión para actualizar tu diagnóstico.' : 'Sin cambios pendientes';
        save.disabled = busy || conflicted || (!changed.size && !reviewDirty && !data?.recalculation_pending);
        save.textContent = coverage.checked ? 'Finalizar diagnóstico' : 'Guardar y recalcular';
        reload.disabled = busy;
    }
    function field(parent, title, type, value, options = []) {
        const label = node('label', title);
        const input = document.createElement(type === 'select' ? 'select' : 'input');
        if (type === 'select') {
            options.forEach(([key, name]) => { const option = node('option', name); option.value = key; input.append(option); });
        } else {
            input.type = type;
            if (type === 'number') { input.min = '0'; input.max = '9000000000000'; input.step = '1'; input.inputMode = 'numeric'; }
        }
        if (type === 'checkbox') { input.checked = Boolean(value); label.className = 'diag-check'; }
        else input.value = value ?? '';
        label.append(input); parent.append(label);
        return input;
    }
    function card(item) {
        const el = node('details', null, 'diag-cost');
        const summary = node('summary');
        summary.append(node('strong', item.title));
        const origin = item.mode === 'pending' ? 'Pendiente · falta un monto' : item.mode === 'excluded' ? 'No incluido en este escenario' :
            `${range(item.min, item.max)} · ${item.mode === 'estimate' ? 'Estimación FinancIAl' : 'Monto ingresado por ti'}`;
        summary.append(node('span', origin)); el.append(summary);
        el.append(node('p', item.scope));
        if (!item.estimate) el.append(node('p', 'Sin estimación suficiente: ingresa un monto informado por la municipalidad, un profesional o proveedor. Si no lo conoces, déjalo pendiente; no equivale a $0.'));
        const modes = [['pending', 'Lo revisaré después'], ['manual', 'Ingresar mi monto'], ['excluded', 'No lo necesito en este escenario']];
        if (item.estimate) modes.splice(1, 0, ['estimate', `Usar estimación: ${range(item.estimate.min, item.estimate.max)}`]);
        const mode = field(el, '¿Cómo quieres considerar este costo?', 'select', item.mode, modes);
        const fields = node('fieldset'); el.append(fields);
        const amount = field(fields, 'Monto total en pesos (antes de descontar abonos)', 'number', item.max);
        const frequency = field(fields, '¿Cada cuánto se paga?', 'select', item.frequency, [['', 'Selecciona una opción'], ['once', 'Una sola vez'], ['monthly', 'Cada mes'], ['annual', 'Una vez al año']]);
        const originInput = field(fields, '¿De dónde viene tu monto?', 'select', item.origin?.includes('cotización') ? 'quote' : item.origin?.includes('cálculo') ? 'own' : 'informed', [['informed', 'Monto que me informaron'], ['quote', 'Cotización recibida'], ['own', 'Mi propio cálculo']]);
        const paid = field(fields, '¿Cuánto ya pagaste? (0 si nada)', 'number', item.paid);
        const due = field(fields, 'Fecha en que pagarás el saldo', 'date', item.due_date);
        const included = field(fields, 'Ya está incluido en mis gastos mensuales', 'checkbox', item.included);
        function visibility() {
            const active = ['manual', 'estimate'].includes(mode.value), estimate = mode.value === 'estimate';
            fields.hidden = !active; fields.disabled = !active;
            amount.parentElement.hidden = estimate; amount.disabled = !active || estimate; amount.required = active && !estimate;
            originInput.parentElement.hidden = estimate; originInput.disabled = !active || estimate;
            frequency.disabled = !active || estimate; frequency.required = active;
            if (estimate) frequency.value = item.estimate.frequency;
            const monthly = frequency.value === 'monthly';
            for (const input of [paid, due]) { input.parentElement.hidden = monthly; input.disabled = !active || monthly; }
            paid.required = active && !monthly;
            const high = estimate ? item.estimate.max : Number(amount.value);
            due.required = active && !monthly && high > Number(paid.value || 0);
            included.parentElement.hidden = !monthly; included.disabled = !active || !monthly;
        }
        el.addEventListener('input', () => {
            changed.add(item.key); el.classList.add('changed'); visibility();
            coverage.checked = false; reviewDirty = Boolean(data?.coverage_confirmed); updateDirty();
        });
        visibility(); controls.set(item.key, {el, mode, amount, paid, frequency, due, included, origin: originInput, refresh: visibility});
        return el;
    }
    function render(next) {
        data = next; changed.clear(); controls.clear(); conflicted = false; reviewDirty = false; reload.hidden = true;
        content.hidden = !data.available;
        if (!data.available) { message(data.message); return; }
        coverage.checked = Boolean(data.coverage_confirmed); coverage.disabled = false;
        const assessment = $('diag-assessment');
        assessment.replaceChildren(node('h2', data.capacity ? `Capacidad económica: ${data.capacity.toUpperCase()}` : data.economic_status === 'not_evaluable' ? 'Escenario sin actividad mensual suficiente' : 'Completa tu diagnóstico'));
        assessment.firstElementChild.id = 'diag-assessment-title';
        assessment.append(node('p', data.capacity_explanation));
        if (data.capacity_details?.length) {
            const explanation = node('details', null, 'diag-explanation');
            explanation.append(node('summary', '¿Por qué recibí este nivel?'));
            data.capacity_details.forEach(text => explanation.append(node('p', text.replaceAll('*', ''))));
            assessment.append(explanation);
        }
        if (data.pending_requirements?.length) {
            const list = node('ul');
            data.pending_requirements.forEach(text => list.append(node('li', text)));
            assessment.append(list);
        }
        $('diag-summary').replaceChildren();
        const values = [['Resultado del mes observado · ' + data.period, money(data.summary.current_operating_result)],
                        [data.missing_count ? 'Pagos iniciales conocidos · revisa los pendientes' : 'Pagos iniciales del escenario revisado', range(data.summary.initial_remaining_min, data.summary.initial_remaining_max)],
                        ['Saldo mensual conocido tras gastos, retiros y deudas', money(data.summary.future_after_withdrawals_and_debt)]];
        values.forEach(([label, value]) => { const box = node('article', null, 'diag-metric'); box.append(node('small', label), node('strong', value)); $('diag-summary').append(box); });
        $('diag-costs').replaceChildren();
        [['permits', 'Trámites y permisos'], ['setup', 'Arreglos y equipos'], ['monthly', 'Gastos posteriores']].forEach(([key, title]) => {
            const section = node('section', null, 'diag-group'), cards = node('div', null, 'diag-cards');
            section.append(node('h2', title), cards);
            data.items.filter(item => item.group === key).forEach(item => cards.append(card(item)));
            $('diag-costs').append(section);
        });
        const sources = $('diag-sources'); sources.replaceChildren(node('p', `Catálogo: ${data.catalog_version}. Proyección de tres meses. La capacidad económica es orientativa; verifica permisos y cobros con cada organismo.`));
        data.assumptions.forEach(text => sources.append(node('p', text)));
        data.sources.forEach(source => sources.append(node('p', typeof source === 'string' ? source : Object.values(source).filter(value => typeof value === 'string').join(' · '))));
        message(data.recalculation_pending ? 'Tus cambios están guardados. Pulsa Guardar y recalcular para completar el resultado.' : 'Abre un costo para ajustarlo. Puedes cambiar varios antes de guardar.');
        updateDirty();
    }
    async function load() {
        if (busy) return;
        if ((changed.size || reviewDirty) && !window.confirm('¿Descartar los cambios de esta pantalla y cargar la versión guardada?')) return;
        busy = true; updateDirty(); message('Cargando tu diagnóstico…');
        try { render(await api()); }
        catch (error) { message(error.message, true); reload.hidden = false; if (error.status === 401) content.hidden = true; }
        finally { busy = false; updateDirty(); }
    }
    form.addEventListener('submit', async event => {
        event.preventDefault(); if (busy || conflicted) return;
        // Closed <details> must be opened before browser validation can focus a field.
        for (const key of changed) {
            const c = controls.get(key); c.el.open = true;
            if (!Array.from(c.el.querySelectorAll('input,select')).every(input => input.reportValidity())) return;
        }
        const changes = Array.from(changed).map(key => {
            const c = controls.get(key), monthly = c.frequency.value === 'monthly';
            return {key, mode: c.mode.value, amount: c.mode.value === 'manual' ? Number(c.amount.value) : null,
                    paid: monthly ? 0 : Number(c.paid.value || 0), frequency: c.frequency.value || null,
                    due_date: monthly ? null : c.due.value || null, included: monthly && c.included.checked, origin: c.origin.value};
        });
        busy = true; updateDirty(); form.querySelectorAll('input,select').forEach(input => input.disabled = true);
        const previous = data.summary.initial_remaining_max;
        try {
            const csrf = document.querySelector('meta[name="financial-csrf-token"]')?.content;
            render(await api({method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf || ''},
                body: JSON.stringify({diagnostic_id: data.diagnostic_id, lock_version: data.lock_version, request_id: crypto.randomUUID(), changes, coverage_confirmed: coverage.checked})}));
            message(`${data.capacity ? 'Diagnóstico finalizado. Capacidad: ' + data.capacity.toUpperCase() + '.' : 'Cambios guardados. Revisa los pendientes indicados arriba.'} Pagos iniciales: antes ${money(previous)}, ahora ${money(data.summary.initial_remaining_max)}. Consulta «mi diagnóstico» en WhatsApp.`);
            status.scrollIntoView({behavior: 'smooth', block: 'center'});
        } catch (error) {
            message(error.message, true); reload.hidden = false;
            // Do not blindly replay writes after a conflict or ambiguous server failure.
            conflicted = [409, 503, 401, 404].includes(error.status) || !error.status;
            if (error.status === 401) content.hidden = true;
            // Re-enable through each card's own visibility rules without losing edits.
            controls.forEach(c => { c.mode.disabled = false; c.refresh(); });
            coverage.disabled = false;
        } finally { busy = false; updateDirty(); }
    });
    form.noValidate = true;
    reload.addEventListener('click', load);
    coverage.addEventListener('change', () => { reviewDirty = coverage.checked !== Boolean(data?.coverage_confirmed); updateDirty(); });
    window.addEventListener('beforeunload', event => { if (changed.size || reviewDirty) { event.preventDefault(); event.returnValue = ''; } });
    load();
})();
