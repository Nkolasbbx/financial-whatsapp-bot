from pathlib import Path
import hashlib
import json
import shutil

workspace = Path(__file__).resolve().parents[1]
stage = workspace / 'tmp' / 'ingesta_pos_preparada'
root = (workspace.parent / 'Documentos diagnostico').resolve(strict=True)
destination = (root / 'ingesta_pos').resolve()
source_pdf = (root / 'fuentes_locales' / 'minsal_aranceles_res_331_2026.pdf').resolve(strict=True)
target_pdf = (destination / 'fuentes' / source_pdf.name).resolve()

# Comprobación de todas las rutas antes de crear, actualizar o trasladar archivos.
assert root.name == 'Documentos diagnostico'
assert destination.parent == root
assert source_pdf.is_relative_to(root)
assert target_pdf.is_relative_to(destination)
assert not destination.exists(), 'El destino ya existe; no se sobrescribe.'
expected = '56b89573b8803d0f44b141d7f903a457fca56e364f4174e02de3e62705911974'
assert hashlib.sha256(source_pdf.read_bytes()).hexdigest() == expected
manifest = json.loads((stage / '_control/manifiesto.json').read_text(encoding='utf-8'))
for entry in manifest['documents']:
    p = (stage / entry['path']).resolve(strict=True)
    assert p.is_relative_to(stage)
    assert hashlib.sha256(p.read_bytes()).hexdigest() == entry['sha256']

replacements = [
    (root / '01_fuentes_y_vigencia.md', 'fuentes_locales/minsal_aranceles_res_331_2026.pdf', 'ingesta_pos/fuentes/minsal_aranceles_res_331_2026.pdf'),
    (root / 'fuentes_locales/INVENTARIO.md', '](minsal_aranceles_res_331_2026.pdf)', '](../ingesta_pos/fuentes/minsal_aranceles_res_331_2026.pdf)'),
]
updates = []
for p, old, new in replacements:
    assert p.resolve(strict=True).is_relative_to(root)
    original = p.read_bytes()
    old_bytes, new_bytes = old.encode('utf-8'), new.encode('utf-8')
    assert original.count(old_bytes) == 1, f'Referencia inesperada en {p}'
    updates.append((p, original.replace(old_bytes, new_bytes)))

shutil.copytree(stage, destination)
target_pdf.parent.mkdir(parents=True, exist_ok=True)
shutil.move(str(source_pdf), str(target_pdf))
assert hashlib.sha256(target_pdf.read_bytes()).hexdigest() == expected
for p, updated in updates:
    p.write_bytes(updated)
print(f'Creada carpeta: {destination}')
print(f'Markdown de contenido: {len(manifest["documents"])}')
print('PDF MINSAL trasladado con SHA-256 conservado; 2 enlaces originales actualizados.')
