import os
import time
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, clientside_callback, dash_table, dcc, html

from pages import configpage
from source import StreamCapture, exporter, pipeline
from source.pipeline import is_valid_combo
from source.plotting import get_viewport, plot_NML, plot_XY, viewport_size

data_temp = Path("data") / "temp"
data_xml = Path("data") / "xml"
data_simulators = Path("data") / "simulators"
data_sqd = Path("data") / "sqd"
data_results = Path("data") / "results"
simmanneal_default_path = data_simulators / "simanneal"


layout = html.Div([
    dcc.Interval(id="log-updater", interval=500, n_intervals=0),  # every 0.5s
    html.Div([
        dcc.Dropdown(
            id='gate-dropdown',
            multi=False, placeholder='Select a gate',
            style={'width': '100%', 'marginRight': '10px', 'marginBottom': '10px'}),
        dcc.Input(
            id='wire-length-input',
            type='number',
            placeholder='Wires',
            min=1,
            step=1,
            style={'width': '10%', 'marginRight': '10px', 'marginBottom': '10px', 'height': '35px'}
        ),
        dcc.Input(
            id='fixes-allowed',
            type='number',
            placeholder='Fixes',
            min=0,
            step=1,
            style={'width': '10%', 'marginRight': '10px', 'marginBottom': '10px', 'height': '35px'}
        ),
        html.Div([
            html.Button("⚙", 
            id="toggle-config", 
            n_clicks=0, 
            style={'width': '100%', 'gap': '10px', 'height': '35px'}
        ),
        ], style={'flex': '0.1', 'marginRight': '10px'}),
        dbc.Button("Connect", id="btn-connect", color="secondary", disabled=False, style={'width': '15%', 'marginRight': '10px', 'height': '35px'}),
        dbc.Button("Undo", id="btn-undo", color="secondary", disabled=False, style={'width': '15%', 'marginRight': '10px', 'height': '35px'}),
        dbc.Button("Clear", id="btn-clear", color="secondary", disabled=False, style={'width': '15%', 'marginRight': '10px', 'height': '35px'}),
        dbc.Button("Simulate", id="btn-simulate", color="primary", disabled=False, style={'width': '15%', 'marginRight': '10px', 'height': '35px'}),
    ], style={'display': 'flex'}),
    html.Div([
        html.Div([
                dcc.Graph(id='gate-view-plot', style={'height': '700px'}),
        ], style={'width': '50%', 'paddingLeft': '10px'}),

        html.Div([
            html.Div(configpage.layout, id='config-page', style={'display': 'none'}),
            html.Pre(id='backend-log', style={
                'height': '700px',
                'overflowY': 'scroll',
                'backgroundColor': '#111',
                'color': '#0f0',
                'paddingleft': '10px',
                'border': '1px solid #333',
                'fontFamily': 'monospace',
                'whiteSpace': 'pre-wrap'
            }),
        ], style={
            'width': '50%',
            'paddingLeft': '10px',
            'justifyContent': 'flex-end'
        }),
    ], style={'display': 'flex', 'flexDirection': 'column'}, className='flex-row'),

    html.H1("Truth Table visualization", id='truth-table-label', style={'fontSize': '30px', 'marginTop': '40px', 'textAlign': 'center', 'color': '#ffffff', 'width': '100%'}),
    html.Div([
        html.Div([
            dcc.Graph(id='specific-gate', style={'height': '700px'}),
        ], style={'width': '50%', 'paddingLeft': '10px', 'display': 'flex', 'flexDirection': 'column'}),
        html.Div([
            dash_table.DataTable(
                id='circuit-truth-table',
                columns=[
                    {"name": "Result", "id": "result"},
                    {"name": "Expected", "id": "expected"},
                    {"name": "File_ID", "id": "file_id"},
                    {"name": "Energy", "id": "energy"},
                ],
                page_size=20,
                style_table={'overflowX': 'auto'},
                style_header={'backgroundColor': '#1e1e1e', 'color': 'white'},
                style_cell={'backgroundColor': '#111111', 'color': 'white'},
        )   
        ], style={
            'width': '50%',
            'paddingLeft': '10px',
            'justifyContent': 'flex-end'
        }),
    ], style={'display': 'flex', 'flexDirection': 'column'}, className='flex-row'),
        dcc.Slider(
        id='gate-slider',
        min=0,
        max=0,
        step=1,
        value=0,
        tooltip={"placement": "bottom", "always_visible": False},
        className='ppm-slider'
    ),
    html.Button("Save as PNG", id='save-button', n_clicks=0, style={'width': '200px', 'height': '35px', 'marginTop': '20px'}),
    html.Button("Auto Batch", id='auto-batch-button', n_clicks=0, style={'width': '200px', 'height': '35px', 'marginTop': '20px'}),
    html.Button("Save gate", id='save-gate-button', n_clicks=0, style={'width': '200px', 'height': '35px', 'marginTop': '20px'})
])
    

