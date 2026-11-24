import csv
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from source import cli, corrector, exporter, implementation, pipeline, sqd_manipulator


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestCLIArgParsing(unittest.TestCase):
    def test_parse_two_gates_defaults(self):
        args = cli.parse_args(
            ["--cli", "--connect", "./data/sqd/AND", "./data/sqd/OR"]
        )
        self.assertTrue(args.cli)
        self.assertFalse(args.batch)
        self.assertEqual(args.connect, ["./data/sqd/AND", "./data/sqd/OR"])
        self.assertEqual(args.connector, "FIFO")
        self.assertEqual(args.simulator, "simanneal")
        self.assertIsNone(args.output)
        self.assertEqual(args.wires, 1)
        self.assertEqual(args.fixes, 0)
        self.assertEqual(args.sim_param_overrides, {})

    def test_parse_three_gates_case_insensitive_connector_and_sim_params(self):
        args = cli.parse_args(
            [
                "--cli",
                "--connect",
                "./data/sqd/AND",
                "./data/sqd/AND",
                "./data/sqd/OR",
                "--connector",
                "corner",
                "--simulator",
                "quickexact",
                "--output",
                "/tmp/out",
                "--sim-param",
                "muzm=-0.28",
                "--sim-param",
                "anneal_cycles=500",
                "--sim-param",
                "T_schedule=exponential",
            ]
        )
        self.assertEqual(len(args.connect), 3)
        self.assertEqual(args.connector, "CORNER")
        self.assertEqual(args.simulator, "quickexact")
        self.assertEqual(args.output, "/tmp/out")
        self.assertEqual(
            args.sim_param_overrides,
            {"muzm": -0.28, "anneal_cycles": 500, "T_schedule": "exponential"},
        )

    def test_cli_requires_at_least_two_gates(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.parse_args(["--cli", "--connect", "./data/sqd/AND"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_cli_requires_connect_or_batch(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.parse_args(["--cli"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_cli_rejects_unknown_connector(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.parse_args(
                [
                    "--cli",
                    "--connect",
                    "./data/sqd/AND",
                    "./data/sqd/OR",
                    "--connector",
                    "UNKNOWN",
                ]
            )
        self.assertNotEqual(ctx.exception.code, 0)

    def test_batch_parsing_defaults_and_explicit_requirement(self):
        args = cli.parse_args(["--cli", "--batch"])
        self.assertTrue(args.batch)
        self.assertIsNone(args.gate_files)
        self.assertIsNone(args.numbergates)
        self.assertEqual(args.fixes, 5)

        args_custom = cli.parse_args(
            [
                "--cli",
                "--batch",
                "--gate_files",
                "./data/sqd/AND",
                "./data/sqd/OR",
                "--numbergates",
                "2",
                "--fixes",
                "1",
            ]
        )
        self.assertTrue(args_custom.batch)
        self.assertEqual(args_custom.gate_files, ["./data/sqd/AND", "./data/sqd/OR"])
        self.assertEqual(args_custom.numbergates, 2)
        self.assertEqual(args_custom.fixes, 1)

    def test_batch_flags_require_explicit_batch(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.parse_args(["--cli", "--gate_files", "./data/sqd/AND"])
        self.assertNotEqual(ctx.exception.code, 0)

        with self.assertRaises(SystemExit) as ctx:
            cli.parse_args(
                [
                    "--cli",
                    "--connect",
                    "./data/sqd/AND",
                    "./data/sqd/OR",
                    "--numbergates",
                    "2",
                ]
            )
        self.assertNotEqual(ctx.exception.code, 0)

    def test_batch_and_connect_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.parse_args(
                [
                    "--cli",
                    "--batch",
                    "--connect",
                    "./data/sqd/AND",
                    "./data/sqd/OR",
                ]
            )
        self.assertNotEqual(ctx.exception.code, 0)


class TestResolutionAndPipeline(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        os.chdir(PROJECT_ROOT)

    def tearDown(self):
        os.chdir(self._orig_cwd)

    def test_resolve_gate_path_file_and_folder(self):
        direct = pipeline.resolve_gate_path("./data/sqd/AND/AND0.28.sqd")
        self.assertTrue(direct.endswith("AND0.28.sqd"))

        folder = pipeline.resolve_gate_path("./data/sqd/AND")
        self.assertTrue(folder.endswith("AND0.28.sqd"))

        exact_folder = pipeline.resolve_gate_path("./data/sqd/INPUT")
        self.assertTrue(exact_folder.endswith("INPUT.sqd"))

    def test_resolve_gate_path_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            pipeline.resolve_gate_path("./data/sqd/DOES_NOT_EXIST")

    def test_resolve_simulator_and_config_roundtrip(self):
        sim_exec, sim_physeng = pipeline.resolve_simulator("simanneal")
        self.assertTrue(sim_physeng.endswith("simanneal.physeng"))
        self.assertTrue(Path(sim_exec).is_file())

        sim_exec_dir, sim_physeng_dir = pipeline.resolve_simulator(
            "./data/simulators/simanneal"
        )
        self.assertEqual(Path(sim_exec_dir).resolve(), Path(sim_exec).resolve())
        self.assertEqual(Path(sim_physeng_dir).resolve(), Path(sim_physeng).resolve())

        params = pipeline.get_simulator_params(sim_physeng)
        self.assertIn("muzm", params)
        self.assertEqual(params["muzm"]["val"], -0.28)

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_sim = Path(tmpdir) / "sim.physeng"
            fake_sim.write_text("<physeng/>")
            pipeline.save_simulator_config(fake_sim, {"muzm": -0.3, "anneal_cycles": 250})
            reloaded = pipeline.load_simulator_config(fake_sim)
            self.assertEqual(reloaded, {"muzm": -0.3, "anneal_cycles": 250})

    def test_connect_gates_fifo_and_corner_error(self):
        g1 = pipeline.resolve_gate_path("./data/sqd/AND")
        g2 = pipeline.resolve_gate_path("./data/sqd/AND")
        conn = pipeline.connect_gates([g1, g2], wire_length=1, strategy="FIFO")
        self.assertEqual(conn.gate.name, "Circuit_AND0_AND0")
        self.assertTrue(len(conn.gate.db_dots) > 0)
        self.assertEqual(len(conn.gate.gate_metadata), 2)

        with self.assertRaises(NotImplementedError):
            pipeline.connect_gates([g1, g2], wire_length=1, strategy="CORNER")

    def test_generate_unique_batch_combos_and_default_max_gates(self):
        g_and = pipeline.resolve_gate_path("./data/sqd/AND")
        g_or = pipeline.resolve_gate_path("./data/sqd/OR")

        combos_1 = pipeline.generate_unique_batch_combos([g_and, g_or], max_gates=1)
        self.assertEqual(len(combos_1), 2)

        combos_2 = pipeline.generate_unique_batch_combos([g_and, g_or], max_gates=2)
        self.assertGreater(len(combos_2), len(combos_1))

        # Verify default max_gates = min(len(gate_files), 3) with a 1-gate-file batch
        with tempfile.TemporaryDirectory() as tmpdir:
            res = pipeline.run_batch_simulation(
                gate_files=[g_and],
                max_gates=None,
                output_dir=tmpdir,
                max_corrections=0,
                cooldown=False,
            )
            self.assertEqual(res["max_gates"], 1)
            self.assertEqual(res["total_combinations"], 1)
            self.assertTrue(res["summary_csv"].is_file())
            self.assertTrue(res["expressions_file"].is_file())

    def test_end_to_end_cli_pipeline_to_custom_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = subprocess.run(
                [
                    sys.executable,
                    "app.py",
                    "--cli",
                    "--connect",
                    "./data/sqd/AND",
                    "./data/sqd/AND/AND0.28.sqd",
                    "--connector",
                    "FIFO",
                    "--simulator",
                    "simanneal",
                    "--output",
                    tmpdir,
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"CLI failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
            )

            out_dir = Path(tmpdir) / "Circuit_AND0_AND0"
            self.assertTrue((out_dir / "Circuit_AND0_AND0.sqd").is_file())
            self.assertTrue((out_dir / "truth_table.png").is_file())
            self.assertTrue((out_dir / "simulation_config.txt").is_file())
            gate_pngs = list(out_dir.glob("specific_gate_*.png"))
            self.assertGreater(len(gate_pngs), 0)

            config_text = (out_dir / "simulation_config.txt").read_text()
            self.assertIn("simanneal", config_text)

    def test_end_to_end_cli_batch_and_resume(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                sys.executable,
                "app.py",
                "--cli",
                "--batch",
                "--gate_files",
                "./data/sqd/AND",
                "./data/sqd/OR",
                "--numbergates",
                "1",
                "--connector",
                "FIFO",
                "--simulator",
                "simanneal",
                "--output",
                tmpdir,
            ]
            proc1 = subprocess.run(
                cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
            )
            self.assertEqual(
                proc1.returncode,
                0,
                msg=f"Batch CLI failed:\nSTDOUT:\n{proc1.stdout}\nSTDERR:\n{proc1.stderr}",
            )

            summary_csv = Path(tmpdir) / "summary.csv"
            expressions_json = Path(tmpdir) / "saved_expressions.json"
            self.assertTrue(summary_csv.is_file())
            self.assertTrue(expressions_json.is_file())

            with open(summary_csv, "r") as f:
                rows = list(csv.DictReader(f))
            data_rows = [r for r in rows if r["RowType"] == "DATA"]
            summary_rows = [r for r in rows if r["RowType"] == "SUMMARY"]
            self.assertEqual(len(data_rows), 2)
            self.assertEqual(len(summary_rows), 3)

            saved_exprs = json.loads(expressions_json.read_text())
            self.assertEqual(len(saved_exprs), 2)

            pngs = list(Path(tmpdir).rglob("*_truth_table.png"))
            self.assertEqual(len(pngs), 2)
            configs = list(Path(tmpdir).rglob("simulation_config.txt"))
            self.assertEqual(len(configs), 2)

            # Re-running the same batch against the same output dir must resume & skip
            proc2 = subprocess.run(
                cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
            )
            self.assertEqual(proc2.returncode, 0, msg=proc2.stderr)
            self.assertIn("Resuming from last progress", proc2.stdout)
            self.assertIn("Skipping already completed combination", proc2.stdout)

    def test_clustercomplete_resolution_and_cli_execution(self):
        # 1. Resolve by case-insensitive name and by directory path
        sim_exec, sim_physeng = pipeline.resolve_simulator("ClusterComplete")
        self.assertTrue(sim_physeng.endswith("clustercomplete.physeng"))
        self.assertTrue(Path(sim_exec).is_file())

        sim_exec_dir, sim_physeng_dir = pipeline.resolve_simulator(
            "./data/simulators/clustercomplete"
        )
        self.assertEqual(Path(sim_exec_dir).resolve(), Path(sim_exec).resolve())
        self.assertEqual(Path(sim_physeng_dir).resolve(), Path(sim_physeng).resolve())

        cfg = pipeline.load_simulator_config(sim_physeng)
        self.assertIn("autofail", cfg)
        self.assertEqual(cfg["muzm"], -0.28)
        self.assertEqual(cfg["eps_r"], 4.1)
        self.assertEqual(cfg["debye_length"], 1.8)

        # 2. End-to-end --connect CLI run using ClusterComplete (default custom_sim_params.xml)
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = subprocess.run(
                [
                    sys.executable,
                    "app.py",
                    "--cli",
                    "--connect",
                    "./data/sqd/AND",
                    "./data/sqd/AND",
                    "--connector",
                    "FIFO",
                    "--simulator",
                    "ClusterComplete",
                    "--output",
                    tmpdir,
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"ClusterComplete --connect failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
            )
            self.assertIn("clustercomplete", proc.stdout.lower())

            out_dir = Path(tmpdir) / "Circuit_AND0_AND0"
            self.assertTrue((out_dir / "Circuit_AND0_AND0.sqd").is_file())
            self.assertTrue((out_dir / "truth_table.png").is_file())
            self.assertTrue((out_dir / "simulation_config.txt").is_file())
            self.assertEqual(len(list(out_dir.glob("specific_gate_*.png"))), 8)

            config_text = (out_dir / "simulation_config.txt").read_text()
            self.assertIn("clustercomplete", config_text.lower())
            self.assertIn("autofail: 100", config_text)
            self.assertIn("eps_r: 4.1", config_text)
            self.assertIn("debye_length: 1.8", config_text)

        # 3. End-to-end --batch CLI run using ClusterComplete
        with tempfile.TemporaryDirectory() as tmpdir:
            proc_batch = subprocess.run(
                [
                    sys.executable,
                    "app.py",
                    "--cli",
                    "--batch",
                    "--gate_files",
                    "./data/sqd/AND",
                    "--numbergates",
                    "1",
                    "--simulator",
                    "ClusterComplete",
                    "--output",
                    tmpdir,
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                proc_batch.returncode,
                0,
                msg=f"ClusterComplete --batch failed:\nSTDOUT:\n{proc_batch.stdout}\nSTDERR:\n{proc_batch.stderr}",
            )
            self.assertTrue((Path(tmpdir) / "summary.csv").is_file())
            self.assertTrue((Path(tmpdir) / "saved_expressions.json").is_file())
            configs = list(Path(tmpdir).rglob("simulation_config.txt"))
            self.assertEqual(len(configs), 1)
            self.assertIn("clustercomplete", configs[0].read_text().lower())


class TestFixesAndRegressionGuards(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        os.chdir(PROJECT_ROOT)

    def tearDown(self):
        os.chdir(self._orig_cwd)

    def test_call_sim_timeout_and_nonzero_exit(self):
        with self.assertRaises(TimeoutError):
            implementation.call_sim(
                "unused_in", "unused_out", simulator="sleep 2 #", timeout=0.1
            )

        with self.assertRaises(RuntimeError):
            implementation.call_sim(
                "unused_in", "unused_out", simulator="false #", timeout=5
            )

    def test_pruned_valid_combo_generator_matches_cartesian_product_exactly(self):
        files = [
            pipeline.resolve_gate_path("./data/sqd/AND"),
            pipeline.resolve_gate_path("./data/sqd/OR"),
            pipeline.resolve_gate_path("./data/sqd/INPUT"),
        ]
        for max_gates in (1, 2, 3):
            expected = [
                combo
                for r in range(1, 2 * max_gates)
                for combo in itertools.product(files, repeat=r)
                if pipeline.is_valid_combo(combo, max_gates)
            ]
            actual = list(pipeline._iter_valid_combos(files, max_gates))
            self.assertEqual(actual, expected)

    def test_main_operator_cache_returns_isolated_copies(self):
        and_path = pipeline.resolve_gate_path("./data/sqd/AND")
        g1 = sqd_manipulator.main_operator(and_path)
        original_count = len(g1.db_dots)
        g1.db_dots.pop()
        g2 = sqd_manipulator.main_operator(and_path)
        self.assertEqual(len(g2.db_dots), original_count)

    def test_read_result_returns_lowest_energy_not_last(self):
        and_path = pipeline.resolve_gate_path("./data/sqd/AND")
        gate = sqd_manipulator.main_operator(and_path)
        out_x = gate.output_dot.physloc["x"]
        out_y = gate.output_dot.physloc["y"]

        xml_content = f"""<?xml version="1.0" encoding="utf-8"?>
<sim_out>
    <physloc>
        <dbdot x="{out_x}" y="{out_y}"/>
    </physloc>
    <elec_dist>
        <dist energy="-1.5" count="5" physically_valid="1" state_count="1">-</dist>
        <dist energy="-0.25" count="2" physically_valid="1" state_count="1">0</dist>
    </elec_dist>
</sim_out>
"""
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as tf:
            tf.write(xml_content)
            tmp_xml = tf.name

        try:
            symbols_out, energy = sqd_manipulator.read_result(tmp_xml, gate)
            self.assertEqual(symbols_out, ["1"])
            self.assertEqual(energy, -1.5)
        finally:
            os.remove(tmp_xml)

    def test_up_correction_clear_occupied_and_far_dot_no_regressions(self):
        g_and = pipeline.resolve_gate_path("./data/sqd/AND")
        conn = pipeline.connect_gates([g_and, g_and], wire_length=1, strategy="FIFO")
        gate = conn.gate
        meta = gate.gate_metadata[0]
        meta["stabilizers"] = []

        # Compute boundaries matching correction_function
        pts = []
        for pos in meta["inputs"]:
            pts.append(sqd_manipulator.Gate.from_dict(gate.to_dict()).pivot_dot.physloc)
        gate_boundaries_pts = [
            (pos[0] * 3.84, pos[1] * 7.68 + pos[2] * 2.25)
            for pos in meta["inputs"] + [meta["center"]]
        ]
        boundaries = corrector.calculate_boundaries(gate_boundaries_pts)

        # Case 1: Clear top area -> adds top stabilizer and returns gate
        initial_dots = len(gate.db_dots)
        updated_gate = corrector.up_correction(gate, meta, boundaries)
        self.assertIsNotNone(updated_gate)
        self.assertEqual(len(updated_gate.db_dots), initial_dots + 1)
        self.assertEqual(len(meta["stabilizers"]), 1)

        # Case 2: Occupied top area (the stabilizer we just added is now in the area!)
        # Must detect collision, skip adding another top dot, and STILL return gate (not None)
        dots_before_occupied = len(updated_gate.db_dots)
        updated_again = corrector.up_correction(updated_gate, meta, boundaries)
        self.assertIsNotNone(updated_again)
        self.assertEqual(len(updated_again.db_dots), dots_before_occupied)
        self.assertEqual(len(meta["stabilizers"]), 1)

        # Case 3: Distant dot far above the clearance zone should NOT block top stabilizer
        conn2 = pipeline.connect_gates([g_and], wire_length=1, strategy="FIFO")
        gate2 = conn2.gate
        meta2 = gate2.gate_metadata[0]
        meta2["stabilizers"] = []
        gate2.add_dot(NML=(meta2["center"][0], meta2["original_inputs"][0][1] - 10, 0))
        b2_pts = [
            (pos[0] * 3.84, pos[1] * 7.68 + pos[2] * 2.25)
            for pos in meta2["inputs"] + [meta2["center"]]
        ]
        b2 = corrector.calculate_boundaries(b2_pts)
        dots_before_far = len(gate2.db_dots)
        res2 = corrector.up_correction(gate2, meta2, b2)
        self.assertIsNotNone(res2)
        self.assertEqual(len(res2.db_dots), dots_before_far + 1)
        self.assertEqual(len(meta2["stabilizers"]), 1)

    def test_batch_failed_simulation_does_not_poison_saved_expressions(self):
        g_and = pipeline.resolve_gate_path("./data/sqd/AND")
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_failed = pipeline.SimulationResult(
                gate=sqd_manipulator.main_operator(g_and),
                table_data=[{"result": "", "expected": "", "file_id": "", "energy": ""}],
                specific_gate_data=[],
                has_mismatches=True,
                failed=True,
                simulator_path="data/simulators/simanneal/simanneal.physeng",
                simulator_config={},
            )
            with mock.patch.object(pipeline, "run_simulation", return_value=fake_failed):
                res = pipeline.run_batch_simulation(
                    gate_files=[g_and],
                    max_gates=1,
                    output_dir=tmpdir,
                    cooldown=False,
                )

            self.assertFalse(res["expressions_file"].exists())
            with open(res["summary_csv"], "r") as f:
                rows = list(csv.DictReader(f))
            error_rows = [r for r in rows if r["RowType"] == "ERROR"]
            self.assertEqual(len(error_rows), 1)

            # Resuming now must NOT skip the previously failed combo
            res_retry = pipeline.run_batch_simulation(
                gate_files=[g_and],
                max_gates=1,
                output_dir=tmpdir,
                max_corrections=0,
                cooldown=False,
            )
            self.assertEqual(res_retry["simulated_in_run"], 1)
            self.assertTrue(res_retry["expressions_file"].is_file())


class TestWebUIStartupIntegrity(unittest.TestCase):
    def test_webui_import_and_layout_intact(self):
        script = (
            "import app; "
            "assert app.app is not None; "
            "assert app.server is not None; "
            "assert app.app.title == 'SIQAD Interconector'; "
            "assert app.app.layout is not None; "
            "assert len(app.app.callback_map) > 0; "
            "print('WEBUI_OK', len(app.app.callback_map))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"Web-UI import check failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
        )
        self.assertIn("WEBUI_OK", proc.stdout)

    def test_cli_mode_does_not_import_dash(self):
        script = (
            "import sys; "
            "import source.cli; "
            "assert 'dash' not in sys.modules, 'dash should not be imported in CLI modules'; "
            "assert 'source.StreamCapture' not in sys.modules, 'StreamCapture should not be imported in CLI modules'; "
            "print('CLI_ISOLATED_OK')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("CLI_ISOLATED_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
