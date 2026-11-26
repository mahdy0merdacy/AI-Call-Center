"""Run the data-quality checks on the lake.

    S3_SECRET_KEY=... .venv-quality/bin/python data/quality/run.py silver_calls silver_turns
    ... run.py --since 2026-11-23 bronze bronze_batch_stats      # one day, as Airflow does
    ... run.py                                                    # every table

Exit code: 1 if any CRITICAL suite failed (Airflow then stops before building
the next layer), 0 otherwise. Warnings are printed and land in the report.

Report: Great Expectations Data Docs, a static HTML site, at
data/quality/gx/uncommitted/data_docs/local_site/index.html: every run, every
rule, and for each failure the offending values.

The rules live in suites.py as code. The gx/ folder is generated from them on
each run and is not committed: there is one source of truth for a rule, and it
is reviewed like any other code.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import great_expectations as gx
import lake
from great_expectations.checkpoint import UpdateDataDocsAction
from suites import SUITES

HERE = Path(__file__).resolve().parent
# Where GX keeps its generated project and the Data Docs site. Writable; the
# code folder itself is mounted read-only in Airflow.
GX_HOME = Path(os.environ.get("QUALITY_HOME") or HERE)

LOADERS = {
    "bronze": lake.bronze,
    "bronze_batch_stats": lake.bronze_batch_stats,
    "silver_call_events": lake.silver_call_events,
    "silver_calls": lake.silver_calls,
    "silver_turns": lake.silver_turns,
    "silver_utterance_emotions": lake.silver_utterance_emotions,
    "silver_call_sentiment": lake.silver_call_sentiment,
    "silver_knowledge_searches": lake.silver_knowledge_searches,
    "gold_tenant_daily": lake.gold_tenant_daily,
    "gold_sentiment_daily": lake.gold_sentiment_daily,
    "gold_call_sentiment_daily": lake.gold_call_sentiment_daily,
    "gold_tool_daily": lake.gold_tool_daily,
    "gold_knowledge_daily": lake.gold_knowledge_daily,
    "gold_document_usage": lake.gold_document_usage,
    "gold_knowledge_gaps": lake.gold_knowledge_gaps,
    "gold_hourly": lake.gold_hourly,
    "gold_calls_to_review": lake.gold_calls_to_review,
}


def _asset(ds, name: str):
    try:
        return ds.get_asset(name)
    except LookupError:
        return ds.add_dataframe_asset(name=name)


def _batch_definition(asset, name: str):
    try:
        return asset.get_batch_definition(name)
    except KeyError:
        return asset.add_batch_definition_whole_dataframe(name)


def check(context, table: str, df, docs: bool = True) -> dict:
    """Validate one table against its critical and warning suites.

    docs=False skips the Data Docs build (tests use an in-memory context).
    """
    ds = context.data_sources.add_or_update_pandas(name="lake")
    batch = _batch_definition(_asset(ds, table), table)

    definitions = []
    for severity, expectations in SUITES[table]().items():
        suite = context.suites.add_or_update(
            gx.ExpectationSuite(name=f"{table}.{severity}", expectations=list(expectations)))
        definitions.append(context.validation_definitions.add_or_update(
            gx.ValidationDefinition(name=f"{table}.{severity}", data=batch, suite=suite)))

    checkpoint = context.checkpoints.add_or_update(gx.Checkpoint(
        name=table, validation_definitions=definitions,
        actions=[UpdateDataDocsAction(name="data_docs")] if docs else [],
        result_format={"result_format": "SUMMARY", "partial_unexpected_count": 5}))
    result = checkpoint.run(batch_parameters={"dataframe": df})

    out = {"table": table, "rows": len(df), "suites": {}}
    for vr in result.run_results.values():
        severity = vr.suite_name.rsplit(".", 1)[-1]
        failed = []
        for r in vr.results:
            if r.success:
                continue
            cfg = r.expectation_config
            failed.append({
                "rule": cfg.type,
                "column": (cfg.kwargs.get("column") or cfg.kwargs.get("column_list")
                           or f"{cfg.kwargs.get('column_A')} vs {cfg.kwargs.get('column_B')}"),
                "unexpected": (r.result or {}).get("unexpected_count"),
                "examples": (r.result or {}).get("partial_unexpected_list", [])[:3],
            })
        out["suites"][severity] = {"success": vr.success,
                                   "rules": len(vr.results), "failed": failed}
    return out


def _print(report: dict) -> None:
    print(f"\n{report['table']}  ({report['rows']} rows)")
    for severity in ("critical", "warning"):
        s = report["suites"].get(severity)
        if not s:
            continue
        mark = "PASS" if s["success"] else ("FAIL" if severity == "critical" else "WARN")
        print(f"  {severity:<8} {mark}  {s['rules'] - len(s['failed'])}/{s['rules']} rules")
        for f in s["failed"]:
            examples = ", ".join(map(str, f["examples"]))
            print(f"    x {f['rule']} on {f['column']}: {f['unexpected']} rows"
                  f"{f'  e.g. {examples}' if examples else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tables", nargs="*", metavar="TABLE",
                    help=f"any of: {', '.join(LOADERS)} (default: all)")
    ap.add_argument("--since", help="only partitions on or after this date (YYYY-MM-DD)")
    ap.add_argument("--json", type=Path, help="also write the summary here")
    args = ap.parse_args()
    if unknown := set(args.tables) - set(LOADERS):
        ap.error(f"unknown table(s): {', '.join(sorted(unknown))}")
    logging.getLogger("great_expectations").setLevel(logging.ERROR)

    context = gx.get_context(mode="file", project_root_dir=str(GX_HOME))
    reports = []
    for table in args.tables or LOADERS:
        reports.append(check(context, table, LOADERS[table](since=args.since)))
        _print(reports[-1])

    if args.json:
        args.json.write_text(json.dumps(reports, indent=2, default=str))
    docs = GX_HOME / "gx" / "uncommitted" / "data_docs" / "local_site" / "index.html"
    print(f"\nreport: {docs}")
    critical_failed = [r["table"] for r in reports
                       if not r["suites"].get("critical", {}).get("success", True)]
    if critical_failed:
        print(f"CRITICAL checks failed on: {', '.join(critical_failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
