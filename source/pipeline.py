import ast
import csv
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from pathlib import Path

from sympy import symbols, sympify
from sympy.logic.boolalg import truth_table

from source import corrector, exporter, file_manager, gate_connector, implementation, sqd_manipulator


DATA_TEMP = Path("data") / "temp"
DATA_XML = Path("data") / "xml"
DATA_SIMULATORS = Path("data") / "simulators"
DATA_SQD = Path("data") / "sqd"
DEFAULT_SIMULATOR_NAME = "simanneal"
DEFAULT_BATCH_MAX_GATES_CAP = 3


@dataclass
class ConnectionResult:
    gate: object
    gate_pos_name: list
    wire_length: int
    overlaps: bool


@dataclass
class SimulationResult:
    gate: object
    table_data: list
    specific_gate_data: list
    has_mismatches: bool
    failed: bool
    simulator_path: str
    simulator_config: dict


def discover_gate_files(sqd_dir=None):
    """Dynamically discover all `.sqd` gate files under `data/sqd`."""
    target_dir = DATA_SQD if sqd_dir is None else Path(sqd_dir)
    return file_manager.get_files(str(target_dir))


def is_valid_combo(combo, max_size=999999999):
    """Validate whether a sequence of gate file paths forms a valid tree combo."""
    if not combo:
        return False

    input_name = "INPUT.sqd"
    names = [os.path.basename(str(g)) for g in combo]

    # 1) Cannot start with INPUT
    if input_name in names[0]:
        return False

    # 2) Track available parent slots
    available_slots = 1
    for name in names:
        if available_slots <= 0:
            return False

        available_slots -= 1
        if name != input_name:
            available_slots += 2

    # 3) Enforce max non-input gate count
    non_input_count = sum(1 for name in names if name != input_name)
    if non_input_count > max_size:
        return False
    return True


def resolve_gate_path(gate_arg):
    """Resolve a direct `.sqd` file path or gate folder path to an `.sqd` file.

    For folder paths (e.g. `./data/sqd/AND`), checks `{folder_name}.sqd` first,
    then falls back to the `.sqd` file inside that folder (e.g. `AND0.28.sqd`).
    Also falls back to looking under `./data/sqd/<gate_arg>` if not found directly.
    """
    raw_path = Path(gate_arg)

    candidates = [raw_path]
    if not raw_path.exists() and not raw_path.is_absolute():
        candidates.append(DATA_SQD / gate_arg)

    for candidate in candidates:
        if candidate.is_file():
            if candidate.suffix.lower() != ".sqd":
                raise ValueError(f"Expected a .sqd file, got: {candidate}")
            return str(candidate)

        if candidate.is_dir():
            exact = candidate / f"{candidate.name}.sqd"
            if exact.is_file():
                return str(exact)

            sqd_files = sorted(
                p for p in candidate.iterdir() if p.is_file() and p.suffix.lower() == ".sqd"
            )
            if sqd_files:
                for p in sqd_files:
                    if p.stem.lower() == candidate.name.lower():
                        return str(p)
                return str(sqd_files[0])

            raise FileNotFoundError(f"No .sqd files found inside gate folder: {candidate}")

    raise FileNotFoundError(f"Gate file or folder not found: {gate_arg}")


def _physeng_to_executable(physeng_path):
    sim_str = str(physeng_path)
    if os.name == "posix":
        return sim_str.replace(".physeng", "")
    if os.name == "nt":
        return sim_str.replace(".physeng", ".exe")
    return sim_str.replace(".physeng", "")


