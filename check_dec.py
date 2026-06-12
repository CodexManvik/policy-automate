from datetime import datetime, timezone, timedelta
from pipeline import ClaimsAdjudicationPipeline
from test_hardening import create_base_test_context

pipeline = ClaimsAdjudicationPipeline(use_ai=False)
context = create_base_test_context()
line_item = context.line_items[0]
plan = pipeline.planner.create_execution_plan(context, line_item)
print("Execution steps in plan:")
for step in plan.execution_steps:
    print(f"- {step.rule_id} ({step.gate}) [type: {step.execution_type}]")
