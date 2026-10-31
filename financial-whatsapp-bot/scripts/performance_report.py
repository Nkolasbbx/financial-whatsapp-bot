"""Read opt-in metrics locally. Never contacts WhatsApp, Redis or Supabase."""
from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path


PREFIX = "FINANCIAL_PERF "


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def read_records(inputs, run=None):
    paths = set()
    for item in inputs:
        matches = glob.glob(str(item))
        if not matches:
            raise ValueError(f"No existe la entrada: {item}")
        for match in matches:
            path = Path(match)
            paths.update(path.rglob("*.jsonl") if path.is_dir() else [path])
    records = {}
    malformed = 0
    for path in sorted(paths):
        with path.open(encoding="utf-8-sig", errors="replace") as source:
            for line in source:
                if PREFIX in line:
                    line = line.split(PREFIX, 1)[1]
                elif not line.lstrip().startswith("{"):
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or row.get("schema") != 1:
                        continue
                    if row.get("phase") not in {"web", "worker"}:
                        raise ValueError("Invalid phase")
                    if not isinstance(row.get("trace_id"), str) or not isinstance(row.get("run"), str):
                        raise ValueError("Missing trace")
                    if not number(row.get("elapsed_ms")):
                        raise ValueError("Missing duration")
                    if run is not None and row.get("run") != run:
                        continue
                    key = (row["run"], row["trace_id"], row["phase"], row.get("attempt", 1))
                    # Deduplicate the same record copied from stdout and JSONL.
                    previous = records.get(key)
                    if previous is None or str(row.get("recorded_at", "")) >= str(previous.get("recorded_at", "")):
                        records[key] = row
                except (ValueError, TypeError):
                    malformed += 1
    return list(records.values()), malformed


def samples_from_records(records):
    grouped = defaultdict(list)
    for row in records:
        grouped[(row["run"], row["trace_id"])].append(row)
    samples = []
    for (run, _), rows in grouped.items():
        workers = [row for row in rows if row["phase"] == "worker"]
        final = max(workers or rows, key=lambda row: (row.get("attempt", 1), str(row.get("recorded_at", ""))))
        operations, stages = Counter(), Counter()
        errors = 0
        for row in rows:
            errors += int(row.get("outcome") == "failed")
            for operation, count in row.get("db_operations", {}).items():
                if number(count):
                    operations[operation] += count
            for stage, values in row.get("stages", {}).items():
                if isinstance(values, dict):
                    if number(values.get("ms")):
                        stages[stage] += values["ms"]
                    if number(values.get("errors")):
                        errors += values["errors"]
        clock_warning = any(row.get("clock_warning", False) for row in rows)
        complete = final.get("outcome") == "completed" and number(final.get("last_response_ms")) and final.get("accepted_sends", 0) > 0
        samples.append({
            "run": run, "case": final.get("case", "other"),
            "input_type": final.get("input_type", "other"),
            "outcome": final.get("outcome", "other"),
            "response_ms": final.get("last_response_ms") if complete and not clock_warning and not errors else None,
            "queue_ms": final.get("queue_wait_ms"),
            "operations": operations, "stages": stages,
            "errors": errors, "clock_warning": clock_warning,
            "retries": max(0, int(final.get("attempt", 1)) - 1) if workers else 0,
            "worker_only": bool(workers) and not any(row["phase"] == "web" for row in rows),
        })
    return samples


def percentile95(values):
    return sorted(values)[math.ceil(len(values) * .95) - 1] if values else None


def aggregate(samples):
    groups = defaultdict(list)
    for sample in samples:
        groups[(sample["case"], sample["input_type"])].append(sample)
    result = {}
    for key, group in groups.items():
        good = [sample for sample in group if sample["response_ms"] is not None and not sample["worker_only"]]
        durations = [sample["response_ms"] for sample in good]
        result[key] = {
            "samples": len(group), "valid": len(good),
            "median_ms": statistics.median(durations) if durations else None,
            "p95_ms": percentile95(durations),
            "db_median": statistics.median(sum(sample["operations"].values()) for sample in good) if good else None,
            "errors": sum(sample["errors"] > 0 or sample["outcome"] == "failed" for sample in group),
            "pending": sum(sample["outcome"] == "queued" or sample["worker_only"] for sample in group),
            "no_reply": sum(sample["outcome"] == "no_reply" for sample in group),
            "retries": sum(sample["retries"] for sample in group),
            "clock_warnings": sum(sample["clock_warning"] for sample in group),
        }
    return result