def resolve_simulator(simulator_arg=None):
    """Dynamically resolve a simulator under `data/simulators/`.

    Accepts a simulator name (e.g. `simanneal`), a simulator directory
    (e.g. `./data/simulators/simanneal`), a `.physeng` path, or a binary path.

    Returns:
        tuple[str, str]: `(executable_path, physeng_path)`
    """
    target = simulator_arg if simulator_arg else DEFAULT_SIMULATOR_NAME
    target_path = Path(target)
    available_physengs = file_manager.get_simulators()

    # Case 1: direct .physeng file path
    if target_path.is_file() and target_path.suffix == ".physeng":
        exec_path = _physeng_to_executable(target_path)
        return exec_path, str(target_path)

    # Case 2: direct simulator folder path
    if target_path.is_dir():
        physengs = sorted(target_path.glob("*.physeng"))
        if physengs:
            physeng = physengs[0]
            return _physeng_to_executable(physeng), str(physeng)
        raise FileNotFoundError(f"No .physeng file found in simulator directory: {target_path}")

    # Case 3: direct executable file path inside a simulator folder
    if target_path.is_file():
        sibling_physeng = target_path.with_suffix(".physeng")
        physeng_str = str(sibling_physeng) if sibling_physeng.is_file() else str(target_path)
        return str(target_path), physeng_str

    # Case 4: match dynamically discovered simulators by name (case-insensitive)
    target_lower = str(target).strip().lower()
    if target_lower.endswith(".physeng"):
        target_lower = target_lower[: -len(".physeng")]

    for physeng in available_physengs:
        p = Path(physeng)
        if p.stem.lower() == target_lower or p.parent.name.lower() == target_lower:
            return _physeng_to_executable(p), str(p)

    # Fallback substring match (preserves existing Web-UI matching semantics)
    for physeng in available_physengs:
        if target_lower in physeng.lower():
            return _physeng_to_executable(physeng), str(physeng)

    available_names = sorted(Path(p).stem for p in available_physengs)
    raise FileNotFoundError(
        f"Simulator '{target}' not found under {DATA_SIMULATORS}. "
        f"Available simulators: {available_names}"
    )


def load_simulator_config(simulator_path):
    """Load `custom_sim_params.xml` from the simulator's directory if present."""
    if not simulator_path:
        return {}

    sim_path = Path(simulator_path)
    sim_folder = sim_path if sim_path.is_dir() else sim_path.parent
    config_file_path = sim_folder / "custom_sim_params.xml"

    if not config_file_path.is_file():
        return {}

    try:
        content = config_file_path.read_text()
        parsed = ast.literal_eval(content)
        return parsed if isinstance(parsed, dict) else {}
    except Exception as e:
        print(f"Error reading custom_sim_params.xml: {e}")
        return {}


def save_simulator_config(simulator_path, config):
    """Persist custom simulator parameters dict to `custom_sim_params.xml`."""
    if not simulator_path:
        raise ValueError("Cannot save simulator configuration without a simulator path.")

    sim_path = Path(simulator_path)
    sim_folder = sim_path if sim_path.is_dir() else sim_path.parent
    sim_folder.mkdir(parents=True, exist_ok=True)
    config_file_path = sim_folder / "custom_sim_params.xml"
    config_file_path.write_text(str(dict(config)))
    return config_file_path


def get_simulator_params(simulator_path):
    """Parse `.physeng` parameter metadata and merge any saved `custom_sim_params.xml`."""
    if not simulator_path:
        return {}

    params = file_manager.parse_physeng(simulator_path)
    if not params:
        return {}

    custom_params = load_simulator_config(simulator_path)
    for key, value in custom_params.items():
        if key in params:
            params[key]["val"] = value
    return params


def parse_sim_param_overrides(raw_overrides):
    """Parse `KEY=VALUE` simulator parameter override strings into a dict."""
    overrides = {}
    if not raw_overrides:
        return overrides

    for item in raw_overrides:
        if "=" not in item:
            raise ValueError(
                f"Invalid simulator parameter '{item}'. Expected format: KEY=VALUE."
            )
        key, raw_val = item.split("=", 1)
        key = key.strip()
        raw_val = raw_val.strip()
        if not key:
            raise ValueError(f"Empty parameter key in '{item}'.")

        try:
            parsed_val = ast.literal_eval(raw_val)
            if isinstance(parsed_val, (int, float, str, bool)):
                val = parsed_val
            else:
                val = raw_val
        except (ValueError, SyntaxError):
            val = raw_val

        overrides[key] = val

    return overrides


