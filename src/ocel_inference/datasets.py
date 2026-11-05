"""The OCEL 2.0 logs used in the paper, loadable by short name."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

DATA_DIR = "input/data"
OUTPUT_DIR = "output"


@dataclass(frozen=True)
class Dataset:
    name: str       # display name used in the paper's tables
    file_name: str  # OCEL 2.0 JSON file in DATA_DIR
    folder: str     # sub-folder of DATA_DIR (DuckDB file) and of OUTPUT_DIR (graphs, ORO)


DATASETS: Dict[str, Dataset] = {
    "order_mgmt": Dataset("Order Management", "order-management.json", "order_mgmt"),
    "logistics": Dataset("Container Logistics", "ContainerLogistics.json", "logistics"),
    "p2p": Dataset("Procure-to-Pay", "ocel2-p2p.json", "p2p"),
    "bpi17": Dataset("BPI 2017", "bpi17_ocel.json", "bpi17"),
}


def path(key: str, data_dir: str = DATA_DIR) -> Path:
    """Path of a log's OCEL 2.0 JSON file."""
    return Path(data_dir) / DATASETS[key].file_name


def load(key: str, data_dir: str = DATA_DIR, output_dir: str = OUTPUT_DIR,
         visualize: bool = False, verbose: bool = False):
    """Run inference on one paper log and wait until Ontop serves it.

    Returns the OCELSchemaInferrer, ready for SPARQL queries and schema checks. Its DuckDB file
    is kept in ``<data_dir>/<log>/`` (read by Ontop); with ``visualize`` the OIG and ORO graphs
    are written to ``<output_dir>/<log>/``, where export_ttl() also writes the ORO.
    """
    from .inferrer import OCELSchemaInferrer

    if key not in DATASETS:
        raise KeyError(f"Unknown dataset '{key}'. Available: {', '.join(DATASETS)}")
    folder = DATASETS[key].folder
    inferrer = OCELSchemaInferrer(str(path(key, data_dir)), db_dir=str(Path(data_dir) / folder),
                                  output_dir=str(Path(output_dir) / folder), verbose=verbose)
    inferrer.run_inference(keep_db=True, update_oro_artifacts=True)
    if visualize:
        # Before serving: once Ontop queries the log it holds the DuckDB file lock.
        inferrer.visualize()
    inferrer.serve()
    return inferrer
