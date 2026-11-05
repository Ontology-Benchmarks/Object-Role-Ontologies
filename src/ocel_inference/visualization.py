import re
from pathlib import Path
from typing import Optional

import pandas as pd
from pyvis.network import Network


class OCELSchemaVisualizer:
    """A class to support all graph visualization rendering and HTML injection logic 
    based on the object interaction graphs and aggregations generated in the OCELSchemaInferrer class."""

    def __init__(self, template_dir: Optional[str] = None, log_fn=None):
        # Sidebar/CSS/JS templates ship with the package.
        self.template_dir = Path(template_dir) if template_dir else Path(__file__).parent / "assets"
        self._log_fn = log_fn

    def _log(self, message: str) -> None:
        if self._log_fn:
            self._log_fn(message)

    def visualize_object_interaction_graph(self, schema_df: pd.DataFrame, output_file: str = "object_interaction_graph.html"):
        """
        Visualizes the Object Interaction Graph (OIG) using PyVis (interactive HTML).
        Nodes are Object Types (sized by max population).
        Edges are Event Types (labeled with cardinalities).
        """
        self._log(f"Generating interactive Object Interaction Graph (OIG) visualization: {output_file}")

        net = Network(height="100vh", width="100%", bgcolor="#f0f2f5", font_color="#333", directed=False,
                      cdn_resources="in_line")

        node_pops = {}
        for _, row in schema_df.iterrows():
            src = row['otype1']
            tgt = row['otype2']
            node_pops[src] = row['otype1_total_pop']
            node_pops[tgt] = row['otype2_total_pop']

        for node, pop in node_pops.items():
            label = f"{node}\n(Total={pop})"
            net.add_node(
                node,
                label=label,
                title=f"Object: {node} (Total Pop: {pop})",
                shape='box',
                font={'size': 20, 'face': 'Arial', 'color': '#333'},
                color={'background': '#ffffff', 'border': '#1a73e8', 'highlight': {'background': '#e8f0fe', 'border': '#1a73e8'}},
                borderWidth=2,
                margin=15,
                shadow=True
            )

        for idx, row in schema_df.iterrows():
            src = row['otype1']
            tgt = row['otype2']
            event = row['viatype']
            card_ab = f"{int(row['ABCardinalityMin'])}..{int(row['ABCardinalityMax'])}"
            card_ba = f"{int(row['BACardinalityMin'])}..{int(row['BACardinalityMax'])}"
            edge_label = f"{event}\n{card_ab} : {card_ba}"

            net.add_edge(
                src,
                tgt,
                id=str(idx),
                label=edge_label,
                color={'color': '#5f6368', 'highlight': '#d93025'},
                font={'align': 'horizontal', 'size': 14, 'background': '#fafafa', 'strokeWidth': 1, 'strokeColor': '#cccccc'},
                width=1.5,
                arrowStrikethrough=False
            )

        net.set_options("""
        var options = {
          "physics": {
            "barnesHut": {
              "gravitationalConstant": -20000,
              "centralGravity": 0.1,
              "springLength": 350,
              "springConstant": 0.03,
              "damping": 0.5,
              "avoidOverlap": 0.6
            },
            "minVelocity": 0.75,
            "stabilization": {
                "enabled": true,
                "iterations": 1000,
                "updateInterval": 50,
                "onlyDynamicEdges": false,
                "fit": true
            }
          },
          "edges": {
            "smooth": {
              "type": "dynamic",
              "forceDirection": "none",
              "roundness": 0.5
            },
            "arrows": {
              "to": {
                "enabled": true,
                "scaleFactor": 0.5
              }
            }
          }
        }
        """)

        net.write_html(output_file)
        self._inject_interactive_js(output_file, schema_df)
        self._log(f"Object Interaction Graph (OIG) saved to {output_file}")

    def visualize_schema(self, schema_df: pd.DataFrame, output_file: str = "schema_graph.html"):
        """Backward-compatible alias for visualize_object_interaction_graph()."""
        self.visualize_object_interaction_graph(schema_df, output_file)

    def visualize_object_role_ontology_graph(self, stats_df: pd.DataFrame, output_file: str = "object_role_ontology_graph.html"):
        """
        Visualizes object role ontology (ORO) triples in a tripartite graph:
        Event Type <-> EventObjectRole <-> Object Type.
        """
        self._log(f"Generating Object Role Ontology (ORO) graph visualization: {output_file}")

        if stats_df.empty:
            self._log("No Object Role Ontology (ORO) statistics available; skipping visualization.")
            return

        stats_df = (
            stats_df
            .groupby(['activity', 'otype'], as_index=False)
            .agg({
                'part_pop': 'max',
                'total_pop': 'max',
                'earliest_pop': 'max',
                'latest_pop': 'max',
                'object_role_min_card': 'min',
                'object_role_max_card': 'max',
                'event_role_min_card': 'min',
                'event_role_max_card': 'max',
                'min_card': 'min',
                'max_card': 'max'
            })
        )

        net = Network(height="100vh", width="100%", bgcolor="#f0f2f5", font_color="#333", directed=False,
                      cdn_resources="in_line")

        event_types = sorted(stats_df['activity'].unique().tolist())
        object_types = sorted(stats_df['otype'].unique().tolist())
        role_pairs = sorted(stats_df[['activity', 'otype']].drop_duplicates().itertuples(index=False, name=None))

        def format_role_display(activity: str, otype: str) -> str:
            activity_tokens = [t for t in re.split(r'[^A-Za-z0-9]+', str(activity).strip()) if t]
            object_tokens = [t for t in re.split(r'[^A-Za-z0-9]+', str(otype).strip()) if t]

            if len(activity_tokens) >= 2:
                activity_code = activity_tokens[0][0] + activity_tokens[1][0]
            elif len(activity_tokens) == 1:
                activity_code = activity_tokens[0][:2]
            else:
                activity_code = 'XX'

            activity_code = (activity_code + 'X')[:2].upper()
            object_code = (object_tokens[0][0] if object_tokens else 'X').upper()
            return f"Role_{activity_code}_{object_code}"

        def label_size_for_hex(label: str, font_size: int, padding: int, min_width: int, min_height: int):
            lines = [line for line in str(label).split('\n') if line is not None]
            if not lines:
                lines = ['']

            longest_line = max((len(line) for line in lines), default=0)
            line_count = max(len(lines), 1)

            approx_char_width = font_size * 0.72
            approx_line_height = font_size * 1.35

            width = int((longest_line * approx_char_width) + (padding * 2) + 24)
            height = int((line_count * approx_line_height) + (padding * 2) + 10)

            return {
                'minimum': max(width, min_width)
            }, {
                'minimum': max(height, min_height)
            }

        event_spacing = 150
        role_spacing = 150
        object_spacing = 150
        event_offset = -((len(event_types) - 1) * event_spacing) / 2 if event_types else 0
        role_offset = -((len(role_pairs) - 1) * role_spacing) / 2 if role_pairs else 0
        object_offset = -((len(object_types) - 1) * object_spacing) / 2 if object_types else 0

        for idx, event_type in enumerate(event_types):
            node_id = f"event::{event_type}"
            event_x = event_offset + (idx * event_spacing)
            net.add_node(
                node_id,
                label=f"Activity\n{event_type}",
                title=f"Activity: {event_type}",
                shape='ellipse',
                color={'background': '#fff4e5', 'border': '#f9ab00', 'highlight': {'background': '#fef7e0', 'border': '#f9ab00'}},
                borderWidth=2,
                font={'size': 18, 'face': 'Arial', 'color': '#333'},
                margin=14,
                shadow=True,
                group='event',
                x=event_x,
                y=170,
                physics=True
            )

        for idx, (activity, otype) in enumerate(role_pairs):
            role_x = role_offset + (idx * role_spacing)
            role_id = f"role::{activity}::{otype}"
            earliest_subrole_id = f"subrole::earliest::{activity}::{otype}"
            latest_subrole_id = f"subrole::latest::{activity}::{otype}"
            role_row = stats_df[(stats_df['activity'] == activity) & (stats_df['otype'] == otype)].iloc[0]
            part_pop = int(role_row['part_pop'])
            total_pop = int(role_row['total_pop'])
            earliest_pop = int(role_row['earliest_pop'])
            latest_pop = int(role_row['latest_pop'])
            min_card = int(role_row['min_card'])
            max_card = int(role_row['max_card'])
            object_role_min_card = int(role_row['object_role_min_card'])
            object_role_max_card = int(role_row['object_role_max_card'])
            event_role_min_card = int(role_row['event_role_min_card'])
            event_role_max_card = int(role_row['event_role_max_card'])
            role_display = format_role_display(activity, otype)
            earliest_label = f"Earliest\n{earliest_pop}/{part_pop}"
            latest_label = f"Latest\n{latest_pop}/{part_pop}"

            net.add_node(
                role_id,
                label=role_display,
                title=(
                    f"EventObjectRole<br>"
                    f"Role: {role_display}<br>"
                    f"Activity: {activity}<br>"
                    f"Object Type: {otype}<br>"
                    f"Participation: {part_pop}/{total_pop}<br>"
                    f"Earliest: {earliest_pop}/{part_pop}<br>"
                    f"Latest: {latest_pop}/{part_pop}<br>"
                    f"Events per Object: {object_role_min_card}..{object_role_max_card}<br>"
                    f"Objects per Event: {event_role_min_card}..{event_role_max_card}"
                ),
                shape='circle',
                color={'background': '#e6f4ea', 'border': '#188038', 'highlight': {'background': '#d7f1dd', 'border': '#188038'}},
                borderWidth=2,
                font={'size': 8, 'face': 'Arial', 'color': '#1f4f2b'},
                margin=2,
                shadow=True,
                group='role',
                x=role_x,
                y=0,
                physics=True
            )

            net.add_node(
                earliest_subrole_id,
                label=earliest_label,
                title=(
                    f"Earliest Subrole<br>"
                    f"Role: {role_display}<br>"
                    f"Earliest Participants: {earliest_pop}/{part_pop}"
                ),
                shape='circle',
                color={'background': '#e6f4ea', 'border': '#188038', 'highlight': {'background': '#d7f1dd', 'border': '#188038'}},
                borderWidth=2,
                font={'size': 7, 'face': 'Arial', 'color': '#0f5a24'},
                margin=2,
                shadow=False,
                group='subrole',
                x=role_x - 46,
                y=70,
                physics=True
            )

            net.add_node(
                latest_subrole_id,
                label=latest_label,
                title=(
                    f"Latest Subrole<br>"
                    f"Role: {role_display}<br>"
                    f"Latest Participants: {latest_pop}/{part_pop}"
                ),
                shape='circle',
                color={'background': '#fce8e6', 'border': '#c5221f', 'highlight': {'background': '#f9dedc', 'border': '#c5221f'}},
                borderWidth=2,
                font={'size': 7, 'face': 'Arial', 'color': '#8b1d1a'},
                margin=2,
                shadow=False,
                group='subrole',
                x=role_x + 46,
                y=70,
                physics=True
            )

        object_totals = (
            stats_df.groupby('otype', as_index=False)['total_pop']
            .max()
            .set_index('otype')['total_pop']
            .to_dict()
        )

        for idx, otype in enumerate(object_types):
            total_pop = int(object_totals.get(otype, 0))
            node_id = f"object::{otype}"
            object_x = object_offset + (idx * object_spacing)
            net.add_node(
                node_id,
                label=f"Object\n{otype}\n(Total={total_pop})",
                title=f"Object Type: {otype} (Total Pop: {total_pop})",
                shape='box',
                color={'background': '#ffffff', 'border': '#1a73e8', 'highlight': {'background': '#e8f0fe', 'border': '#1a73e8'}},
                borderWidth=2,
                font={'size': 16, 'face': 'Arial', 'color': '#333'},
                margin=14,
                shadow=True,
                group='object',
                x=object_x,
                y=-170,
                physics=True
            )

        for _, row in stats_df.iterrows():
            event_type = row['activity']
            otype = row['otype']
            part_pop = int(row['part_pop'])
            total_pop = int(row['total_pop'])
            earliest_pop = int(row['earliest_pop'])
            latest_pop = int(row['latest_pop'])
            min_card = int(row['min_card'])
            max_card = int(row['max_card'])
            object_role_min_card = int(row['object_role_min_card'])
            object_role_max_card = int(row['object_role_max_card'])
            event_role_min_card = int(row['event_role_min_card'])
            event_role_max_card = int(row['event_role_max_card'])
            role_id = f"role::{event_type}::{otype}"
            earliest_subrole_id = f"subrole::earliest::{event_type}::{otype}"
            latest_subrole_id = f"subrole::latest::{event_type}::{otype}"

            left_title = (
                f"playsRoleIn<br>"
                f"Activity: {event_type}<br>"
                f"Role: {event_type}/{otype}<br>"
                f"Objects per Event: {event_role_min_card}..{event_role_max_card}"
            )
            right_title = (
                f"isRoleOf<br>"
                f"Object Type: {otype}<br>"
                f"Participation: {part_pop}/{total_pop}<br>"
                f"Earliest: {earliest_pop}/{part_pop}<br>"
                f"Latest: {latest_pop}/{part_pop}<br>"
                f"Events per Object: {object_role_min_card}..{object_role_max_card}"
            )

            net.add_edge(
                f"event::{event_type}",
                role_id,
                id=f"edge::event_role::{event_type}::{otype}",
                label=f"{event_role_min_card}..{event_role_max_card}",
                title=left_title,
                color={'color': '#9aa0a6', 'highlight': '#5f6368'},
                font={'align': 'horizontal', 'size': 11, 'background': '#fafafa', 'strokeWidth': 1, 'strokeColor': '#dddddd'},
                width=1.5,
                length=110,
                hidden=True,
                arrowStrikethrough=False
            )

            net.add_edge(
                role_id,
                f"object::{otype}",
                id=f"edge::role_object::{event_type}::{otype}",
                label=f"{object_role_min_card}..{object_role_max_card}",
                title=right_title,
                color={'color': '#5f6368', 'highlight': '#d93025'},
                font={'align': 'horizontal', 'size': 12, 'background': '#fafafa', 'strokeWidth': 1, 'strokeColor': '#cccccc'},
                width=2,
                length=75,
                hidden=True,
                arrowStrikethrough=False
            )

            net.add_edge(
                role_id,
                earliest_subrole_id,
                id=f"edge::role_subrole::earliest::{event_type}::{otype}",
                label=f"{earliest_pop}/{part_pop}",
                title=f"Earliest participants for role {event_type}/{otype}: {earliest_pop}/{part_pop}",
                color={'color': '#34a853', 'highlight': '#188038'},
                font={'align': 'horizontal', 'size': 10, 'background': '#fafafa', 'strokeWidth': 1, 'strokeColor': '#dddddd'},
                width=1.3,
                length=28,
                hidden=True,
                arrowStrikethrough=False
            )

            net.add_edge(
                role_id,
                latest_subrole_id,
                id=f"edge::role_subrole::latest::{event_type}::{otype}",
                label=f"{latest_pop}/{part_pop}",
                title=f"Latest participants for role {event_type}/{otype}: {latest_pop}/{part_pop}",
                color={'color': '#ea4335', 'highlight': '#c5221f'},
                font={'align': 'horizontal', 'size': 10, 'background': '#fafafa', 'strokeWidth': 1, 'strokeColor': '#dddddd'},
                width=1.3,
                length=28,
                hidden=True,
                arrowStrikethrough=False
            )

        net.set_options('''
        var options = {
          "layout": {
                        "improvedLayout": true
          },
          "physics": {
                        "enabled": false,
                        "solver": "barnesHut",
                        "barnesHut": {
                            "gravitationalConstant": -5200,
                            "centralGravity": 0.26,
                            "springLength": 95,
                            "springConstant": 0.07,
                            "damping": 0.84,
                            "avoidOverlap": 0.65
                        },
                        "maxVelocity": 4,
                        "minVelocity": 0.45,
                        "stabilization": {
                            "enabled": true,
                            "iterations": 240,
                            "updateInterval": 25,
                            "fit": true
                        }
          },
          "interaction": {
            "dragNodes": true,
            "dragView": true,
            "zoomView": true
          },
          "edges": {
                        "arrows": {
                            "to": { "enabled": false },
                            "from": { "enabled": false }
                        },
            "smooth": {
                            "enabled": false
            }
          }
        }
        ''')

        net.write_html(output_file)
        self._inject_event_object_interactive_js(output_file, stats_df)
        self._log(f"Object Role Ontology (ORO) graph saved to {output_file}")

    def visualize_event_object_schema(self, stats_df: pd.DataFrame, output_file: str = "event_object_schema_graph.html"):
        """Backward-compatible alias for visualize_object_role_ontology_graph()."""
        self.visualize_object_role_ontology_graph(stats_df, output_file)

    def _inject_event_object_interactive_js(self, filepath, stats_df: pd.DataFrame):
        """Inject sidebar/table interaction for the Object Role Ontology (ORO) graph."""
        json_stats = stats_df.to_json(orient='records')

        try:
            css = (self.template_dir / "event_object_viz.css").read_text(encoding='utf-8')
            html_sidebar = (self.template_dir / "event_object_sidebar.html").read_text(encoding='utf-8')
            js = (self.template_dir / "event_object_viz.js").read_text(encoding='utf-8')
        except FileNotFoundError as e:
            self._log(f"Warning: Could not load ORO graph visualization templates. Error: {e}")
            return

        custom_content = f"""
        <style>{css}</style>
        {html_sidebar}
        <script>
            var eventObjectData = {json_stats};
            {js}
        </script>
        """

        with open(filepath, 'r', encoding='utf-8') as f:
            html_content = f.read()

        if "</body>" in html_content:
            new_html = html_content.replace("</body>", f"{custom_content}\n</body>")
        else:
            new_html = html_content + custom_content

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_html)

    def _calculate_aggregated_oig(self, schema_df):
        """Calculates Aggregated Object Interaction Graph (AOG) edges."""
        agg_rows = []

        df_w_idx = schema_df.copy()
        if 'id' not in df_w_idx.columns:
            df_w_idx['id'] = df_w_idx.index

        groups = df_w_idx.groupby(['otype1', 'otype2'])

        for (src, tgt), group in groups:
            min_ab = int(group['ABCardinalityMin'].min())
            max_ab = int(group['ABCardinalityMax'].max())
            min_ba = int(group['BACardinalityMin'].min())
            max_ba = int(group['BACardinalityMax'].max())

            agg_ab = f"{min_ab}..{max_ab}"
            agg_ba = f"{min_ba}..{max_ba}"

            event_types = sorted(group['viatype'].unique())
            label = f"{len(event_types)} Event Types" if len(event_types) > 1 else event_types[0]

            pop1 = group.iloc[0]['otype1_total_pop']
            pop2 = group.iloc[0]['otype2_total_pop']
            part_pop1 = group['otype1_part_pop'].max()
            part_pop2 = group['otype2_part_pop'].max()

            agg_rows.append({
                'id': f"agg_{src}_{tgt}",
                'from': src,
                'to': tgt,
                'label': f"{label}\n{agg_ab} : {agg_ba}",
                'viatype': label,
                'ABCardinalityMin': min_ab,
                'ABCardinalityMax': max_ab,
                'BACardinalityMin': min_ba,
                'BACardinalityMax': max_ba,
                'otype1': src,
                'otype2': tgt,
                'otype1_total_pop': pop1,
                'otype2_total_pop': pop2,
                'otype1_part_pop': part_pop1,
                'otype2_part_pop': part_pop2,
                'child_ids': group['id'].tolist()
            })

        return pd.DataFrame(agg_rows)

    def _calculate_aggregated_schema(self, schema_df):
        """Backward-compatible alias for _calculate_aggregated_oig()."""
        return self._calculate_aggregated_oig(schema_df)

    def _inject_interactive_js(self, filepath, schema_df):
        """Injects custom CSS, HTML, and JS, along with OIG and AOG data."""

        df_detailed = schema_df.copy()
        df_detailed['id'] = df_detailed.index
        json_detailed = df_detailed.to_json(orient='records')

        df_agg = self._calculate_aggregated_oig(df_detailed)
        json_agg = df_agg.to_json(orient='records')

        try:
            css = (self.template_dir / "schema_viz.css").read_text(encoding='utf-8')
            html_sidebar = (self.template_dir / "schema_sidebar.html").read_text(encoding='utf-8')
            js = (self.template_dir / "schema_viz.js").read_text(encoding='utf-8')
        except FileNotFoundError as e:
            self._log(f"Warning: Could not load visualization templates. Error: {e}")
            return

        with open(filepath, 'r', encoding='utf-8') as f:
            html_content = f.read()

        custom_content = f"""
        <style>{css}</style>
        {html_sidebar}
        <script>
            var schemaData = {json_detailed};
            var aggSchemaData = {json_agg};
            {js}
        </script>
        """

        if "</body>" in html_content:
            new_html = html_content.replace("</body>", f"{custom_content}\n</body>")
        else:
            new_html = html_content + custom_content

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_html)


