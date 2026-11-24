import argparse
import sys

from source import pipeline


VALID_CONNECTORS = ("FIFO", "CORNER")


def _parse_connector(value):
    normalized = value.strip().upper()
    if normalized not in VALID_CONNECTORS:
        raise argparse.ArgumentTypeError(
            f"Invalid connector '{value}'. Choose from: FIFO, Corner."
        )
    return normalized


def build_parser():
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="SIQAD Interconnection Toolkit (Web-UI by default, or headless via --cli).",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Enable headless CLI mode and bypass starting the Web-UI server.",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--connect",
        nargs="+",
        metavar="GATE_SQD",
        help=(
            "Paths to two or more input gate .sqd files or gate folders "
            "(e.g. ./data/sqd/AND/AND0.28.sqd or ./data/sqd/AND)."
        ),
    )
    mode_group.add_argument(
        "--batch",
        action="store_true",
        help="Explicitly enable full Auto Batch combination simulation mode.",
    )

    parser.add_argument(
        "--gate_files",
        nargs="+",
        metavar="GATE_SQD",
        default=None,
        help=(
            "Gate .sqd files or gate folders to include in --batch mode "
            "(defaults to all .sqd files dynamically discovered under ./data/sqd)."
        ),
    )
    parser.add_argument(
        "--numbergates",
        type=int,
        default=None,
        metavar="X",
        help=(
            "Maximum number of non-INPUT gates per combination in --batch mode "
            "(default: min(number of gate files, 3))."
        ),
    )
    parser.add_argument(
        "--connector",
        type=_parse_connector,
        default="FIFO",
        help="Connector strategy: FIFO or Corner (case-insensitive, default: FIFO).",
    )
    parser.add_argument(
        "--simulator",
        default=pipeline.DEFAULT_SIMULATOR_NAME,
        help="Simulator name or path resolved under ./data/simulators/ (default: simanneal).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Optional custom output directory (defaults to data/saved & data/saved_gates "
            "for --connect, or data/auto_batch_results for --batch)."
        ),
    )
    parser.add_argument(
        "--wires",
        type=int,
        default=1,
        help="Initial wire length between gates (default: 1).",
    )
    parser.add_argument(
        "--fixes",
        type=int,
        default=None,
        help=(
            "Maximum automatic layout corrections allowed "
            "(default: 0 for --connect, 5 for --batch)."
        ),
    )
    parser.add_argument(
        "--max-mismatches",
        type=int,
        default=1,
        help="Maximum truth-table mismatches before stopping/correcting (default: 1).",
    )
    parser.add_argument(
        "--sim-param",
        action="append",
        metavar="KEY=VALUE",
        default=None,
        help=(
            "Override a simulator parameter for this run without modifying "
            "custom_sim_params.xml (repeatable, e.g. --sim-param muzm=-0.28)."
        ),
    )
    return parser


def parse_args(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cli:
        if not args.batch and not args.connect:
            parser.error(
                "In --cli mode, specify either --connect <gate1> <gate2> [...] or --batch."
            )

        if args.connect is not None and len(args.connect) < 2:
            parser.error("--connect requires at least 2 gate paths in --cli mode.")

        if not args.batch and (args.gate_files is not None or args.numbergates is not None):
            parser.error("--gate_files and --numbergates require --batch to be explicitly specified.")

        if args.numbergates is not None and args.numbergates < 1:
            parser.error("--numbergates must be >= 1.")

        if args.wires < 1:
            parser.error("--wires must be >= 1.")

        if args.fixes is None:
            args.fixes = 5 if args.batch else 0
        elif args.fixes < 0:
            parser.error("--fixes must be >= 0.")

        if args.max_mismatches < 1:
            parser.error("--max-mismatches must be >= 1.")

        if args.sim_param:
            try:
                args.sim_param_overrides = pipeline.parse_sim_param_overrides(args.sim_param)
            except ValueError as exc:
                parser.error(str(exc))
        else:
            args.sim_param_overrides = {}
    else:
        args.sim_param_overrides = {}

    return args


def run_cli(argv=None):
    args = parse_args(argv)
    if not args.cli:
        build_parser().print_help()
        return 0

    try:
        if args.batch:
            result = pipeline.run_batch_simulation(
                gate_files=args.gate_files,
                max_gates=args.numbergates,
                connector=args.connector,
                simulator=args.simulator,
                config_sim=None,
                config_overrides=args.sim_param_overrides or None,
                output_dir=args.output,
                wire_length=args.wires,
                max_corrections=args.fixes,
                max_mismatches=args.max_mismatches,
                cooldown=True,
            )
            print("CLI batch pipeline completed.")
            print(f"Summary CSV: {result['summary_csv']}")
            print(f"Output directory: {result['output_root']}")
            return 0

        result = pipeline.run_pipeline(
            gate_inputs=args.connect,
            connector=args.connector,
            simulator=args.simulator,
            output_dir=args.output,
            wire_length=args.wires,
            max_corrections=args.fixes,
            max_mismatches=args.max_mismatches,
            config_overrides=args.sim_param_overrides or None,
        )
    except (FileNotFoundError, ValueError, NotImplementedError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("CLI pipeline completed.")
    print(f"Gate SQD: {result['saved_gate_sqd']}")
    print(f"Output directory: {result['saved_artifacts']['save_folder']}")
    return 0
