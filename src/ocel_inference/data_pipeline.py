"""DuckDB data loading and OIG cardinality pipeline operations."""

import duckdb
import pandas as pd
import pm4py


class DataPipelineMixin:
    def _calculate_cardinalities(self) -> pd.DataFrame:
        """
        Run DuckDB queries to calculate O2O cardinalities.
        """
        # Execute SPARQL queries to compute cardinalities
        # No need for DuckDB preprocessing - SPARQL queries work directly on the RDF graph
        schema_df = self._execute_cardinality_queries(None)
        
        return schema_df

    def _debug_sparql_structure(self):
        """
        Run diagnostic SPARQL queries to understand the RDF data structure.
        Helps identify issues with mappings.
        """
        self._log("\n1. Testing basic SPARQL connectivity...")
        try:
            test_query = "SELECT (COUNT(*) as ?count) WHERE { ?s ?p ?o } LIMIT 1"
            result = self.query_sparql(test_query)
            if not result.empty:
                self._log(f"   ✓ SPARQL endpoint is working. Triple count: {result.iloc[0, 0] if len(result) > 0 else 'N/A'}")
            else:
                self._log("   ✗ No data returned from endpoint")
        except Exception as e:
            self._log(f"   ✗ SPARQL query failed: {e}")
            return
        
        self._log("\n2. Checking for Event instances...")
        event_query = '''
        PREFIX ex: <http://www.example.com/>
        PREFIX ocel: <https://stl.mie.utoronto.ca/ontologies/ocel/>
        SELECT (COUNT(DISTINCT ?event) as ?count) WHERE {
            ?event a ocel:Event .
        }
        '''
        try:
            result = self.query_sparql(event_query)
            count = result.iloc[0, 0] if not result.empty else 0
            self._log(f"   Found {count} Event instances")
            if count == 0:
                self._log("   → Checking alternative Event patterns...")
                alt_query = "SELECT ?s WHERE { ?s ?p <https://stl.mie.utoronto.ca/ontologies/ocel/Event> } LIMIT 5"
                alt_result = self.query_sparql(alt_query)
                self._log(f"     Alternative search returned {len(alt_result)} results")
        except Exception as e:
            self._log(f"   Error querying Events: {e}")
        
        self._log("\n3. Checking for Object instances...")
        obj_query = '''
        PREFIX ex: <http://www.example.com/>
        PREFIX ocel: <https://stl.mie.utoronto.ca/ontologies/ocel/>
        SELECT (COUNT(DISTINCT ?obj) as ?count) WHERE {
            ?obj a ocel:Object .
        }
        '''
        try:
            result = self.query_sparql(obj_query)
            count = result.iloc[0, 0] if not result.empty else 0
            self._log(f"   Found {count} Object instances")
        except Exception as e:
            self._log(f"   Error querying Objects: {e}")
        
        self._log("\n4. Checking for involves relationships...")
        involves_query = '''
        PREFIX ex: <http://www.example.com/>
        PREFIX ocel: <https://stl.mie.utoronto.ca/ontologies/ocel/>
        SELECT (COUNT(*) as ?count) WHERE {
            ?event ocel:involves ?obj .
        }
        '''
        try:
            result = self.query_sparql(involves_query)
            count = result.iloc[0, 0] if not result.empty else 0
            self._log(f"   Found {count} involves relationships")
        except Exception as e:
            self._log(f"   Error querying involves: {e}")
        
        self._log("\n5. Sample RDF predicates...")
        pred_query = '''
        SELECT DISTINCT ?p (COUNT(*) as ?count) WHERE {
            ?s ?p ?o .
        } GROUP BY ?p LIMIT 10
        '''
        try:
            result = self.query_sparql(pred_query)
            if not result.empty:
                self._log("   Predicates found:")
                for idx, row in result.iterrows():
                    self._log(f"      {row.iloc[0]}: {row.iloc[1]} triples")
            else:
                self._log("   No predicates found")
        except Exception as e:
            self._log(f"   Error listing predicates: {e}")
        
        self._log("\n6. Checking staging files...")
        self._log(f"   OBDA file: {self.obda_mapping_file}")
        self._log(f"   Exists: {self.obda_mapping_file.exists()}")
        if self.obda_mapping_file.exists():
            content = self.obda_mapping_file.read_text()
            lines = content.split('\n')
            self._log(f"   File size: {len(content)} bytes, {len(lines)} lines")
            if len(lines) > 5:
                self._log("   First few lines:")
                for line in lines[:5]:
                    self._log(f"      {line}")
        
        self._log(f"\n   Properties file: {self.properties_file}")
        self._log(f"   Exists: {self.properties_file.exists()}")
        if self.properties_file.exists():
            content = self.properties_file.read_text()
            lines = content.split('\n')
            self._log(f"   File size: {len(content)} bytes, {len(lines)} lines")


    def _execute_cardinality_queries(self, con) -> pd.DataFrame:
        """
        Execute cardinality calculation queries directly using DuckDB.
        Creates intermediate tables for access to detailed OIG relationship data.
        
        Returns an OIG DataFrame with object interactions and cardinalities.
        """
        # Connect to DuckDB database
        con = duckdb.connect(database=str(self.db_path), read_only=False)
        
        try:
            self._log("  Cardinality step 0/5: preparing OIG working table...")
            # 0. Materialize OIG working table without PK/FK constraints.
            o2o_cols = con.execute("PRAGMA table_info('log_o2o')").fetchall()
            o2o_type_map = {col_name: col_type for _, col_name, col_type, *_ in o2o_cols}

            oid1_type = o2o_type_map.get('oid1', 'VARCHAR')
            otype1_type = o2o_type_map.get('otype1', 'VARCHAR')
            oid2_type = o2o_type_map.get('oid2', 'VARCHAR')
            otype2_type = o2o_type_map.get('otype2', 'VARCHAR')
            event_id_type = o2o_type_map.get('event_id', 'VARCHAR')
            con.execute("DROP TABLE IF EXISTS o2o")
            con.execute('''
                CREATE TABLE o2o (
                    oid1 {} NOT NULL,
                    otype1 {} NOT NULL,
                    oid2 {} NOT NULL,
                    otype2 {} NOT NULL,
                    event_id {} NOT NULL
                )
            '''.format(oid1_type, otype1_type, oid2_type, otype2_type, event_id_type))
            con.execute('''
                INSERT INTO o2o
                SELECT DISTINCT oid1, otype1, oid2, otype2, event_id
                FROM log_o2o
                WHERE oid1 IS NOT NULL
                  AND otype1 IS NOT NULL
                  AND oid2 IS NOT NULL
                  AND otype2 IS NOT NULL
                  AND event_id IS NOT NULL
            ''')
            self._log(f"    OIG interaction rows: {con.execute('SELECT COUNT(*) FROM o2o').fetchone()[0]}")

            # 1. Derive "Via Types"
            self._log("  Cardinality step 1/5: deriving typed OIG links...")
            con.execute('''
                CREATE OR REPLACE TABLE derived_o2o_typed AS
                SELECT 
                    links.oid1,
                    e1.object_type AS type1,
                    links.oid2,
                    e2.object_type AS type2,
                    links.event_id AS eid,
                    ev.activity AS via_event_type
                FROM o2o links
                JOIN log_e2o e1 ON links.oid1 = e1.object_id AND links.event_id = e1.eid
                JOIN log_e2o e2 ON links.oid2 = e2.object_id AND links.event_id = e2.eid
                JOIN log_events ev ON links.event_id = ev.eid
            ''')
            
            # 2. Get Distinct Event Types
            self._log("  Cardinality step 2/5: deriving distinct type/event combinations...")
            con.execute('''
                CREATE OR REPLACE TABLE derived_o2o_typed_distinct AS
                SELECT DISTINCT
                    type1,
                    type2,
                    via_event_type
                FROM derived_o2o_typed
            ''')
            
            # 3. Calculate Cardinalities
            self._log("  Cardinality step 3/5: calculating min/max cardinalities...")
            con.execute('''
                CREATE OR REPLACE TABLE derived_o2o_etype_cardinalities AS
                WITH 
                actual_counts AS (
                    SELECT 
                        type1, type2, via_event_type, oid1, COUNT(DISTINCT oid2) as num_connected
                    FROM derived_o2o_typed
                    GROUP BY type1, type2, via_event_type, oid1
                ),
                relevant_objects AS (
                    SELECT DISTINCT 
                        t.type1, 
                        t.type2, 
                        t.via_event_type, 
                        link.object_id as oid1
                    FROM derived_o2o_typed_distinct t
                    JOIN log_events ev ON t.via_event_type = ev.activity
                    JOIN log_e2o link ON ev.eid = link.eid
                    WHERE link.object_type = t.type1
                ),
                object_stats AS (
                    SELECT 
                        base.type1,
                        base.type2,
                        base.via_event_type,
                        COALESCE(curr.num_connected, 0) as connection_count
                    FROM relevant_objects base
                    LEFT JOIN actual_counts curr 
                        ON base.type1 = curr.type1 
                        AND base.type2 = curr.type2 
                        AND base.via_event_type = curr.via_event_type
                        AND base.oid1 = curr.oid1
                )
                SELECT 
                    type1, 
                    type2,
                    via_event_type,
                    MIN(connection_count) AS min_card,
                    MAX(connection_count) AS max_card
                FROM object_stats
                GROUP BY type1, type2, via_event_type
            ''')
            
            # 4. Final AOG Aggregation (Updated with Global Populations)
            self._log("  Cardinality step 4/5: building final AOG table...")
            con.execute('''
                CREATE OR REPLACE TABLE o2o_etype_schema AS
                WITH event_specific_populations AS (
                    SELECT 
                        ev.activity AS event_type,
                        link.object_type,
                        COUNT(DISTINCT link.object_id) as event_pop
                    FROM log_events ev
                    JOIN log_e2o link ON ev.eid = link.eid
                    GROUP BY ev.activity, link.object_type
                ),
                global_populations AS (
                    SELECT object_type, COUNT(DISTINCT object_id) as total_pop
                    FROM log_e2o
                    GROUP BY object_type
                ),
                earliest_event_populations AS (
                    SELECT
                        obj.object_type,
                        ev.activity AS event_type,
                        COUNT(DISTINCT obj.object_id) AS earliest_pop
                    FROM objects obj
                    JOIN log_events ev ON obj.earliest_event_id = ev.eid
                    GROUP BY obj.object_type, ev.activity
                ),
                latest_event_populations AS (
                    SELECT
                        obj.object_type,
                        ev.activity AS event_type,
                        COUNT(DISTINCT obj.object_id) AS latest_pop
                    FROM objects obj
                    JOIN log_events ev ON obj.latest_event_id = ev.eid
                    GROUP BY obj.object_type, ev.activity
                )
                SELECT 
                    r.type1 AS otype1,
                    p1.event_pop AS otype1_part_pop,
                    gp1.total_pop AS otype1_total_pop,
                    COALESCE(ep1.earliest_pop, 0) AS otype1_earliest_pop,
                    COALESCE(lp1.latest_pop, 0) AS otype1_latest_pop,
                    r.type2 AS otype2,
                    p2.event_pop AS otype2_part_pop,
                    gp2.total_pop AS otype2_total_pop,
                    COALESCE(ep2.earliest_pop, 0) AS otype2_earliest_pop,
                    COALESCE(lp2.latest_pop, 0) AS otype2_latest_pop,
                    r.via_event_type AS viatype,
                    c_forward.min_card AS ABCardinalityMin,
                    c_forward.max_card AS ABCardinalityMax,
                    c_reverse.min_card AS BACardinalityMin,
                    c_reverse.max_card AS BACardinalityMax
                FROM derived_o2o_typed_distinct r
                JOIN derived_o2o_etype_cardinalities c_forward 
                    ON r.type1 = c_forward.type1 
                    AND r.type2 = c_forward.type2 
                    AND r.via_event_type = c_forward.via_event_type
                JOIN derived_o2o_etype_cardinalities c_reverse 
                    ON r.type1 = c_reverse.type2 
                    AND r.type2 = c_reverse.type1 
                    AND r.via_event_type = c_reverse.via_event_type
                JOIN event_specific_populations p1 
                    ON r.type1 = p1.object_type 
                    AND r.via_event_type = p1.event_type
                JOIN event_specific_populations p2 
                    ON r.type2 = p2.object_type 
                    AND r.via_event_type = p2.event_type
                JOIN global_populations gp1 
                    ON r.type1 = gp1.object_type
                JOIN global_populations gp2 
                    ON r.type2 = gp2.object_type
                LEFT JOIN earliest_event_populations ep1
                    ON r.type1 = ep1.object_type
                    AND r.via_event_type = ep1.event_type
                LEFT JOIN latest_event_populations lp1
                    ON r.type1 = lp1.object_type
                    AND r.via_event_type = lp1.event_type
                LEFT JOIN earliest_event_populations ep2
                    ON r.type2 = ep2.object_type
                    AND r.via_event_type = ep2.event_type
                LEFT JOIN latest_event_populations lp2
                    ON r.type2 = lp2.object_type
                    AND r.via_event_type = lp2.event_type
                WHERE r.type1 <= r.type2 
            ''')

            # 5. Materialize role rows for simple OBDA mapping queries
            self._log("  Cardinality step 5/5: materializing role statistics...")
            con.execute('''
                CREATE OR REPLACE TABLE o2o_event_object_roles AS
                WITH role_pairs AS (
                    SELECT DISTINCT
                        ev.activity AS activity,
                        link.object_type AS otype
                    FROM log_events ev
                    JOIN log_e2o link ON ev.eid = link.eid
                ),
                global_populations AS (
                    SELECT
                        object_type AS otype,
                        COUNT(DISTINCT object_id) AS total_pop
                    FROM objects
                    GROUP BY object_type
                ),
                role_participation_populations AS (
                    SELECT
                        ev.activity AS activity,
                        link.object_type AS otype,
                        COUNT(DISTINCT link.object_id) AS part_pop
                    FROM log_events ev
                    JOIN log_e2o link ON ev.eid = link.eid
                    GROUP BY ev.activity, link.object_type
                ),
                earliest_event_populations AS (
                    SELECT
                        ev.activity AS activity,
                        obj.object_type AS otype,
                        COUNT(DISTINCT obj.object_id) AS earliest_pop
                    FROM objects obj
                    JOIN log_events ev ON obj.earliest_event_id = ev.eid
                    GROUP BY ev.activity, obj.object_type
                ),
                latest_event_populations AS (
                    SELECT
                        ev.activity AS activity,
                        obj.object_type AS otype,
                        COUNT(DISTINCT obj.object_id) AS latest_pop
                    FROM objects obj
                    JOIN log_events ev ON obj.latest_event_id = ev.eid
                    GROUP BY ev.activity, obj.object_type
                ),
                object_role_counts AS (
                    SELECT
                        ev.activity AS activity,
                        link.object_type AS otype,
                        link.object_id,
                        COUNT(DISTINCT link.eid) AS role_count
                    FROM log_events ev
                    JOIN log_e2o link ON ev.eid = link.eid
                    GROUP BY ev.activity, link.object_type, link.object_id
                ),
                object_role_cardinalities AS (
                    SELECT
                        rp.activity,
                        rp.otype,
                        MIN(COALESCE(orc.role_count, 0)) AS object_role_min_card,
                        MAX(COALESCE(orc.role_count, 0)) AS object_role_max_card
                    FROM role_pairs rp
                    JOIN objects obj ON obj.object_type = rp.otype
                    LEFT JOIN object_role_counts orc
                        ON orc.activity = rp.activity
                        AND orc.otype = rp.otype
                        AND orc.object_id = obj.object_id
                    GROUP BY rp.activity, rp.otype
                ),
                event_role_counts AS (
                    SELECT
                        ev.activity AS activity,
                        link.object_type AS otype,
                        link.eid,
                        COUNT(DISTINCT link.object_id) AS object_count
                    FROM log_events ev
                    JOIN log_e2o link ON ev.eid = link.eid
                    GROUP BY ev.activity, link.object_type, link.eid
                ),
                event_role_cardinalities AS (
                    SELECT
                        rp.activity,
                        rp.otype,
                        MIN(COALESCE(erc.object_count, 0)) AS event_role_min_card,
                        MAX(COALESCE(erc.object_count, 0)) AS event_role_max_card
                    FROM role_pairs rp
                    JOIN log_events ev ON ev.activity = rp.activity
                    LEFT JOIN event_role_counts erc
                        ON erc.activity = rp.activity
                        AND erc.otype = rp.otype
                        AND erc.eid = ev.eid
                    GROUP BY rp.activity, rp.otype
                )
                SELECT
                    rp.activity,
                    rp.otype,
                    COALESCE(pp.part_pop, 0) AS part_pop,
                    COALESCE(gp.total_pop, 0) AS total_pop,
                    COALESCE(ep.earliest_pop, 0) AS earliest_pop,
                    COALESCE(lp.latest_pop, 0) AS latest_pop,
                    COALESCE(orc.object_role_min_card, 0) AS object_role_min_card,
                    COALESCE(orc.object_role_max_card, 0) AS object_role_max_card,
                    COALESCE(erc.event_role_min_card, 0) AS event_role_min_card,
                    COALESCE(erc.event_role_max_card, 0) AS event_role_max_card,
                    COALESCE(orc.object_role_min_card, 0) AS min_card,
                    COALESCE(orc.object_role_max_card, 0) AS max_card
                FROM role_pairs rp
                LEFT JOIN role_participation_populations pp
                    ON pp.activity = rp.activity
                    AND pp.otype = rp.otype
                LEFT JOIN global_populations gp
                    ON gp.otype = rp.otype
                LEFT JOIN earliest_event_populations ep
                    ON ep.activity = rp.activity
                    AND ep.otype = rp.otype
                LEFT JOIN latest_event_populations lp
                    ON lp.activity = rp.activity
                    AND lp.otype = rp.otype
                LEFT JOIN object_role_cardinalities orc
                    ON orc.activity = rp.activity
                    AND orc.otype = rp.otype
                LEFT JOIN event_role_cardinalities erc
                    ON erc.activity = rp.activity
                    AND erc.otype = rp.otype
            ''')
            
            # Retrieve Result
            self._log("  Retrieving final cardinality dataframe...")
            df_result = con.execute("SELECT * FROM o2o_etype_schema ORDER BY otype1, otype2, viatype").fetch_df()
            
            return df_result
        finally:
            con.close()

    def _get_event_object_population_stats(self) -> pd.DataFrame:
        """
        Build reified EventObjectRole statistics directly from DuckDB.

        Returns:
            pd.DataFrame with columns:
                - activity
                - otype
                - part_pop
                - total_pop
                - earliest_pop
                - latest_pop
                - object_role_min_card
                - object_role_max_card
                - event_role_min_card
                - event_role_max_card
                - min_card (backward-compatible alias of object_role_min_card)
                - max_card (backward-compatible alias of object_role_max_card)
        """
        con = duckdb.connect(database=str(self.db_path), read_only=False)
        try:
            return con.execute('''
                SELECT
                    activity,
                    otype,
                    part_pop,
                    total_pop,
                    earliest_pop,
                    latest_pop,
                    object_role_min_card,
                    object_role_max_card,
                    event_role_min_card,
                    event_role_max_card,
                    min_card,
                    max_card
                FROM o2o_event_object_roles
                ORDER BY activity, otype
            ''').fetch_df()
        finally:
            con.close()

    def visualize_object_role_ontology_graph(self, output_file: str = "object_role_ontology_graph.html"):
        """Delegate ORO graph visualization to the dedicated visualizer class."""
        stats_df = self._get_event_object_population_stats()
        self.visualizer.visualize_object_role_ontology_graph(stats_df, output_file)

    def visualize_event_object_schema(self, output_file: str = "event_object_schema_graph.html"):
        """Backward-compatible alias for visualize_object_role_ontology_graph()."""
        self.visualizer.visualize_event_object_schema(stats_df=self._get_event_object_population_stats(), output_file=output_file)

    def _load_and_transform(self):
        """Reads JSON, cleans columns, handles resources, and melts E2O data."""
        
        self._log("Loading OCEL JSON...")
        ocel_data = pm4py.read_ocel2_json(str(self.input_path))
        raw_df = ocel_data.get_extended_table()
        
        # Handle 'resource' column if present (P2P logic)
        if 'resource' in raw_df.columns:
            self._log("Detected 'resource' column, converting to object type...")
            raw_df['type:resource'] = raw_df['resource'].apply(lambda x: [x] if pd.notna(x) else [])
            raw_df = raw_df.drop(columns=['resource'])

        # Clean column names
        raw_df.columns = [(x.replace(' ', '_').replace('ocel:', '')).lower() for x in list(raw_df.columns)]
        
        # Filter columns
        object_columns = [x for x in list(raw_df.columns) if x.startswith('type:')]
        event_columns = set(raw_df.columns) - set(object_columns)
        
        # Create Event DataFrame
        event_df = raw_df[list(event_columns)].copy()
        
        # Create E2O DataFrame
        e2o_df = raw_df[['eid'] + object_columns].copy()
        e2o_df = e2o_df.melt(
            id_vars=['eid'], 
            value_vars=object_columns, 
            var_name='object_type', 
            value_name='object_id'
        )
        e2o_df = e2o_df.explode('object_id')
        e2o_df = e2o_df.dropna(subset=['object_id'])
        e2o_df['object_type'] = (
            e2o_df['object_type']
            .str.replace('type:', '')
            .str.replace(' ', '_')
        )
        
        # Store unique object types for DB creation
        self.object_types = [col.replace('type:', '').replace(' ', '_') for col in object_columns]
        
        return event_df, e2o_df

    def _init_db_tables(self, con, events_df: pd.DataFrame, e2o_df: pd.DataFrame):
        """Creates initial tables in DuckDB from pandas DataFrames."""
        # Register incoming dataframes and stage them in temporary tables for type introspection.
        self._log("  Registering DataFrames...")
        con.register('events_df', events_df)
        con.register('e2o_df', e2o_df)
        con.execute("CREATE TEMP TABLE _events_raw AS SELECT * FROM events_df")
        con.execute("CREATE TEMP TABLE _e2o_raw AS SELECT * FROM e2o_df")

        # Build log_events with an explicit primary key.
        self._log("  Creating log_events table...")
        events_cols = con.execute("PRAGMA table_info('_events_raw')").fetchall()
        event_defs = []
        for _, col_name, col_type, *_ in events_cols:
            not_null = " NOT NULL" if col_name == 'eid' else ""
            event_defs.append(f'"{col_name}" {col_type}{not_null}')

        con.execute("DROP TABLE IF EXISTS log_events")
        con.execute(f'''
            CREATE TABLE log_events (
                {', '.join(event_defs)},
                PRIMARY KEY (eid)
            )
        ''')
        con.execute('''
            INSERT INTO log_events
            SELECT DISTINCT *
            FROM _events_raw
            WHERE eid IS NOT NULL
        ''')
        self._log(f"    Created log_events with {con.execute('SELECT COUNT(*) FROM log_events').fetchone()[0]} rows")

        # Inspect E2O staging schema for type-safe table definitions.
        self._log("  Creating log_e2o table...")
        e2o_cols = con.execute("PRAGMA table_info('_e2o_raw')").fetchall()
        e2o_defs = []
        for _, col_name, col_type, *_ in e2o_cols:
            not_null = " NOT NULL" if col_name in {'eid', 'object_id', 'object_type'} else ""
            e2o_defs.append(f'"{col_name}" {col_type}{not_null}')

        # Build canonical objects table with object_id as primary key.
        object_id_type = next((col_type for _, col_name, col_type, *_ in e2o_cols if col_name == 'object_id'), 'VARCHAR')
        object_type_type = next((col_type for _, col_name, col_type, *_ in e2o_cols if col_name == 'object_type'), 'VARCHAR')
        
        # Get event_id type from events
        event_id_type = next((col_type for _, col_name, col_type, *_ in events_cols if col_name == 'eid'), 'VARCHAR')
        
        self._log("  Creating objects table (this may take a moment for large datasets)...")
        con.execute("DROP TABLE IF EXISTS objects")
        con.execute('''
            CREATE TABLE objects (
                object_id {} NOT NULL,
                object_type {} NOT NULL,
                earliest_event_id {},
                latest_event_id {},
                PRIMARY KEY (object_id),
                FOREIGN KEY (earliest_event_id) REFERENCES log_events(eid),
                FOREIGN KEY (latest_event_id) REFERENCES log_events(eid)
            )
        '''.format(object_id_type, object_type_type, event_id_type, event_id_type))
        
        # Optimized insert: compute event boundaries more efficiently
        self._log("    Computing earliest/latest events per object...")
        con.execute('''
            INSERT INTO objects
            SELECT 
                base.object_id,
                base.object_type,
                early.eid as earliest_event_id,
                late.eid as latest_event_id
            FROM (
                SELECT
                    object_id,
                    object_type,
                    ROW_NUMBER() OVER (PARTITION BY object_id ORDER BY object_type) AS rn
                FROM _e2o_raw
                WHERE object_id IS NOT NULL
                  AND object_type IS NOT NULL
            ) base
            LEFT JOIN (
                SELECT 
                    e2o.object_id,
                    e2o.eid,
                    ev.timestamp,
                    ROW_NUMBER() OVER (PARTITION BY e2o.object_id ORDER BY ev.timestamp ASC, e2o.eid ASC) as rn
                FROM _e2o_raw e2o
                LEFT JOIN _events_raw ev ON e2o.eid = ev.eid
            ) early ON base.object_id = early.object_id AND early.rn = 1
            LEFT JOIN (
                SELECT 
                    e2o.object_id,
                    e2o.eid,
                    ev.timestamp,
                    ROW_NUMBER() OVER (PARTITION BY e2o.object_id ORDER BY ev.timestamp DESC, e2o.eid DESC) as rn
                FROM _e2o_raw e2o
                LEFT JOIN _events_raw ev ON e2o.eid = ev.eid
            ) late ON base.object_id = late.object_id AND late.rn = 1
            WHERE base.rn = 1
        ''')
        self._log(f"    Created objects table with {con.execute('SELECT COUNT(*) FROM objects').fetchone()[0]} rows")

        # Build log_e2o without PK/FK constraints to avoid DuckDB instability on bulk insert.
        self._log("  Creating log_e2o table...")
        con.execute("DROP TABLE IF EXISTS log_e2o")
        con.execute(f'''
            CREATE TABLE log_e2o (
                {', '.join(e2o_defs)}
            )
        ''')
        con.execute('''
            INSERT INTO log_e2o
            SELECT DISTINCT *
            FROM _e2o_raw
            WHERE object_id IS NOT NULL
              AND eid IS NOT NULL
              AND object_type IS NOT NULL
        ''')
        con.execute('''
            DELETE FROM log_e2o
            WHERE object_id NOT IN (SELECT object_id FROM objects)
        ''')
        self._log(f"    Created log_e2o with {con.execute('SELECT COUNT(*) FROM log_e2o').fetchone()[0]} rows")
        
        # Create Object Tables
        self._log("  Creating per-type object tables...")
        for otype in self.object_types:
            con.execute(f"DROP TABLE IF EXISTS obj_{otype}")
            con.execute(f'''
                CREATE TABLE obj_{otype} (
                    object_id {object_id_type} NOT NULL,
                    earliest_event_id {event_id_type},
                    latest_event_id {event_id_type},
                    PRIMARY KEY (object_id),
                    FOREIGN KEY (earliest_event_id) REFERENCES log_events(eid),
                    FOREIGN KEY (latest_event_id) REFERENCES log_events(eid)
                )
            ''')
            con.execute(f'''
                INSERT INTO obj_{otype}
                SELECT 
                    object_id,
                    earliest_event_id,
                    latest_event_id
                FROM objects
                WHERE object_type = '{otype}'
            ''')
        
        # Derive O2O relationships
        self._derive_o2o_relationships(con)
        self._derive_o2o_activity(con)
        self._build_oro_role_tables(con)

    def _build_oro_role_tables(self, con):
        """Reified roles r_{o,e} and their order, read by the ORO mappings (schemata.py).

        ``pos`` ranks an object's roles by timestamp with ties sharing a rank, so nextRole and
        laterRole follow strictly increasing timestamps. laterRole is not materialized (it has
        up to n(n-1)/2 pairs per object); its mapping joins oro_roles on pos.
        """
        self._log("  Creating ORO role tables...")
        con.execute("DROP TABLE IF EXISTS oro_roles")
        con.execute('''
            CREATE TABLE oro_roles AS
            SELECT r.object_id, r.eid, r.otype, ev.activity,
                   DENSE_RANK() OVER (PARTITION BY r.object_id ORDER BY ev.timestamp) AS pos
            FROM (SELECT DISTINCT object_id, eid, object_type AS otype FROM log_e2o) r
            JOIN log_events ev ON ev.eid = r.eid
        ''')
        con.execute("DROP TABLE IF EXISTS oro_next_role")
        con.execute('''
            CREATE TABLE oro_next_role AS
            SELECT a.object_id, a.eid AS eid1, b.eid AS eid2
            FROM oro_roles a
            JOIN oro_roles b ON a.object_id = b.object_id AND b.pos = a.pos + 1
        ''')
        self.role_pairs = [tuple(r) for r in con.execute(
            "SELECT DISTINCT activity, otype FROM oro_roles ORDER BY otype, activity").fetchall()]
        self.activities = [r[0] for r in con.execute(
            "SELECT DISTINCT activity FROM log_events ORDER BY activity").fetchall()]
        self._log(f"    {con.execute('SELECT COUNT(*) FROM oro_roles').fetchone()[0]} roles, "
                  f"{con.execute('SELECT COUNT(*) FROM oro_next_role').fetchone()[0]} nextRole pairs")

    def _verify_duckdb_tables(self, con):
        """Verify that DuckDB tables have been created and contain data."""
        self._log("\n  Verifying DuckDB tables...")
        
        try:
            # Check log_events
            result = con.execute("SELECT COUNT(*) as count FROM log_events").fetchall()
            event_count = result[0][0] if result else 0
            self._log(f"    log_events: {event_count} rows")
            
            # Check log_e2o
            result = con.execute("SELECT COUNT(*) as count FROM log_e2o").fetchall()
            e2o_count = result[0][0] if result else 0
            self._log(f"    log_e2o: {e2o_count} rows")
            
            # Check log_o2o (OIG interactions)
            result = con.execute("SELECT COUNT(*) as count FROM log_o2o").fetchall()
            o2o_count = result[0][0] if result else 0
            self._log(f"    log_o2o (OIG interactions): {o2o_count} rows")
            
            # Check log_o2o_detailed (OIG interactions with event context)
            result = con.execute("SELECT COUNT(*) as count FROM log_o2o_detailed").fetchall()
            o2o_detailed_count = result[0][0] if result else 0
            self._log(f"    log_o2o_detailed (OIG + event context): {o2o_detailed_count} rows")
            
            # Check unique activities
            result = con.execute("SELECT COUNT(DISTINCT activity) as count FROM log_events").fetchall()
            activity_count = result[0][0] if result else 0
            self._log(f"    Unique activities: {activity_count}")
            
            # Check unique object types
            result = con.execute("SELECT COUNT(DISTINCT object_type) as count FROM log_e2o").fetchall()
            otype_count = result[0][0] if result else 0
            self._log(f"    Unique object types: {otype_count}")
            
            if event_count == 0 or e2o_count == 0:
                self._log("    ⚠ WARNING: Tables appear to be empty!")
                
        except Exception as e:
            self._log(f"    ✗ Error verifying tables: {e}")

    def _derive_o2o_relationships(self, con):
        """Derives OIG object interactions by joining E2O on Event ID."""
        self._log("  Deriving OIG object interactions...")
        e2o_cols = con.execute("PRAGMA table_info('log_e2o')").fetchall()
        e2o_type_map = {col_name: col_type for _, col_name, col_type, *_ in e2o_cols}
        object_id_type = e2o_type_map.get('object_id', 'VARCHAR')
        object_type_type = e2o_type_map.get('object_type', 'VARCHAR')
        eid_type = e2o_type_map.get('eid', 'VARCHAR')

        con.execute("DROP TABLE IF EXISTS log_o2o")
        con.execute('''
            CREATE TABLE log_o2o (
                oid1 {} NOT NULL,
                otype1 {} NOT NULL,
                oid2 {} NOT NULL,
                otype2 {} NOT NULL,
                event_id {} NOT NULL
            )
        '''.format(object_id_type, object_type_type, object_id_type, object_type_type, eid_type))
        con.execute('''
            INSERT INTO log_o2o
            SELECT DISTINCT
                e2o1.object_id AS oid1,
                e2o1.object_type AS otype1,
                e2o2.object_id AS oid2,
                e2o2.object_type AS otype2,
                e2o1.eid AS event_id
            FROM log_e2o e2o1
            JOIN log_e2o e2o2
                ON e2o1.eid = e2o2.eid
                AND e2o1.object_id <> e2o2.object_id
        ''')
        self._log(f"    Created log_o2o with {con.execute('SELECT COUNT(*) FROM log_o2o').fetchone()[0]} rows")

    def _derive_o2o_activity(self, con):
        """Attach event activity and timestamp to OIG object interactions to avoid heavy SPARQL joins."""
        self._log("  Deriving detailed OIG object interactions with activities...")
        o2o_cols = con.execute("PRAGMA table_info('log_o2o')").fetchall()
        o2o_type_map = {col_name: col_type for _, col_name, col_type, *_ in o2o_cols}
        events_cols = con.execute("PRAGMA table_info('log_events')").fetchall()
        events_type_map = {col_name: col_type for _, col_name, col_type, *_ in events_cols}

        oid1_type = o2o_type_map.get('oid1', 'VARCHAR')
        otype1_type = o2o_type_map.get('otype1', 'VARCHAR')
        oid2_type = o2o_type_map.get('oid2', 'VARCHAR')
        otype2_type = o2o_type_map.get('otype2', 'VARCHAR')
        event_id_type = o2o_type_map.get('event_id', 'VARCHAR')
        activity_type = events_type_map.get('activity', 'VARCHAR')
        timestamp_type = events_type_map.get('timestamp', 'TIMESTAMP')

        con.execute("DROP TABLE IF EXISTS log_o2o_detailed")
        con.execute('''
            CREATE TABLE log_o2o_detailed (
                oid1 {} NOT NULL,
                otype1 {} NOT NULL,
                oid2 {} NOT NULL,
                otype2 {} NOT NULL,
                event_id {} NOT NULL,
                activity {},
                timestamp {}
            )
        '''.format(oid1_type, otype1_type, oid2_type, otype2_type, event_id_type, activity_type, timestamp_type))
        con.execute('''
            INSERT INTO log_o2o_detailed
            SELECT
                o2o.oid1,
                o2o.otype1,
                o2o.oid2,
                o2o.otype2,
                o2o.event_id,
                e.activity,
                e.timestamp
            FROM log_o2o o2o
            JOIN log_events e
                ON o2o.event_id = e.eid
        ''')
        self._log(f"    Created log_o2o_detailed with {con.execute('SELECT COUNT(*) FROM log_o2o_detailed').fetchone()[0]} rows")