def fmt(value):
    return "—" if value is None else f"{value:.1f}"


def report(records, malformed=0, title="Línea base"):
    samples = samples_from_records(records)
    lines = [f"# {title}", "", "Tiempos en ms. Respuesta = último envío aceptado por Meta; no entrega ni lectura.", "",
             "| Caso | Entrada | Total | Válidas | Mediana | P95 | Consultas (mediana) | Con errores | Incompletas | Sin envío | Reintentos | Reloj inconsistente |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for (case, input_type), row in sorted(aggregate(samples).items()):
        lines.append(f"| {case} | {input_type} | {row['samples']} | {row['valid']} | {fmt(row['median_ms'])} | {fmt(row['p95_ms'])} | {fmt(row['db_median'])} | {row['errors']} | {row['pending']} | {row['no_reply']} | {row['retries']} | {row['clock_warnings']} |")
    stage_values = defaultdict(list)
    for sample in samples:
        if sample["response_ms"] is None or sample["worker_only"]:
            continue
        for stage, duration in sample["stages"].items():
            stage_values[(sample["case"], sample["input_type"], stage)].append(duration)
        if number(sample["queue_ms"]):
            stage_values[(sample["case"], sample["input_type"], "queue_wait_approx")].append(sample["queue_ms"])
    lines += ["", "## Etapas", "", "Duraciones inclusivas: routing contiene consultas; financial_processing contiene financial_parse. No sumar las filas.", "",
              "| Caso | Entrada | Etapa | Muestras | Mediana | P95 |", "|---|---|---|---:|---:|---:|"]
    for (case, input_type, stage), values in sorted(stage_values.items()):
        lines.append(f"| {case} | {input_type} | {stage} | {len(values)} | {fmt(statistics.median(values))} | {fmt(percentile95(values))} |")
    lines += ["", f"Etiquetas incluidas: {', '.join(sorted({sample['run'] for sample in samples})) or 'ninguna'}.",
              f"Registros descartados por formato inválido: {malformed}.",
              "Las métricas válidas excluyen errores observados, relojes inconsistentes y trazas incompletas. No certifican la corrección de la respuesta; comprobarla manualmente.",
              "Supabase cuenta execute(), no sentencias SQL ni reintentos internos del SDK. Con menos de 20 muestras por caso, el P95 es poco estable.",
              "La espera de cola y la respuesta del worker usan relojes de ambos procesos: son aproximadas y requieren relojes sincronizados."]
    return "\n".join(lines) + "\n"


def comparison(before, after):
    old = aggregate(samples_from_records(before))
    new = aggregate(samples_from_records(after))
    lines = ["# Comparación", "", "Reducción positiva = menor tiempo. Comparar solamente con los mismos datos, mensajes, ambiente y carga.", "",
             "| Caso | Entrada | N antes/después | Mediana antes | Mediana después | Reducción | P95 antes/después | Consultas antes/después |",
             "|---|---|---|---:|---:|---:|---|---|"]
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key, {}), new.get(key, {})
        previous, current = a.get("median_ms"), b.get("median_ms")
        reduction = f"{(previous - current) / previous * 100:.1f}%" if previous and current is not None else "—"
        lines.append(f"| {key[0]} | {key[1]} | {a.get('valid', 0)}/{b.get('valid', 0)} | {fmt(previous)} | {fmt(current)} | {reduction} | {fmt(a.get('p95_ms'))}/{fmt(b.get('p95_ms'))} | {fmt(a.get('db_median'))}/{fmt(b.get('db_median'))} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="Carpetas JSONL, archivos o patrones de logs de web y worker")
    parser.add_argument("--run", help="Filtrar por PERFORMANCE_RUN_LABEL")
    parser.add_argument("--compare", nargs="+", help="Carpetas o archivos de la ejecución posterior")
    parser.add_argument("--compare-run", help="Etiqueta de la ejecución posterior")
    parser.add_argument("--output", type=Path, help="Guardar el reporte Markdown (opcional)")
    args = parser.parse_args(argv)
    try:
        before, malformed = read_records(args.inputs, args.run)
        if not before:
            parser.error("No se encontraron métricas; revisa las rutas y la etiqueta.")
        output = report(before, malformed)
        if args.compare:
            after, invalid_after = read_records(args.compare, args.compare_run)
            if not after:
                parser.error("No hay métricas para comparar.")
            output = comparison(before, after) + "\n" + output + "\n" + report(after, invalid_after, "Ejecución posterior")
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
