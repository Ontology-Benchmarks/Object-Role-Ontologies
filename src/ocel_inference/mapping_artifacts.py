"""Ontop mapping and artifact generation operations for OCEL inference."""

import json
import os
from typing import Dict, Optional


class MappingArtifactsMixin:
    def _generate_obda_mappings(self, custom_mapping_template: Optional[Dict] = None):
        """
        Generate OBDA mappings for the OCEL data structures.

        Default behavior reads from an external OBDA template file and expands
        the {{otype}} placeholder block for each object type.
        
        Args:
            custom_mapping_template (Dict, optional): Custom mapping template to use.
                If provided (or previously set via set_custom_mapping_template),
                falls back to the legacy dictionary-based mapping builder.
        """
        template_override = custom_mapping_template or self._custom_template

        if template_override is not None:
            mapping_template = {
                "prefixes": dict(template_override.get("prefixes", {})),
                "mappings": dict(template_override.get("mappings", {}))
            }

            for otype in self.object_types:
                mapping_id = f"objects_type_{otype}"
                mapping_template["mappings"][mapping_id] = {
                    "target": f"ex:{{object_id}} a ex:otype_{otype} .",
                    "source": f"SELECT object_id FROM obj_{otype}"
                }

            obda_content = self._build_obda_content(mapping_template)
            self._log("Using legacy dictionary template for ORO construction mapping generation")
        else:
            obda_content = self._build_obda_content_from_template_file()
            self._log(f"Using ORO construction mapping template file: {self.obda_template_file}")
        obda_content = self._add_oro_mappings(obda_content)

        # Write to file
        self.obda_mapping_file.write_text(obda_content, encoding='utf-8')
        self._log(f"✓ ORO construction mappings written to {self.obda_mapping_file}")
        self._log(f"  File size: {len(obda_content)} bytes")
        

        properties_content = self._build_properties_content()
        self.properties_file.write_text(properties_content, encoding='utf-8')
        self._log(f"✓ Properties file written to {self.properties_file}")
        self._log(f"  File size: {len(properties_content)} bytes")
        
        # Print the database URL for debugging
        lines = properties_content.split('\n')
        for line in lines:
            if 'jdbc.url' in line:
                self._log(f"  Database URL: {line.split('=')[1] if '=' in line else 'N/A'}")

    def _add_oro_mappings(self, obda_content: str) -> str:
        """Append the mappings of the ORO vocabulary (schemata.py) before the closing ``]]``.

        Reified roles r_{o,e} get the IRI oro:role/<object>/<event>. Each class R_a(ot), Role(ot)
        and Ev_a is one mapping with a constant class IRI, generated from the log's role pairs.
        """
        from .schemata import ORO, event_type, role, role_of_type

        def sql(text: str) -> str:
            return "'" + str(text).replace("'", "''") + "'"

        r = f"<{ORO}role/{{object_id}}/{{eid}}>"
        r1, r2 = f"<{ORO}role/{{object_id}}/{{eid1}}>", f"<{ORO}role/{{object_id}}/{{eid2}}>"
        blocks = [
            ("oro_roles",
             f"{r} a <{ORO}Role> ; <{ORO}memberOf> ex:eventRole_{{activity}}_{{otype}} . "
             f"ex:{{object_id}} <{ORO}playsRole> {r} . ex:{{eid}} <{ORO}frames> {r} .",
             "SELECT object_id, eid, otype, activity FROM oro_roles"),
            ("oro_objects", f"ex:{{object_id}} a <{ORO}Object> .", "SELECT object_id FROM objects"),
            ("oro_events", f"ex:{{eid}} a <{ORO}Event> .", "SELECT eid FROM log_events"),
            ("oro_next_role", f"{r1} <{ORO}nextRole> {r2} .", "SELECT object_id, eid1, eid2 FROM oro_next_role"),
            ("oro_later_role", f"{r1} <{ORO}laterRole> {r2} .",
             "SELECT a.object_id, a.eid AS eid1, b.eid AS eid2 FROM oro_roles a "
             "JOIN oro_roles b ON a.object_id = b.object_id AND a.pos < b.pos"),
        ]
        for i, (activity, otype) in enumerate(self.role_pairs):
            blocks.append((f"oro_role_class_{i}", f"{r} a <{role(activity, otype).iri}> .",
                           f"SELECT object_id, eid FROM oro_roles WHERE otype = {sql(otype)} AND activity = {sql(activity)}"))
        for i, otype in enumerate(sorted({otype for _, otype in self.role_pairs})):
            blocks.append((f"oro_role_type_{i}", f"{r} a <{role_of_type(otype).iri}> .",
                           f"SELECT object_id, eid FROM oro_roles WHERE otype = {sql(otype)}"))
        for i, activity in enumerate(self.activities):
            blocks.append((f"oro_event_class_{i}", f"ex:{{eid}} a <{event_type(activity).iri}> .",
                           f"SELECT eid FROM log_events WHERE activity = {sql(activity)}"))

        text = "".join(f"\nmappingId\t{mid}\ntarget\t\t{target}\nsource\t\t{source}\n" for mid, target, source in blocks)
        end = obda_content.rstrip().rfind("]]")
        return obda_content[:end].rstrip("\n") + "\n" + text + "\n]]\n"

    def _build_obda_content_from_template_file(self) -> str:
        """Load ORO construction mapping template from file and expand per-object-type mapping blocks."""
        if not self.obda_template_file.exists():
            raise FileNotFoundError(
                f"ORO construction mapping template not found: {self.obda_template_file}. "
                "Create this file with a placeholder mapping block containing {{otype}}."
            )

        template_text = self.obda_template_file.read_text(encoding='utf-8')
        return self._expand_obda_otype_placeholders(template_text)

    def _expand_obda_otype_placeholders(self, template_text: str) -> str:
        """
        Expand contiguous template lines containing {{otype}} once per object type.
        """
        lines = template_text.splitlines()
        expanded_lines = []
        i = 0

        while i < len(lines):
            if "{{otype}}" in lines[i]:
                block = []
                while i < len(lines) and "{{otype}}" in lines[i]:
                    block.append(lines[i])
                    i += 1

                for idx, otype in enumerate(self.object_types):
                    for block_line in block:
                        expanded_lines.append(block_line.replace("{{otype}}", otype))
                    if idx < len(self.object_types) - 1:
                        expanded_lines.append("")
            else:
                expanded_lines.append(lines[i])
                i += 1

        trailing_newline = "\n" if template_text.endswith("\n") else ""
        return "\n".join(expanded_lines) + trailing_newline

    def _generate_ontop_lenses_template(self):
        """
        Generate a template Ontop lenses JSON file for optional use.
        The template is safe by default (pass-through BasicLens definitions).
        """
        template = self._build_lenses_template()
        self.lenses_file.write_text(json.dumps(template, indent=2), encoding='utf-8')
        self._log(f"✓ Lenses template written to {self.lenses_file}")
        self._log(f"  File size: {self.lenses_file.stat().st_size} bytes")

    def _build_lenses_template(self) -> Dict:
        """Build a lenses template by loading from a JSON template file."""
        
        # Load the lens template from the staging directory
        template_file = self.staging_dir / "lens_template.json"
        
        if not template_file.exists():
            self._log(f"Warning: Lens template file not found at {template_file}")
            self._log(f"Creating a default lens structure...")
            return self._create_default_lens_template()
        
        with open(template_file, 'r') as f:
            template = json.load(f)
        
        return template
    
    def _create_default_lens_template(self) -> Dict:
        """Create a default lens template structure if none is provided."""
        return {
            "relations": [
                {
                    "name": "lens_log_events",
                    "baseRelation": ["\"log_events\""],
                    "filterExpression": "",
                    "columns": {
                        "added": [],
                        "hidden": []
                    },
                    "uniqueConstraints": {
                        "added": [
                            {
                                "name": "pk_log_events",
                                "determinants": ["\"eid\""],
                                "isPrimaryKey": True
                            }
                        ]
                    },
                    "otherFunctionalDependencies": {
                        "added": []
                    },
                    "foreignKeys": {
                        "added": []
                    },
                    "nonNullConstraints": {
                        "added": []
                    },
                    "type": "BasicLens"
                },
                {
                    "name": "lens_log_e2o",
                    "baseRelation": ["\"log_e2o\""],
                    "filterExpression": "",
                    "columns": {
                        "added": [],
                        "hidden": []
                    },
                    "uniqueConstraints": {
                        "added": [
                            {
                                "name": "pk_log_e2o",
                                "determinants": ["\"eid\"", "\"object_id\""],
                                "isPrimaryKey": True
                            }
                        ]
                    },
                    "otherFunctionalDependencies": {
                        "added": []
                    },
                    "foreignKeys": {
                        "added": [
                            {
                                "name": "fk_log_e2o_log_events",
                                "from": ["\"eid\""],
                                "to": {
                                    "relation": "lens_log_events",
                                    "columns": ["\"eid\""]
                                }
                            }
                        ]
                    },
                    "nonNullConstraints": {
                        "added": []
                    },
                    "type": "BasicLens"
                },
                {
                    "name": "lens_log_o2o",
                    "baseRelation": ["\"log_o2o\""],
                    "filterExpression": "",
                    "columns": {
                        "added": [],
                        "hidden": []
                    },
                    "uniqueConstraints": {
                        "added": [
                            {
                                "name": "pk_log_o2o",
                                "determinants": ["\"oid1\"", "\"oid2\"", "\"event_id\""],
                                "isPrimaryKey": True
                            }
                        ]
                    },
                    "otherFunctionalDependencies": {
                        "added": []
                    },
                    "foreignKeys": {
                        "added": [
                            {
                                "name": "fk_log_o2o_log_events",
                                "from": ["\"event_id\""],
                                "to": {
                                    "relation": "lens_log_events",
                                    "columns": ["\"eid\""]
                                }
                            }
                        ]
                    },
                    "nonNullConstraints": {
                        "added": []
                    },
                    "type": "BasicLens"
                },
                {
                    "name": "lens_log_o2o_detailed",
                    "baseRelation": ["\"log_o2o_detailed\""],
                    "filterExpression": "",
                    "columns": {
                        "added": [],
                        "hidden": []
                    },
                    "uniqueConstraints": {
                        "added": [
                            {
                                "name": "pk_log_o2o_detailed",
                                "determinants": ["\"oid1\"", "\"oid2\"", "\"event_id\""],
                                "isPrimaryKey": True
                            }
                        ]
                    },
                    "otherFunctionalDependencies": {
                        "added": []
                    },
                    "foreignKeys": {
                        "added": [
                            {
                                "name": "fk_log_o2o_detailed_log_events",
                                "from": ["\"event_id\""],
                                "to": {
                                    "relation": "lens_log_events",
                                    "columns": ["\"eid\""]
                                }
                            },
                            {
                                "name": "fk_log_o2o_detailed_log_o2o",
                                "from": ["\"oid1\"", "\"oid2\"", "\"event_id\""],
                                "to": {
                                    "relation": "lens_log_o2o",
                                    "columns": ["\"oid1\"", "\"oid2\"", "\"event_id\""]
                                }
                            }
                        ]
                    },
                    "nonNullConstraints": {
                        "added": []
                    },
                    "type": "BasicLens"
                }
            ]
        }

    def set_custom_mapping_template(self, mapping_template: Dict) -> None:
        """
        Set a custom OBDA mapping template that will be used for the next inference run.
        
        Args:
            mapping_template (Dict): A dictionary with 'prefixes' and 'mappings' keys.
                Example structure:
                {
                    'prefixes': {
                        'ex': 'http://www.example.com/',
                        'ocel': 'https://stl.mie.utoronto.ca/ontologies/ocel/'
                    },
                    'mappings': {
                        'events': {
                            'target': 'ex:{eid} a ocel:Event ; ...',
                            'source': 'SELECT * FROM log_events'
                        },
                        ...
                    }
                }
        """
        self._custom_template = mapping_template
        self._log(f"Custom mapping template set with {len(mapping_template.get('mappings', {}))} mappings")

    def get_default_mapping_template(self) -> Dict:
        """
        Get the default OCEL to RDF mapping template for customization.
        Useful for users who want to modify the defaults.
        
        Returns:
            Dict: The default mapping template
        """
        return self._get_default_mapping_template()

    def _get_default_mapping_template(self) -> Dict:
        """
        Get the default OCEL to RDF mapping template.
        Returns a template that can be customized.
        """
        return {
            "prefixes": {
                "ex": "http://www.example.com/",
                "ocel": "https://stl.mie.utoronto.ca/ontologies/ocel/",
                "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                "xsd": "http://www.w3.org/2001/XMLSchema#"
            },
            "mappings": {
                "events": {
                    "target": "ex:{eid} a ocel:Event ; ocel:hasActivity ocel:{activity} ; ocel:timestamp {timestamp}^^xsd:dateTime .",
                    "source": "SELECT * FROM log_events"
                },
                "e2o": {
                    "target": "ex:{eid} ocel:involves ex:{object_id} .",
                    "source": "SELECT eid, object_id FROM log_e2o"
                },
                "objects": {
                    "target": "ex:{object_id} a ocel:Object ; ocel:earliestEvent ex:{earliest_event_id} ; ocel:latestEvent ex:{latest_event_id} .",
                    "source": "SELECT object_id, earliest_event_id, latest_event_id FROM objects"
                },
                "o2o_consolidated": {
                    "target": "ex:o2o_{oid1}_{oid2}_{event_id} a ocel:ObjectRelationship ; ocel:relatesA ex:{oid1} ; ocel:relatesB ex:{oid2} ; ocel:viaEvent ex:{event_id} .",
                    "source": "SELECT oid1, oid2, event_id FROM o2o"
                }
            }
        }

    def _build_obda_content(self, mapping_template: Dict) -> str:
        """Build the OBDA file content from a mapping template."""
        lines = []
        
        # Prefix declarations
        lines.append("[PrefixDeclaration]")
        for prefix, uri in mapping_template["prefixes"].items():
            lines.append(f"{prefix}:\t\t{uri}")
        
        lines.append("\n[MappingDeclaration] @collection [[")
        
        # Mappings
        for mapping_id, mapping_def in mapping_template["mappings"].items():
            lines.append(f"\nmappingId\t{mapping_id}")
            lines.append(f"target\t\t{mapping_def['target']}")
            lines.append(f"source\t\t{mapping_def['source']}")
        
        lines.append("\n]]")
        
        return "\n".join(lines)

    def _build_properties_content(self) -> str:
        """
        Build the properties file content for DuckDB
        Converts database path to be accessible from within the Ontop container.
        """
        # Convert to absolute path inside Ontop container
        # The input directory is mounted at /opt/ontop/input in the container
        # So we need to convert relative paths like "input/data/bpi17/bpi17.duckdb"
        # to "/opt/ontop/input/data/bpi17/bpi17.duckdb"
        
        db_path_str = str(self.db_path)
        
        # If path starts with "input/", prepend the container mount point
        if db_path_str.startswith("input/"):
            db_path_container = f"/opt/ontop/{db_path_str}"
        elif db_path_str.startswith("/workspace/input/"):
            # If absolute path, replace /workspace/input with /opt/ontop/input
            db_path_container = db_path_str.replace("/workspace/input", "/opt/ontop/input")
        else:
            # Fallback to absolute path
            db_path_container = os.path.abspath(db_path_str).replace("/workspace/input", "/opt/ontop/input")
        
        db_url = f"jdbc:duckdb:{db_path_container}"
        
        content = f"""# OCEL Schema Inferrer - Auto-generated properties
# Database Connection for DuckDB
jdbc.url={db_url}
jdbc.driver=org.duckdb.DuckDBDriver
jdbc.user=
jdbc.password=

# Ontop Settings
ontop.exposeSystemTables=false
ontop.cardinalityEstimationLoggingEnabled=false
"""
        return content
