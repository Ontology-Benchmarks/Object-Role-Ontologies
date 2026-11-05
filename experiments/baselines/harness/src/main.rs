//! Timing harness for the OC-DECLARE and OCPQ baselines of the ORO paper.
//!
//! `ocdeclare`: end-to-end discovery (OCEL 2.0 JSON import, linking, discovery) in one process,
//! so a driver can run it once per repetition and read a per-run peak RSS.
//!
//! `ocpq`: loads the log once, then times each query's evaluation on the loaded log, the same way
//! OCPQ's own `evaluate_box_tree(.., measure_performance = true)` does. The violated situations of
//! the root node are written out so they can be compared with the ORO counterexamples.
ocpq_core::use_mimalloc!();

use std::{fs, io::Write, path::PathBuf, time::Instant};

use clap::{Parser, Subcommand};
use ocpq_core::{
    binding_box::{BindingBoxTree, EvaluationResultWithCount},
    process_mining::{
        core::event_data::object_centric::linked_ocel::{LinkedOCELAccess, SlimLinkedOCEL},
        discovery::object_centric::oc_declare::{
            discover_behavior_constraints, OCDeclareDiscoveryOptions,
        },
        Importable, OCEL,
    },
};
use serde_json::json;

#[derive(Parser)]
struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// One end-to-end OC-DECLARE discovery run with the library's default options.
    Ocdeclare {
        #[arg(long)]
        ocel: PathBuf,
        /// Where to write the discovered constraints (JSON).
        #[arg(long)]
        model_out: Option<PathBuf>,
    },
    /// Load the log once, then evaluate each query `warmup + runs` times.
    Ocpq {
        #[arg(long)]
        ocel: PathBuf,
        /// Directories containing an ocpq-tree.json each.
        #[arg(long, num_args = 1..)]
        queries: Vec<PathBuf>,
        #[arg(long, default_value_t = 4)]
        runs: usize,
        #[arg(long, default_value_t = 1)]
        warmup: usize,
        /// Directory for the violated root situations (<QID>.csv).
        #[arg(long)]
        dump_dir: Option<PathBuf>,
    },
}

/// Peak resident set size of this process in MB (VmHWM, i.e. the same quantity as ru_maxrss).
fn peak_rss_mb() -> f64 {
    let status = fs::read_to_string("/proc/self/status").unwrap_or_default();
    status
        .lines()
        .find_map(|l| l.strip_prefix("VmHWM:"))
        .and_then(|v| v.trim().trim_end_matches("kB").trim().parse::<f64>().ok())
        .map(|kb| kb / 1024.0)
        .unwrap_or(f64::NAN)
}

fn run_ocdeclare(ocel_path: PathBuf, model_out: Option<PathBuf>) {
    let start = Instant::now();
    let ocel = OCEL::import_from_path(&ocel_path).expect("import OCEL");
    let import_s = start.elapsed().as_secs_f64();

    let t = Instant::now();
    let locel = SlimLinkedOCEL::from_ocel(ocel);
    let link_s = t.elapsed().as_secs_f64();

    let options = OCDeclareDiscoveryOptions::default();
    let t = Instant::now();
    let arcs = discover_behavior_constraints(&locel, options.clone());
    let discovery_s = t.elapsed().as_secs_f64();
    let total_s = start.elapsed().as_secs_f64();

    if let Some(path) = model_out {
        fs::write(path, serde_json::to_string_pretty(&arcs).unwrap()).expect("write model");
    }
    let out = json!({
        "tool": "OC-DECLARE",
        "ocel": ocel_path,
        "options": options,
        "events": locel.get_all_evs().count(),
        "objects": locel.get_all_obs().count(),
        "constraints": arcs.len(),
        "import_s": import_s,
        "link_s": link_s,
        "discovery_s": discovery_s,
        "total_s": total_s,
        "peak_rss_mb": peak_rss_mb(),
    });
    println!("{out}");
}

