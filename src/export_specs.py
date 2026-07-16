"""
Standalone Schema Exposer Script.
Uses Pydantic V2 native .model_json_schema() to export JSON specifications
for the Core Plumbing Gateways to docs/api_specs/.
"""

import os
import json
import sys
from pathlib import Path

# Add src folder to sys.path to resolve imports when running from root or src
_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.append(str(_MODULE_DIR))

from schemas import (
    ClaimContext,
    LifetimeStateData,
    PerClaimState,
    ClaimDecision,
    ManualReviewExceptionPayload
)

def export_schemas():
    """Generates and writes JSON schemas for the core Pydantic models"""
    output_dir = _MODULE_DIR.parent / "docs" / "api_specs"
    os.makedirs(output_dir, exist_ok=True)
    print(f"Target specs directory initialized: {output_dir.resolve()}")

    models_to_export = {
        "claim_context": ClaimContext,
        "lifetime_state": LifetimeStateData,
        "per_claim_state": PerClaimState,
        "claim_decision": ClaimDecision,
        "manual_review_exception": ManualReviewExceptionPayload
    }

    for filename, model_class in models_to_export.items():
        try:
            print(f"Generating JSON Schema for: {model_class.__name__}...")
            # Pydantic V2 native schema generation method
            schema = model_class.model_json_schema()
            
            output_filepath = output_dir / f"{filename}.json"
            with open(output_filepath, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2, default=str)
                
            print(f"  [SUCCESS] Written to: {output_filepath.name}")
        except Exception as e:
            print(f"  [ERROR] Failed to generate/write schema for {model_class.__name__}: {e}", file=sys.stderr)
            raise e

if __name__ == "__main__":
    try:
        export_schemas()
        print("All OpenAPI/JSON specifications generated successfully.")
    except Exception as exc:
        sys.exit(1)