def connect_gates(gate_files, wire_length=1, strategy="FIFO"):
    """Validate and interconnect N gate `.sqd` files using `gate_connector`.

    Unpacks the 4-tuple from `gate_connector.connect_n_gates`, runs the overlap
    wire-length retry loop, converts the circuit to a `Gate`, and attaches
    `gate.gate_metadata`.
    """
    if not gate_files:
        raise ValueError("No gate files provided.")

    if not is_valid_combo(gate_files, 999999999):
        raise ValueError("Invalid gate combination selected.")

    strategy_norm = (strategy or "FIFO").strip().upper()
    current_wire_length = wire_length
    original_wire_length = wire_length
    overlap_try = 0

    while True:
        try:
            circuit, gate_pos_name, metadata, overlaps = gate_connector.connect_n_gates(
                gate_files, wires=current_wire_length, strategy=strategy_norm
            )
        except ValueError as exc:
            if strategy_norm == "CORNER":
                raise NotImplementedError(
                    "Connector strategy 'CORNER' is not implemented in gate_connector.connect_n_gates yet. "
                    "Supported strategy: FIFO."
                ) from exc
            raise

        overlap_try += 1
        if overlap_try == len(gate_files) * 3:
            print(
                "⚠️ Maximum attempts to stop overlapping done... Resetting wire lenght. "
                "Current Gate might not be supported on this architecture."
            )
            current_wire_length = original_wire_length
        if not overlaps or overlap_try > len(gate_files) * 3:
            break
        current_wire_length += 1

    gate = sqd_manipulator.circuit_to_gate(circuit)
    gate.gate_metadata = metadata

    return ConnectionResult(
        gate=gate,
        gate_pos_name=gate_pos_name,
        wire_length=current_wire_length,
        overlaps=overlaps,
    )


def _pad_table_data(data, page_size=20):
    rows_to_add = page_size - len(data) % page_size
    if rows_to_add > 0:
        blank_row = (
            {col: "" for col in data[0].keys()}
            if data
            else {"result": "", "expected": "", "file_id": "", "energy": ""}
        )
        data = data + [blank_row.copy() for _ in range(rows_to_add)]
    return data


