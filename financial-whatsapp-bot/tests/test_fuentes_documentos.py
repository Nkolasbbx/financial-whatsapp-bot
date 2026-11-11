import unittest
from unittest.mock import MagicMock

import core.ia as ia
from scripts import normalizar_fuentes_documentos as script


class LineaCitaTests(unittest.TestCase):
    def test_con_url_y_revision(self):
        self.assertEqual(
            ia._linea_cita("Circular", "https://sii.cl/c", "2026-10-15", "2026-11-01"),
            "- Circular | https://sii.cl/c | fecha de revisión: 2026-10-15",
        )

    def test_sin_url_ni_revision_usa_la_fecha_de_carga(self):
        self.assertEqual(
            ia._linea_cita("Ordenanza · Recoleta", "", "", "2026-09-17"),
            "- Ordenanza · Recoleta | cargado el 2026-09-17",
        )

    def test_con_url_y_fecha_de_carga(self):
        self.assertEqual(
            ia._linea_cita("Ordenanza", "https://r.cl", "", "2026-09-17"),
            "- Ordenanza | https://r.cl | cargado el 2026-09-17",
        )

    def test_sin_datos_opcionales_queda_solo_el_nombre(self):
        self.assertEqual(ia._linea_cita("Ordenanza", "", "", ""), "- Ordenanza")


class NormalizarFuentesTests(unittest.TestCase):
    GRUPOS = [
        ("ordenanza-patentes.pdf", "Recoleta", "Panel admin — InnovaRecoleta", 12),
        (None, "Recoleta", "ingesta_panel", 3),
    ]

    def _conn(self, rowcount=12):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = self.GRUPOS
        cur.rowcount = rowcount
        return conn, cur

    def test_planifica_y_reporta_filas_sin_archivo(self):
        cambios, omitidos = script.planificar_cambios(self.GRUPOS)

        self.assertEqual(cambios[0]["fuente_nueva"], "Ordenanza patentes · Recoleta")
        self.assertEqual(omitidos, [("Recoleta", "ingesta_panel", 3)])

    def test_simulacion_no_escribe(self):
        conn, cur = self._conn()

        script.normalizar(conn, ["ingesta_panel"], aplicar=False)

        cur.execute.assert_called_once()
        self.assertEqual(cur.execute.call_args.args[1], ("Panel admin — %", ["ingesta_panel"]))
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    def test_apply_actualiza_y_confirma(self):
        conn, cur = self._conn()

        script.normalizar(conn, [], aplicar=True)

        self.assertEqual(
            cur.execute.call_args.args[1],
            ("Ordenanza patentes · Recoleta", "ordenanza-patentes.pdf", "Recoleta", "Panel admin — InnovaRecoleta"),
        )
        conn.commit.assert_called_once()

    def test_apply_revierte_si_cambia_la_cantidad_de_filas(self):
        conn, _cur = self._conn(rowcount=11)

        with self.assertRaises(RuntimeError):
            script.normalizar(conn, [], aplicar=True)

        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
