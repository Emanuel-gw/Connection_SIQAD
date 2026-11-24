from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
from matplotlib import pyplot as plt

from source import file_manager, sqd_manipulator


DEFAULT_SAVED_DIR = Path("data") / "saved"
DEFAULT_SAVED_GATES_DIR = Path("data") / "saved_gates"
DEFAULT_AUTO_BATCH_DIR = Path("data") / "auto_batch_results"


def _coerce_gate(gate):
    if isinstance(gate, dict):
        return sqd_manipulator.Gate.from_dict(gate)
    return gate


def save_simulation_results(
    gate,
    table_data,
    specific_gate_data,
    simulator_name=None,
    config_sim=None,
    output_dir=None,
    save_folder_override=None,
    truth_table_filename="truth_table.png",
    save_gate_plots=True,
    verbose=True,
):
    """Render truth table PNG, per-state gate XY PNGs, and simulation_config.txt.

    Preserves the existing matplotlib Agg rendering and default save path
    (`data/saved/<gate_name>/`), or writes into `<output_dir>/<gate_name>/`
    when `output_dir` is provided, or `save_folder_override` directly.
    """
    gate = _coerce_gate(gate)
    gate_name = gate.name if gate and gate.name else "unnamed_gate"

    if save_folder_override is not None:
        save_folder = Path(save_folder_override)
    else:
        base_dir = Path(output_dir) if output_dir is not None else DEFAULT_SAVED_DIR
        save_folder = base_dir / gate_name
    save_folder.mkdir(parents=True, exist_ok=True)

    # Save truth table as PNG
    circuit_name = gate_name + "_truth_table"
    expression_name = gate.expression if gate else "N/A"
    sim_label = simulator_name if simulator_name else "default_simanneal"

    df = pd.DataFrame(table_data)
    if df.empty:
        df = pd.DataFrame([{"result": "", "expected": "", "file_id": "", "energy": ""}])

    fig_width = max(4.0, len(df.columns) * 1.5)
    fig_height = max(2.5, len(df) / 2)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis("off")
    ax.axis("tight")

    plt.title(f"Circuit: {circuit_name}\nExpression: {expression_name}", fontsize=14, pad=20)
    table = ax.table(
        cellText=df.values,
        colLabels=df.columns,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.2)

    truth_table_path = save_folder / truth_table_filename
    plt.savefig(truth_table_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    if verbose:
        print(f"✅ Truth table saved: {truth_table_path}")

    # Save specific gate data plots as PNG
    gate_png_paths = []
    if save_gate_plots and specific_gate_data:
        for i, gate_info in enumerate(specific_gate_data):
            x_coords = [dot[0] for dot in gate_info]
            y_coords = [dot[1] for dot in gate_info]
            colors = ["black" if dot[2] == "0" else "blue" for dot in gate_info]

            plt.figure(figsize=(6, 6))
            for x, y, c in zip(x_coords, y_coords, colors):
                plt.scatter(x, y, color=c, s=60, edgecolors="none")
            plt.gca().invert_yaxis()
            plt.axis("off")
            plt.tight_layout()

            gate_png_path = save_folder / f"specific_gate_{i}.png"
            plt.savefig(gate_png_path, dpi=300, bbox_inches="tight")
            plt.close()
            gate_png_paths.append(gate_png_path)

        if verbose:
            print(f"✅ Gate data plots saved under {save_folder}")

    # Save the configuration used for the simulation
    config_file_path = save_folder / "simulation_config.txt"
    with open(config_file_path, "w") as f:
        f.write(f"Simulator: {sim_label}\n")
        f.write("Configuration Parameters:\n")
        for key, value in (config_sim or {}).items():
            f.write(f"{key}: {value}\n")
    if verbose:
        print(f"✅ Simulation configuration saved: {config_file_path}")

    return {
        "save_folder": save_folder,
        "truth_table_png": truth_table_path,
        "gate_pngs": gate_png_paths,
        "config_txt": config_file_path,
    }


def save_gate_sqd(gate, output_dir=None):
    """Save a Gate object to a `.sqd` file using the existing template generator.

    Defaults to `data/saved_gates/<gate_name>.sqd`, or writes into
    `<output_dir>/<gate_name>/<gate_name>.sqd` when `output_dir` is provided.
    """
    gate = _coerce_gate(gate)
    gate_name = gate.name if gate and gate.name else "unnamed_gate"

    if output_dir is not None:
        save_folder = Path(output_dir) / gate_name
    else:
        save_folder = DEFAULT_SAVED_GATES_DIR

    save_folder.mkdir(parents=True, exist_ok=True)
    gate_file_path = save_folder / f"{gate_name}.sqd"

    _, template = file_manager.sqd_template_create(gate, prefix=gate_name + "_", mode="save")
    with open(gate_file_path, "w") as f:
        f.write(template)

    print(f"✅ Gate saved: {gate_file_path}")
    return gate_file_path