fn std_dev(xs: &[f64]) -> f64 {
    if xs.len() < 2 {
        return f64::NAN;
    }
    let mean = xs.iter().sum::<f64>() / xs.len() as f64;
    // Sample s.d., as pandas' std() used for the ORO numbers.
    (xs.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / (xs.len() - 1) as f64).sqrt()
}

/// Mirrors the timed body of OCPQ's `evaluate_box_tree` benchmark path: evaluate the tree and
/// gather the flat results per node.
fn evaluate(tree: &BindingBoxTree, locel: &SlimLinkedOCEL) -> Vec<EvaluationResultWithCount> {
    let (flat, skipped) = tree.evaluate(locel).expect("evaluate tree");
    assert!(!skipped, "evaluation skipped bindings");
    let mut per_node: Vec<EvaluationResultWithCount> =
        tree.nodes.iter().map(|_| EvaluationResultWithCount::default()).collect();
    for (index, binding, viol) in flat {
        let r = &mut per_node[index];
        r.situations.push((std::sync::Arc::unwrap_or_clone(binding), viol.clone()));
        r.situation_count += 1;
        if viol.is_some() {
            r.situation_violated_count += 1;
        }
    }
    per_node
}

fn run_ocpq(ocel_path: PathBuf, queries: Vec<PathBuf>, runs: usize, warmup: usize, dump_dir: Option<PathBuf>) {
    let start = Instant::now();
    let locel = SlimLinkedOCEL::import_from_path(&ocel_path).expect("import OCEL");
    let load_s = start.elapsed().as_secs_f64();

    for qdir in queries {
        let qid = qdir.file_name().unwrap().to_string_lossy().to_string();
        let tree: BindingBoxTree =
            serde_json::from_str(&fs::read_to_string(qdir.join("ocpq-tree.json")).unwrap())
                .expect("parse tree");
        let mut warmup_seconds = Vec::with_capacity(warmup);
        for _ in 0..warmup {
            let t = Instant::now();
            std::hint::black_box(evaluate(&tree, &locel));
            warmup_seconds.push(t.elapsed().as_secs_f64());
        }
        let mut seconds = Vec::with_capacity(runs);
        let mut result = Vec::new();
        for _ in 0..runs {
            let t = Instant::now();
            result = std::hint::black_box(evaluate(&tree, &locel));
            seconds.push(t.elapsed().as_secs_f64());
        }
        let root = &result[0];
        if let Some(dir) = &dump_dir {
            fs::create_dir_all(dir).unwrap();
            let mut f = fs::File::create(dir.join(format!("{qid}.csv"))).unwrap();
            writeln!(f, "obj,event").unwrap();
            for (binding, viol) in &root.situations {
                if viol.is_none() {
                    continue;
                }
                let obj = binding.object_map.first().map(|(_, o)| locel.get_ob_id(o)).unwrap_or("");
                let ev = binding.event_map.first().map(|(_, e)| locel.get_ev_id(e)).unwrap_or("");
                writeln!(f, "{obj},{ev}").unwrap();
            }
        }
        let mean = seconds.iter().sum::<f64>() / seconds.len() as f64;
        let out = json!({
            "tool": "OCPQ",
            "qid": qid,
            "ocel": ocel_path,
            "load_s": load_s,
            "runs": runs,
            "warmup": warmup,
            "warmup_seconds": warmup_seconds,
            "seconds": seconds,
            "mean_s": mean,
            "std_s": std_dev(&seconds),
            "situations": root.situation_count,
            "violated": root.situation_violated_count,
            "peak_rss_mb": peak_rss_mb(),
        });
        println!("{out}");
    }
}

fn main() {
    match Args::parse().command {
        Command::Ocdeclare { ocel, model_out } => run_ocdeclare(ocel, model_out),
        Command::Ocpq { ocel, queries, runs, warmup, dump_dir } => {
            run_ocpq(ocel, queries, runs, warmup, dump_dir)
        }
    }
}
