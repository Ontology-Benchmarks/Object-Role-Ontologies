"""Loading OCEL 2.0 logs: any JSON file by path, or the paper's four logs by short name."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

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


def resolve(log: str, data_dir: str = DATA_DIR) -> Tuple[Path, str]:
    """The JSON file of a log (a path or a paper log's short name) and its folder name.

    A path to one of the paper's files gets that log's folder (e.g. bpi17), so its outputs are the
    same however it is loaded; any other file gets its stem.
    """
    if log in DATASETS:
        return path(log, data_dir), DATASETS[log].folder
    json_path = Path(log)
    if not json_path.is_file():
        raise FileNotFoundError(
            f"'{log}' is neither an OCEL 2.0 JSON file nor a paper log ({', '.join(DATASETS)}).")
    for key, dataset in DATASETS.items():
        known = path(key, data_dir)
        if known.is_file() and known.resolve() == json_path.resolve():
            return json_path, dataset.folder
    return json_path, json_path.stem


def load(log: str, data_dir: str = DATA_DIR, output_dir: str = OUTPUT_DIR,
         visualize: bool = False, verbose: bool = False):
    """Run inference on an OCEL 2.0 log and wait until Ontop serves it.

    ``log`` is the path of an OCEL 2.0 JSON file (e.g. "input/data/my_log.json"), or the short
    name of one of the paper's logs (order_mgmt, logistics, p2p, bpi17). Returns the
    OCELSchemaInferrer, ready for SPARQL queries and schema checks.

    Files are kept per log, in a folder named after the log (its short name, or the JSON file's
    stem): the DuckDB file in ``<data_dir>/<folder>/`` (read by Ontop, so it must be under
    ``input/``, the folder the Ontop container mounts), and with ``visualize`` the OIG and ORO
    graphs in ``<output_dir>/<folder>/``, where export_ttl() also writes the ORO.
    """
    from .inferrer import OCELSchemaInferrer

    json_path, folder = resolve(log, data_dir)
    inferrer = OCELSchemaInferrer(str(json_path), db_dir=str(Path(data_dir) / folder),
                                  output_dir=str(Path(output_dir) / folder), verbose=verbose)
    inferrer.run_inference(keep_db=True, update_oro_artifacts=True)
    if visualize:
        # Before serving: once Ontop queries the log it holds the DuckDB file lock.
        inferrer.visualize()
    inferrer.serve()
    return inferrer
