FROM ontop/ontop:latest

# Copy the DuckDB JDBC driver into the image
COPY ./jdbc/duckdb_jdbc-1.4.2.0.jar /opt/ontop/jdbc/duckdb_jdbc-1.4.2.0.jar

WORKDIR /opt/ontop