placeholder_fig = plot_NML('')
placeholder_fig.update_layout(
    title="Select a gate to view",
    height=700,
)

#Callbacks

@callback(
    Output('config-page', 'style'),
    Output('backend-log', 'style'),
    Input('toggle-config', 'n_clicks'),
    State('config-page', 'style'),
    Input('apply-config', 'n_clicks'),
    Input('close-config', 'n_clicks'),
    prevent_initial_call=True
)
def toggle_config(n_clicks, current_style, apply_click, close_click):
    if n_clicks is None:
        return current_style or {'display': 'none'}
    if current_style is None or current_style.get('display') == 'none':
        return {'display': 'block', 'marginTop': '20px'}, {'height': '400px', 'overflowY': 'scroll', 'backgroundColor': '#111', 'color': '#0f0', 'paddingleft': '10px', 'border': '1px solid #333', 'fontFamily': 'monospace', 'whiteSpace': 'pre-wrap', 'display': 'none'}
    else:
        return {'display': 'none'}, {'height': '700px', 'overflowY': 'scroll', 'backgroundColor': '#111', 'color': '#0f0', 'paddingleft': '10px', 'border': '1px solid #333', 'fontFamily': 'monospace', 'whiteSpace': 'pre-wrap', 'display': 'block'}
@callback(
    Output('wire-lenght-store', 'data'),
    Input('wire-length-input', 'value')        
)
def update_wire_length(value):
    if value is None or value < 1:
        return 1
    return value

@callback(
    Output('fixes-allowed-store', 'data'),
    Input('fixes-allowed', 'value')
)
def update_fixes_allowed(value):
    if value is None or value < 0:
        return 0
    return value

@callback(
    Output('gate-view-plot', 'figure'),
    Output('gate-storage', 'data'),
    Input('btn-connect', 'n_clicks'),
    Input('btn-undo', 'n_clicks'),
    Input('btn-clear', 'n_clicks'),
    Input('selected-gates-store', 'data'),
    State('files-store', 'data'),
    Input('wire-lenght-store', 'data'),
    Input('algorithm-store', 'data')
)
def update_gate_view(_,__, ___, selected_gates, files_data, wire_lenght, algorithm):
    if not selected_gates or not files_data:
        return placeholder_fig, None
    gate_names = [os.path.basename(gate) for gate in selected_gates]
    files = selected_gates

    try:
        connection = pipeline.connect_gates(files, wire_length=wire_lenght, strategy=algorithm)
    except (ValueError, NotImplementedError) as exc:
        print(f"⚠️ {exc}")
        return placeholder_fig, None

    gate = connection.gate
    viewport = get_viewport(gate)
    fig = plot_NML(gate, viewport, len(selected_gates), connection.gate_pos_name)
    print(f"Selected gates: {gate_names}, Wire length: {connection.wire_length}, {viewport_size(viewport)}")
    print(f"Gate expression: {gate.expression}")

    return fig, gate.to_dict()

@callback(
    Output('files-store', 'data'),
    Input('load-files', 'n_intervals'),
    Input('btn-main', 'n_clicks'),
)

def load_files(_, __):
    return pipeline.discover_gate_files(data_sqd)  # This will be a list of file paths

@callback(
    Output('gate-dropdown', 'options'),
    Input('files-store', 'data')
)
def populate_dropdown(file_list):
    if not file_list:
        return []
    return [{"label": os.path.basename(f), "value": f} for f in file_list]

@callback(
    Output('selected-gates-store', 'data'),
    Input('btn-connect', 'n_clicks'),
    Input('btn-undo', 'n_clicks'),
    Input('btn-clear', 'n_clicks'),
    State('gate-dropdown', 'value'),
    State('selected-gates-store', 'data'),
    prevent_initial_call=True
)
def store_selected_gate(button1, button2, button3, selected_gate, stored):
    ctx = dash.callback_context
    if stored is None:
        stored = []
    if not selected_gate:
        return stored
    if ctx.triggered:
        triggered_id = ctx.triggered[0]['prop_id'].split('.')[0]
        if triggered_id == 'btn-clear':
            stored = []
        elif triggered_id == 'btn-undo':
            if stored and len(stored) > 0:
                stored.pop()
        elif triggered_id == 'btn-connect':
            stored.append(selected_gate)
    return stored