def _simulate_internal(
    gate,
    sim_exec,
    config_sim,
    expected,
    max_mismatches=1,
    verbose=True,
    sim_timeout=None,
):
    corrections_needed = False
    results = []
    specific_gate_data = []
    gates = sqd_manipulator.combinators(gate)
    if verbose:
        print(f"Total simulations to run: {len(gates)}")
    percentage_print = max(1, len(gates) // 8)

    print("Progress: [", end="", flush=True)
    sim_template = file_manager.gen_simulator_sim_template(simulator_path=sim_exec)
    if sim_template is None:
        sim_template = file_manager.get_simulator_sim_template(sim_exec)
    if sim_template is None:
        print("No sim template found, Aborting.")
        return [], [], True, True

    max_retries = 3
    retry_sleep = 5

    mismatches_found = 0
    for i in range(len(gates)):
        attempts = 0
        if mismatches_found >= max_mismatches:
            break
        while attempts < max_retries:
            try:
                file_name, template = file_manager.sqd_template_create(
                    gates[i],
                    prefix=f"combination_{i}_",
                    mode="simulation",
                    sim_params_template=sim_template,
                    parameters=config_sim,
                )
                file_path = str(DATA_TEMP / file_name)
                file_manager.make_file(file_path, template)
                result_name = "result_" + file_name
                result_path = implementation.call_sim(
                    file_path,
                    result_name,
                    simulator=sim_exec,
                    simmaneal_default_path=sim_exec,
                    timeout=sim_timeout,
                )
                symbol_table, energy = sqd_manipulator.read_result(result_path, gate)
                specific_gate_data.append(sqd_manipulator.read_result_plusXY(result_path, gate))
                results.append([symbol_table, i, energy])
                raw_symbol = str(symbol_table[0]) if symbol_table else ""
                symbol_value = int(raw_symbol) if raw_symbol in ("0", "1") else None
                expected_value = expected[i]
                if symbol_value != expected_value:
                    mismatches_found += 1
                break
            except Exception as e:
                attempts += 1
                print(f"\n ⚠️ Error reading result for simulation {i}: {e}")
                if attempts < max_retries:
                    print(f"Retrying ({attempts}/{max_retries}) after {retry_sleep} seconds...")
                    time.sleep(retry_sleep)
                else:
                    print(f"Max retries reached for simulation {i}. Skipping this simulation.")
                    results.append(["Error", i, "Error"])
                    return results, specific_gate_data, True, True
        if i % percentage_print == 0:
            print("#", end="", flush=True)

    print("]")
    if mismatches_found >= max_mismatches:
        print(f"⚠️ Maximum mismatches reached ({max_mismatches}). Stopping further simulations.")
        corrections_needed = True

    return results, specific_gate_data, corrections_needed, False


def run_simulation(
    gate,
    simulator=None,
    config_sim=None,
    config_overrides=None,
    max_corrections=0,
    max_mismatches=1,
    pad_page_size=20,
    verbose=True,
    sim_timeout=None,
):
    """Run truth-table simulation and optional stabilization corrections on a Gate."""
    if not gate:
        blank_data = _pad_table_data([], page_size=pad_page_size) if pad_page_size else []
        return SimulationResult(
            gate=None,
            table_data=blank_data,
            specific_gate_data=[],
            has_mismatches=False,
            failed=False,
            simulator_path="",
            simulator_config={},
        )

    file_manager.clear_folder(str(DATA_TEMP))
    file_manager.clear_folder(str(DATA_XML))

    if isinstance(gate, dict):
        gate = sqd_manipulator.Gate.from_dict(gate)

    expected = [
        int(bool(output)) for _, output in truth_table(gate.expression, gate.input_symbols)
    ]

    sim_exec, sim_physeng = resolve_simulator(simulator)
    effective_config = (
        load_simulator_config(sim_physeng) if config_sim is None else dict(config_sim)
    )
    if config_overrides:
        effective_config.update(config_overrides)

    applied_corrections = 0
    max_possible_corrections = len(getattr(gate, "gate_metadata", []) or [])
    effective_max_corrections = min(max_corrections, max_possible_corrections)

    print(f"Simulator: {sim_exec}, gate: {gate.name}")
    results = []
    specific_gate_data = []
    failed = False

    while True:
        results, specific_gate_data, corrections_needed, failed = _simulate_internal(
            gate,
            sim_exec,
            effective_config,
            expected,
            max_mismatches=max_mismatches,
            verbose=verbose,
            sim_timeout=sim_timeout,
        )
        if failed:
            break
        if corrections_needed and applied_corrections < effective_max_corrections:
            print("⚠️ Corrections needed, applying corrections...")
            gate, max_stabilized = corrector.main_correction(
                gate, gate.gate_metadata, specific_gate_data
            )
            applied_corrections += 1
            if max_stabilized:
                print("⚠️ Maximum current stabilization achieved, stopping further corrections.")
                break
        elif corrections_needed and applied_corrections >= effective_max_corrections:
            print("⚠️ Maximum corrections applied, stopping further corrections.")
            break
        else:
            break

    def _clean(val):
        if isinstance(val, (int, float, str, bool)) or val is None:
            return val
        return str(val)

    if failed:
        data = _pad_table_data([], page_size=pad_page_size) if pad_page_size else []
        return SimulationResult(
            gate=gate,
            table_data=data,
            specific_gate_data=[],
            has_mismatches=True,
            failed=True,
            simulator_path=sim_physeng,
            simulator_config=effective_config,
        )

    data = [
        {
            "result": _clean(row[0]),
            "expected": "N/A" if row[1] >= len(expected) else _clean(expected[row[1]]),
            "file_id": _clean(row[1]),
            "energy": _clean(row[2]),
        }
        for row in results
    ]

    has_mismatches = False
    for item in data:
        result_val = str(item["result"])
        if result_val.startswith("[") and result_val.endswith("]"):
            result_val = result_val.strip("[]").strip().strip("'").strip('"')
        expected_val = str(item["expected"])

        if expected_val != "N/A" and result_val != expected_val:
            item["expected"] = f"{expected_val} 🔴"
            has_mismatches = True

    if pad_page_size:
        data = _pad_table_data(data, page_size=pad_page_size)

    return SimulationResult(
        gate=gate,
        table_data=data,
        specific_gate_data=specific_gate_data,
        has_mismatches=has_mismatches,
        failed=False,
        simulator_path=sim_physeng,
        simulator_config=effective_config,
    )


def run_pipeline(
    gate_inputs,
    connector="FIFO",
    simulator=DEFAULT_SIMULATOR_NAME,
    output_dir=None,
    wire_length=1,
    max_corrections=0,
    max_mismatches=1,
    config_overrides=None,
    sim_timeout=None,
):
    """Execute the full headless pipeline: resolve -> connect -> simulate -> save."""
    resolved_gates = [resolve_gate_path(g) for g in gate_inputs]
    gate_names = [os.path.basename(g) for g in resolved_gates]

    connection = connect_gates(
        resolved_gates, wire_length=wire_length, strategy=connector
    )
    print(f"Selected gates: {gate_names}, Wire length: {connection.wire_length}")
    print(f"Gate expression: {connection.gate.expression}")

    sim_result = run_simulation(
        connection.gate,
        simulator=simulator,
        config_sim=None,
        config_overrides=config_overrides,
        max_corrections=max_corrections,
        max_mismatches=max_mismatches,
        pad_page_size=20,
        verbose=True,
        sim_timeout=sim_timeout,
    )

    if sim_result.failed:
        raise RuntimeError(
            f"Simulation failed for gate '{connection.gate.name}' using simulator '{simulator}'."
        )

    saved_gate_path = exporter.save_gate_sqd(sim_result.gate, output_dir=output_dir)
    saved_artifacts = exporter.save_simulation_results(
        sim_result.gate,
        sim_result.table_data,
        sim_result.specific_gate_data,
        simulator_name=sim_result.simulator_path,
        config_sim=sim_result.simulator_config,
        output_dir=output_dir,
    )

    return {
        "resolved_gates": resolved_gates,
        "connection": connection,
        "simulation": sim_result,
        "saved_gate_sqd": saved_gate_path,
        "saved_artifacts": saved_artifacts,
    }


def canonicalize_expression(expr):
    """Rename free SymPy symbols deterministically to x0, x1, ... for deduplication."""
    mapping = {}
    ordered_symbols = sorted(expr.free_symbols, key=lambda s: str(s))
    for counter, sym in enumerate(ordered_symbols):
        mapping[sym] = symbols(f"x{counter}")
    return expr.xreplace(mapping)


def _iter_valid_combos(gate_files, max_gates):
    """Yield all valid gate combinations up to `max_gates` non-INPUT gates in length order.

    Prunes invalid prefixes (INPUT root, slot exhaustion, > max_gates) during generation
    instead of enumerating the full cartesian product.
    """
    input_name = "INPUT.sqd"
    max_len = 2 * max_gates
    indexed_files = [(g, os.path.basename(str(g)) == input_name) for g in gate_files]

    def _recurse(target_len, prefix, available_slots, non_input_count):
        if len(prefix) == target_len:
            yield tuple(prefix)
            return

        if available_slots <= 0:
            return

        is_root = len(prefix) == 0
        for gate_path, is_input in indexed_files:
            if is_root and is_input:
                continue
            next_non_input = non_input_count if is_input else non_input_count + 1
            if next_non_input > max_gates:
                continue
            next_slots = available_slots - 1 + (0 if is_input else 2)
            prefix.append(gate_path)
            yield from _recurse(target_len, prefix, next_slots, next_non_input)
            prefix.pop()

    for length in range(1, max_len):
        yield from _recurse(length, [], 1, 0)


def generate_unique_batch_combos(gate_files, max_gates, wire_length=1, strategy="FIFO"):
    """Generate valid, logically unique gate combinations up to `max_gates` non-INPUT gates."""
    strategy_norm = (strategy or "FIFO").strip().upper()
    seen = set()
    unique_combos = []
    generator_unique_expressions = {}

    for combo in _iter_valid_combos(gate_files, max_gates):
        norm = tuple(os.path.basename(str(g)) for g in combo)
        if norm in seen:
            continue

        try:
            circuit, _, _, _ = gate_connector.connect_n_gates(
                combo, wires=wire_length, strategy=strategy_norm
            )
        except ValueError as exc:
            if strategy_norm == "CORNER":
                raise NotImplementedError(
                    "Connector strategy 'CORNER' is not implemented in gate_connector.connect_n_gates yet. "
                    "Supported strategy: FIFO."
                ) from exc
            raise

        gate = sqd_manipulator.circuit_to_gate(circuit)
        expr_key = canonicalize_expression(gate.expression)
        if expr_key in generator_unique_expressions:
            continue

        generator_unique_expressions[expr_key] = True
        seen.add(norm)
        unique_combos.append(combo)

    return unique_combos


def run_batch_simulation(
    gate_files=None,
    max_gates=None,
    connector="FIFO",
    simulator=DEFAULT_SIMULATOR_NAME,
    config_sim=None,
    config_overrides=None,
    output_dir=None,
    wire_length=1,
    max_corrections=5,
    max_mismatches=1,
    cooldown=True,
    sim_timeout=None,
):
    """Run full Auto Batch simulation with expression deduplication, CSV logging, and resume.

    If `gate_files` is omitted, dynamically discovers all `.sqd` files under `data/sqd`.
    If `max_gates` is omitted, defaults to `min(len(resolved_gate_files), 3)`.
    """
    if gate_files is None:
        resolved_files = discover_gate_files()
    else:
        resolved_files = [resolve_gate_path(g) for g in gate_files]

    if not resolved_files:
        raise ValueError("No gate files available to process for auto batch.")

    if max_gates is None:
        effective_max_gates = max(1, min(len(resolved_files), DEFAULT_BATCH_MAX_GATES_CAP))
    else:
        if max_gates < 1:
            raise ValueError("max_gates must be >= 1.")
        effective_max_gates = max_gates

    _, sim_physeng = resolve_simulator(simulator)
    effective_config = (
        load_simulator_config(sim_physeng) if config_sim is None else dict(config_sim)
    )
    if config_overrides:
        effective_config.update(config_overrides)

    print("Starting auto batch simulation for all gates...")

    output_root = Path(output_dir) if output_dir is not None else exporter.DEFAULT_AUTO_BATCH_DIR
    output_root.mkdir(parents=True, exist_ok=True)
    expressions_file = output_root / "saved_expressions.json"
    summary_csv_path = output_root / "summary.csv"

    gate_unique_expressions = {}
    if expressions_file.exists():
        with open(expressions_file, "r") as f:
            saved = json.load(f)
            gate_unique_expressions = {sympify(k): True for k in saved.keys()}
        print(f"✅ Reloaded {len(gate_unique_expressions)} saved unique expressions")
    else:
        print("No saved expressions found. Starting fresh.")

    unique_combos = generate_unique_batch_combos(
        resolved_files,
        max_gates=effective_max_gates,
        wire_length=wire_length,
        strategy=connector,
    )

    group_sizes = []
    for combo in unique_combos:
        num_gates = sum(1 for g in combo if os.path.basename(g) != "INPUT.sqd")
        while len(group_sizes) < num_gates:
            group_sizes.append(0)
        group_sizes[num_gates - 1] += 1

    for size, count in enumerate(group_sizes):
        print(f"Total {size + 1}-gate combinations: {count}")

    print(f"Approximate Total combinations to simulate: {len(unique_combos)}")
    print(
        "Note: The actual number may be lower due to invalid combinations or logical duplicates being skipped."
    )

    file_exists = summary_csv_path.exists()
    total_combinations = 0
    total_mismatches = 0
    completed_combos = set()
    simulated_in_run = 0
    skipped_completed = 0

    with open(summary_csv_path, mode="a", newline="") as csv_file:
        csv_writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "RowType",
                "Combination",
                "Expression",
                "Num_Gates",
                "Mismatch",
                "Timestamp",
                "Runtime_s",
            ],
        )
        if not file_exists:
            csv_writer.writeheader()

        if file_exists:
            with open(summary_csv_path, mode="r") as read_file:
                csv_reader = csv.DictReader(read_file)
                for row in csv_reader:
                    if row.get("RowType") != "DATA":
                        continue
                    combo_name = row.get("Combination", "").strip()
                    if not combo_name:
                        continue
                    completed_combos.add(combo_name)
                    total_combinations += 1
                    if row.get("Mismatch") == "TRUE":
                        total_mismatches += 1

            print(
                f"❗ Found {len(completed_combos)} completed combinations. Resuming from last progress... ❗"
            )
            print(
                f"Total combinations so far: {total_combinations}, with mismatches: {total_mismatches}"
            )
            mismatch_percentage = (
                (total_mismatches / total_combinations * 100) if total_combinations > 0 else 0
            )
            print(f"Mismatch percentage so far: {mismatch_percentage:.2f}%")
        else:
            print("Starting fresh auto batch simulation.")

        last_num_gates = 1
        for combo in unique_combos:
            if len(combo) != last_num_gates:
                csv_file.flush()
                last_num_gates = len(combo)

            selected_gates = combo
            combo_name = "_".join(os.path.basename(g) for g in selected_gates)
            if combo_name in completed_combos:
                print(f"⚠️ Skipping already completed combination: {combo_name}")
                skipped_completed += 1
                continue

            starting_time = time.time()

            connection = connect_gates(
                selected_gates, wire_length=wire_length, strategy=connector
            )
            gate = connection.gate

            if gate.expression in gate_unique_expressions:
                print(
                    f"⚠️ Duplicate expression found, skipping simulation for "
                    f"{[os.path.basename(g) for g in selected_gates]}. Expression: {gate.expression}"
                )
                continue

            sim_result = run_simulation(
                gate,
                simulator=sim_physeng,
                config_sim=effective_config,
                max_corrections=max_corrections,
                max_mismatches=max_mismatches,
                pad_page_size=20,
                verbose=False,
                sim_timeout=sim_timeout,
            )

            total_combinations += 1
            simulated_in_run += 1
            if sim_result.has_mismatches:
                total_mismatches += 1

            runtime_s = time.time() - starting_time
            num_real_gates = sum(
                1 for g in selected_gates if os.path.basename(g) != "INPUT.sqd"
            )
            csv_row = {
                "RowType": "DATA" if not sim_result.failed else "ERROR",
                "Combination": combo_name,
                "Expression": str(gate.expression) if not sim_result.failed else "N/A",
                "Num_Gates": num_real_gates,
                "Mismatch": "TRUE" if sim_result.has_mismatches else "FALSE",
                "Timestamp": datetime.now().isoformat(),
                "Runtime_s": f"{runtime_s:.2f}",
            }

            csv_writer.writerow(csv_row)
            csv_file.flush()

            if not sim_result.failed:
                gate_unique_expressions[gate.expression] = True
                with open(expressions_file, "w") as f:
                    json.dump({str(k): True for k in gate_unique_expressions.keys()}, f)

            gate_name = gate.name if gate.name else f"unnamed_gate{str(combo)}"
            num_gates_dir = (
                f"{len(selected_gates)}_with_mismatches"
                if sim_result.has_mismatches
                else str(len(selected_gates))
            )
            save_folder = output_root / num_gates_dir / gate_name
            exporter.save_simulation_results(
                sim_result.gate,
                sim_result.table_data,
                sim_result.specific_gate_data,
                simulator_name=sim_result.simulator_path or "default_simanneal",
                config_sim=sim_result.simulator_config,
                save_folder_override=save_folder,
                truth_table_filename=f"{gate_name}_truth_table_truth_table.png",
                save_gate_plots=sim_result.has_mismatches,
                verbose=False,
            )

            if sim_result.has_mismatches:
                print(
                    f"✅ Completed simulation and saving for combination: "
                    f"{[os.path.basename(g) for g in selected_gates]} ⚠️ mismatch found"
                )
            else:
                print(
                    f"✅ Completed simulation and saving for combination: "
                    f"{[os.path.basename(g) for g in selected_gates]}"
                )

            if cooldown:
                time_to_sleep = min(2.0, max(0.2, len(selected_gates) - 1))
                time.sleep(time_to_sleep)
            final_runtime = time.time() - starting_time
            print(f"⏲ Runtime: {final_runtime:<.2f}s")

        mismatch_percentage = (
            (total_mismatches / total_combinations * 100) if total_combinations > 0 else 0
        )
        csv_writer.writerow(
            {
                "RowType": "SUMMARY",
                "Combination": "TOTAL_COMBINATIONS",
                "Num_Gates": total_combinations,
                "Mismatch": False,
            }
        )
        csv_writer.writerow(
            {
                "RowType": "SUMMARY",
                "Combination": "TOTAL_MISMATCHES",
                "Num_Gates": total_mismatches,
                "Mismatch": False,
            }
        )
        csv_writer.writerow(
            {
                "RowType": "SUMMARY",
                "Combination": "MISMATCH_PERCENTAGE",
                "Num_Gates": f"{mismatch_percentage:.2f}%",
                "Mismatch": False,
            }
        )

    print("✅ Auto batch simulation completed for all gates.")
    return {
        "output_root": output_root,
        "summary_csv": summary_csv_path,
        "expressions_file": expressions_file,
        "resolved_gates": resolved_files,
        "max_gates": effective_max_gates,
        "total_combinations": total_combinations,
        "total_mismatches": total_mismatches,
        "simulated_in_run": simulated_in_run,
        "skipped_completed": skipped_completed,
    }
