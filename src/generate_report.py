"""
CLI Analytics Report Generator wrapper script.
Executes PipelineMetricsEngine to render the ASCII dashboard.
"""

import sys
from metrics import generate_analytics_report

def main():
    filepath = "metrics_telemetry.jsonl"
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    
    try:
        report = generate_analytics_report(filepath)
        print(report)
    except Exception as e:
        print(f"Error compiling metrics report: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