@callback(
    Output('circuit-truth-table', 'data'),
    Output('specific-gate-data', 'data'),
    Input('btn-simulate', 'n_clicks'),
    State('gate-storage', 'data'),
    State('current-sim-store', 'data'),
    State('config-sim-store', 'data'),
    State('fixes-allowed-store', 'data'),
)
def simulate_circuit(n_clicks, gate, current_sim, config_sim, max_corrections=0, called_from_callback=True, max_mismatches=1):
    sim_result = pipeline.run_simulation(
        gate,
        simulator=current_sim,
        config_sim=config_sim,
        max_corrections=max_corrections,
        max_mismatches=max_mismatches,
        pad_page_size=20,
        verbose=called_from_callback,
    )

    if called_from_callback:
        if gate and not sim_result.failed:
            print("Simulation completed.")
        return sim_result.table_data, sim_result.specific_gate_data
    return (
        sim_result.table_data,
        sim_result.specific_gate_data,
        sim_result.has_mismatches,
        sim_result.failed,
    )

@callback(
    Output('gate-slider', 'max'),
    Output('gate-slider', 'value'),
    Input('specific-gate-data', 'data'),
)
def update_slider(specific_gate_data):
    if not specific_gate_data:
        return 0, 0

    max_value = len(specific_gate_data) - 1
    return max_value, 0

@callback(
    Output('specific-gate', 'figure'),
    Input('specific-gate-data', 'data'),
    Input('gate-slider', 'value'),
)
def update_specific_gate(specific_gate_data, slider_value):
    if specific_gate_data is None:
        return placeholder_fig
    
    if slider_value < 0 or slider_value >= len(specific_gate_data):
        return placeholder_fig

    gate_info = specific_gate_data[slider_value]
    fig = plot_XY(gate_info)

    return fig




@callback(
    Output("backend-log", "children"),
    Output("log-trigger", "data"),  # trigger for JS
    Input("log-updater", "n_intervals")
)
def update_log(n):
    return StreamCapture.log_buffer.getvalue(), time.time()


clientside_callback(
    """
    function(trigger) {
        const logElement = document.getElementById('backend-log');
        if (!logElement) {
            return window.dash_clientside.no_update;
        }

        const threshold = 50; // pixels from bottom considered "at bottom"
        const atBottom = logElement.scrollHeight - logElement.scrollTop - logElement.clientHeight < threshold;

        if (atBottom) {
            logElement.scrollTop = logElement.scrollHeight;
        }

        return window.dash_clientside.no_update;
    }
    """,
    Output("log-trigger", "data", allow_duplicate=True),  # dummy output, won't change
    Input("log-trigger", "data"),
    prevent_initial_call=True
)

@callback(
    Input('save-button', 'n_clicks'),
    State('circuit-truth-table', 'data'),
    State('specific-gate-data', 'data'),
    State('gate-storage', 'data'),
    State('current-sim-store', 'data'),
    State('config-sim-store', 'data'),
    prevent_initial_call=True
)

def save_current(n_clicks, table_data, specific_gate_data, gate_storage, sim_store, config_sim_store):
    if n_clicks is None or n_clicks <= 0:
        return

    if not table_data or not specific_gate_data:
        print("⚠️ No data to save.")
        return

    exporter.save_simulation_results(
        gate_storage,
        table_data,
        specific_gate_data,
        simulator_name=sim_store,
        config_sim=config_sim_store,
    )

@callback(
    Input('auto-batch-button', 'n_clicks'),
    State('files-store', 'data'),
    State('current-sim-store', 'data'),
    State('config-sim-store', 'data'),
    prevent_initial_call=True
)

def auto_batch_simulation(n_clicks, files_data, current_sim, config_sim_store):
    if n_clicks is None or n_clicks <= 0:
        return

    if not files_data or len(files_data) == 0:
        print("⚠️ No files to process for auto batch.")
        return

    pipeline.run_batch_simulation(
        gate_files=files_data,
        max_gates=5,
        connector="FIFO",
        simulator=current_sim,
        config_sim=config_sim_store,
        wire_length=1,
        max_corrections=5,
        max_mismatches=1,
        cooldown=True,
    )

@callback(
    Input('save-gate-button', 'n_clicks'),
    State('gate-storage', 'data'),
    prevent_initial_call=True   
)

def save_gate(button, gate):
    if button is None or button <= 0:
        return

    if not gate or len(gate) == 0:
        print("⚠️ No files to select for saving gate.")
        return

    exporter.save_gate_sqd(gate)