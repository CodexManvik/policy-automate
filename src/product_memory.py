"""
Product Memory Layer - Phase 2
Parses and stores rule blueprints from Product JSON
Provides structured metadata for AI Planner
"""

from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import json
import re

# Resolve docs directory relative to this source file so the module works
# regardless of the working directory from which Python is invoked.
_MODULE_DIR = Path(__file__).parent          # …/src/
_DOCS_DIR   = _MODULE_DIR.parent / "docs"   # …/project_root/docs/
_DEFAULT_EXTRACTION_FILE = _DOCS_DIR / "Product and Policy Rules Extraction.txt"


class RuleGate(str, Enum):
    """Gate classification for rules"""
    POLICY_VALIDATION = "policy_validation"
    MEMBER_VALIDATION = "member_validation"
    COVERAGE_VALIDATION = "coverage_validation"
    WAITING_PERIOD_VALIDATION = "waiting_period_validation"
    EXCLUSION_VALIDATION = "exclusion_validation"
    FINANCIAL_COMPUTATION = "financial_computation"
    STATE_UPDATE = "state_update"


class ExecutionType(str, Enum):
    """Rule execution patterns"""
    DETERMINISTIC = "deterministic"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass
class RuleBlueprint:
    """
    Structured representation of a single policy rule
    Extracted from Product JSON
    """
    rule_id: str
    rule_name: str
    gate: RuleGate
    section_ref: str
    benefit_category: str  # mandatory, optional, service, wellness
    execution_type: ExecutionType
    priority: int  # Lower = earlier execution
    
    # Dependency management
    depends_on: List[str]  # Rule IDs that must execute before this
    
    # Applicability filters
    variant_applicability: List[str]  # ["Classic", "Select", "Elite"]
    benefit_bucket_filter: Optional[List[str]] = None
    applicable_buckets: Optional[List[str]] = None
    
    # Preconditions (programmatic checks)
    preconditions: List[str] = None  # Expressions like "hospitalization_hours >= 24"
    
    # Tool mapping (for deterministic rules)
    tool_required: Optional[str] = None
    
    # Semantic reasoning (for AI-driven rules)
    semantic_prompt_template: Optional[str] = None
    
    # Formula or logic description
    formula: Optional[str] = None
    
    # Metadata
    source_page: Optional[int] = None
    auto_adjudicable: bool = True
    confidence_weight: float = 1.0
    
    # Additional context
    notes: List[str] = None
    exclusions_within_benefit: List[str] = None
    not_applicable_to: Optional[List[str]] = None
    
    def __post_init__(self):
        if self.preconditions is None:
            self.preconditions = []
        if self.notes is None:
            self.notes = []
        if self.exclusions_within_benefit is None:
            self.exclusions_within_benefit = []
        if self.not_applicable_to is None:
            self.not_applicable_to = []
        if self.applicable_buckets is None:
            self.applicable_buckets = self.benefit_bucket_filter
        if self.benefit_bucket_filter is None:
            self.benefit_bucket_filter = self.applicable_buckets


class ProductMemoryStore:
    """
    Product Memory Store - Single Source of Truth
    Loads and indexes rule blueprints from Product JSON
    """
    
    def __init__(self, product_json_path: Optional[str] = None):
        self.product_id = "R3"
        self.product_name = "ReAssure 3.0"
        self.version = "v2.1_2025-01-15"

        self.rules: Dict[str, RuleBlueprint] = {}
        self.rules_by_gate: Dict[RuleGate, List[RuleBlueprint]] = {gate: [] for gate in RuleGate}
        self.rules_by_priority: List[RuleBlueprint] = []

        # Mutual Exclusivity Constraints - loaded from extraction file
        self.mutual_exclusivity_constraints: List[Dict[str, Any]] = []
        self.mutual_exclusivity_by_id: Dict[str, Dict[str, Any]] = {}

        # Tables - loaded from extraction file
        self.tables: Dict[str, Dict[str, Any]] = {}
        
        # Ingest rules from standard extraction file or fallback to embedded
        import os
        loaded = False
        
        # Determine path to rules extraction file
        # Determine path to rules extraction file — primary is __file__-relative
        # (portable across any working directory); CWD-relative paths are kept as
        # secondary fallbacks for backward compatibility.
        if product_json_path:
            paths_to_try = [product_json_path]
        else:
            paths_to_try = [
                str(_DEFAULT_EXTRACTION_FILE),                                       # portable (primary)
                "docs/Product and Policy Rules Extraction.txt",                      # CWD-relative (legacy)
                "../docs/Product and Policy Rules Extraction.txt",                   # one level up (legacy)
                "C:\\Project\\nivabupa\\policy automate\\docs\\Product and Policy Rules Extraction.txt"  # absolute (last resort)
            ]
            
        for path in paths_to_try:
            if path and os.path.exists(path):
                try:
                    self.load_rules_from_extraction_file(path)
                    print(f"[OK] Successfully ingested rule blueprints from: {path}")
                    loaded = True
                    break
                except Exception as e:
                    print(f"[WARN] Failed to load rules from {path}: {e}")

        if not loaded:
            raise FileNotFoundError("Could not load rule blueprints from extraction file. Fallbacks are disabled.")
            
    def load_rules_from_extraction_file(self, file_path: str):
        """
        Ingest structural rule blueprints from 'Product and Policy Rules Extraction.txt'
        Indexes rules by rule_id, associates them with RuleGate and priority,
        and sets up depends_on relationships dynamically from JSON.
        Also loads mutual exclusivity constraints.
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Load tables
        self.tables = {t['table_id']: t for t in data.get('tables', [])}
        print(f"[OK] Loaded {len(self.tables)} tables from Product JSON")

        # Load mutual exclusivity constraints
        mx_constraints = data.get('mutual_exclusivity_constraints', [])
        self.mutual_exclusivity_constraints = mx_constraints
        for mx in mx_constraints:
            mx_id = mx.get('constraint_id')
            if mx_id:
                self.mutual_exclusivity_by_id[mx_id] = mx
        print(f"[OK] Loaded {len(mx_constraints)} mutual exclusivity constraints")

        rule_blueprints = data.get('rule_blueprints', [])
        loaded_rule_ids: Set[str] = set()

        for r_data in rule_blueprints:
            rule_id = r_data['rule_id']
            rule_name = r_data['name']
            loaded_rule_ids.add(rule_id)

            # Map Gate, Priority, and ExecutionType dynamically
            gate, priority, exec_type = self._determine_gate_priority_exectype(rule_id, rule_name)

            # Read depends_on directly from JSON (overwritten by dynamic assignment)
            depends_on = r_data.get('depends_on', [])

            # Parse other metadata
            variant_applicability = r_data.get('variant_applicability', ["Classic", "Select", "Elite"])
            benefit_category = r_data.get('benefit_category', 'mandatory')
            section_ref = r_data.get('section_ref', '')
            formula = r_data.get('formula', '')
            preconditions = r_data.get('preconditions', [])
            not_applicable_to = r_data.get('not_applicable_to', [])
            
            # Set semantic prompt template for semantic rules if they don't have one
            semantic_prompt_template = None
            if exec_type == ExecutionType.SEMANTIC or exec_type == ExecutionType.HYBRID:
                embedded_templates = {
                    "R3_EXCL_007": """Analyze if this treatment is cosmetic/plastic surgery:
Treatment: {treatment_description}
Diagnosis: {diagnosis}
Doctor Notes: {doctor_notes}

EXCLUDE if cosmetic.
ALLOW if: reconstruction after Accident/Burns/Cancer OR medically necessary to remove immediate health risk (certified by physician).

Return: {"is_cosmetic": bool, "reason": str, "confidence": float}""",
                    "R3_EXCL_004": """Assess if admission was primarily for diagnostics only:
Admission Reason: {admission_reason}
Procedures Performed: {procedures}
Treatment Given: {treatment_description}
Discharge Summary: {discharge_summary}

EXCLUDE if: admission solely for tests (MRI, CT, Endoscopy, Colonoscopy) with no treatment.
ALLOW if: tests were part of active treatment protocol.

Return: {"investigation_only": bool, "reason": str, "confidence": float}""",
                    "R3_EXCL_016": """Check if claim involves maternity:
Diagnosis: {diagnosis}
Procedures: {procedures}
ICD Codes: {icd_codes}

EXCLUDE: Childbirth (normal/complicated/caesarean), Miscarriage (except due to Accident), Lawful medical termination.
ALLOW: Ectopic pregnancy.

Return: {"is_maternity": bool, "is_ectopic": bool, "reason": str, "confidence": float}""",
                    "R3_EXCL_002": "Analyze if condition '{condition}' matches any specified disease in list: {disease_list}",
                    "R3_EXCL_001": "Assess if condition '{condition}' could be pre-existing based on: {medical_history}"
                }
                semantic_prompt_template = r_data.get('semantic_prompt_template') or embedded_templates.get(rule_id)
                if not semantic_prompt_template:
                    semantic_prompt_template = f"Verify coverage and exclusion terms for {rule_name} (ID: {rule_id}) given condition '{{condition}}' and treatment '{{treatment_description}}'."
            
            # Map tools
            tool_required = r_data.get('tool_required')
            if not tool_required:
                if rule_id in ["R3_EXCL_003", "R3_EXCL_002", "R3_EXCL_001"]:
                    tool_required = "calculate_waiting_period"
                elif rule_id == "R3_BEN_004":
                    tool_required = "calculate_room_pro_rata"
                elif rule_id == "R3_FIN_001":
                    tool_required = "calculate_deductible"
                elif rule_id == "R3_FIN_002":
                    tool_required = "calculate_copayment"
                elif rule_id == "R3_SUM_001":
                    tool_required = "calculate_si_waterfall"
                elif rule_id == "R3_SUM_003":
                    tool_required = "calculate_lock_the_clock"
                elif rule_id == "R3_SUM_004":
                    tool_required = "calculate_booster_accumulation"
                elif rule_id == "R3_BEN_006":
                    tool_required = "validate_pre_post_hosp_window"
                
            # Parse or map benefit bucket filter
            applicable_buckets = r_data.get('applicable_buckets') or r_data.get('benefit_bucket_filter')
            if not applicable_buckets and gate == RuleGate.COVERAGE_VALIDATION:
                rule_id_upper = rule_id.upper()
                if rule_id_upper in ["R3_BEN_001", "R3_BEN_002", "R3_BEN_036A"]:
                    applicable_buckets = ["Expenses in reaching a Hospital"]
                elif rule_id_upper in ["R3_BEN_003", "R3_BEN_005", "R3_BEN_005A", "R3_BEN_013"]:
                    applicable_buckets = ["Expenses during Hospitalization"]
                elif rule_id_upper in ["R3_BEN_006", "R3_BEN_031", "R3_BEN_031A"]:
                    applicable_buckets = ["Expenses before and after hospitalization"]
                elif rule_id_upper == "R3_BEN_007":
                    applicable_buckets = ["Home Care / Domiciliary Treatment"]
                elif rule_id_upper == "R3_BEN_008":
                    applicable_buckets = ["Organ Donor"]
                elif rule_id_upper.startswith("R3_BEN_"):
                    applicable_buckets = [rule_name]
            
            benefit_bucket_filter = applicable_buckets

            rule = RuleBlueprint(
                rule_id=rule_id,
                rule_name=rule_name,
                gate=gate,
                section_ref=section_ref,
                benefit_category=benefit_category,
                execution_type=exec_type,
                priority=priority,
                depends_on=depends_on,
                variant_applicability=variant_applicability,
                tool_required=tool_required,
                formula=formula,
                preconditions=preconditions,
                semantic_prompt_template=semantic_prompt_template,
                benefit_bucket_filter=benefit_bucket_filter,
                applicable_buckets=applicable_buckets,
                not_applicable_to=not_applicable_to
            )

            self.add_rule(rule)

        # Dynamically assign depends_on arrays based on gate-sequence and calculator constraints
        self._assign_dependencies_dynamically()

        # Validate all dependencies point to existing rules
        missing_deps = []
        for rule_id, rule in self.rules.items():
            for dep_id in rule.depends_on:
                if dep_id not in self.rules:
                    missing_deps.append((rule_id, dep_id))
                    print(f"[WARN] Rule {rule_id} depends on {dep_id}, which was not loaded")

        if missing_deps:
            print(f"[WARN] {len(missing_deps)} missing dependency references detected - DAG may have issues")
        else:
            print(f"[OK] All {len(self.rules)} rules have valid dependencies")

    def _determine_gate_priority_exectype(self, rule_id: str, rule_name: str) -> Tuple[RuleGate, int, ExecutionType]:
        """Map raw rule properties to RuleGate, priority, and ExecutionType"""
        gate = RuleGate.COVERAGE_VALIDATION
        priority = 100
        exec_type = ExecutionType.DETERMINISTIC
        
        rule_id_upper = rule_id.upper()
        
        # 1. Waiting Periods
        if rule_id_upper in ["R3_EXCL_001", "R3_EXCL_002", "R3_EXCL_003"] or rule_id_upper.startswith("R3_WT"):
            gate = RuleGate.WAITING_PERIOD_VALIDATION
            if rule_id_upper == "R3_EXCL_003":
                priority = 10
                exec_type = ExecutionType.DETERMINISTIC
            elif rule_id_upper == "R3_EXCL_002":
                priority = 20
                exec_type = ExecutionType.HYBRID
            elif rule_id_upper == "R3_EXCL_001":
                priority = 30
                exec_type = ExecutionType.HYBRID
            else:
                priority = 35
                exec_type = ExecutionType.DETERMINISTIC
                
        # 2. Exclusions
        elif rule_id_upper.startswith("R3_EXCL"):
            gate = RuleGate.EXCLUSION_VALIDATION
            if rule_id_upper in ["R3_EXCL_010", "R3_EXCL_020", "R3_EXCL_021"]:
                exec_type = ExecutionType.DETERMINISTIC
                priority = 38
            else:
                exec_type = ExecutionType.SEMANTIC
                if rule_id_upper == "R3_EXCL_004":
                    priority = 40
                elif rule_id_upper == "R3_EXCL_016":
                    priority = 45
                elif rule_id_upper == "R3_EXCL_007":
                    priority = 50
                else:
                    priority = 60
                
        # 3. Policy & Member gates
        elif "policy" in rule_name.lower():
            gate = RuleGate.POLICY_VALIDATION
            priority = 5
        elif "member" in rule_name.lower():
            gate = RuleGate.MEMBER_VALIDATION
            priority = 15
            
        # 4. Coverage Validation
        elif rule_id_upper == "R3_BEN_003":
            gate = RuleGate.COVERAGE_VALIDATION
            priority = 30
            exec_type = ExecutionType.HYBRID
        elif rule_id_upper.startswith("R3_BEN") and rule_id_upper not in ["R3_BEN_004", "R3_BEN_016", "R3_BEN_017"]:
            gate = RuleGate.COVERAGE_VALIDATION
            priority = 70
            if any(w in rule_name.lower() for w in ["opinion", "consultation", "wellness", "counselling", "app", "surprise"]):
                exec_type = ExecutionType.SEMANTIC
                
        # 5. Financial Computation
        elif rule_id_upper in ["R3_BEN_004", "R3_BEN_016", "R3_BEN_017", "R3_GEN_002"] or rule_id_upper.startswith("R3_FIN") or rule_id_upper.startswith("R3_SUM"):
            gate = RuleGate.FINANCIAL_COMPUTATION
            exec_type = ExecutionType.DETERMINISTIC
            if rule_id_upper == "R3_BEN_004":
                priority = 100
            elif rule_id_upper == "R3_GEN_002":
                priority = 110
            elif rule_id_upper == "R3_BEN_016":
                priority = 115
            elif rule_id_upper == "R3_BEN_017":
                priority = 116
            elif rule_id_upper == "R3_FIN_001":
                priority = 120
            elif rule_id_upper == "R3_FIN_002":
                priority = 130
            elif rule_id_upper == "R3_SUM_001":
                priority = 140
            else:
                priority = 150
                
        # 6. State Update / Wellness
        elif rule_id_upper.startswith("R3_LH"):
            gate = RuleGate.STATE_UPDATE
            priority = 200
            exec_type = ExecutionType.DETERMINISTIC
            
        return gate, priority, exec_type

    def load_from_json(self, json_path: str):
        """Load rule blueprints from Product JSON file"""
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for rule_data in data.get('rule_blueprints', []):
            rule = self._parse_rule_blueprint(rule_data)
            self.add_rule(rule)
    
    def _parse_rule_blueprint(self, data: Dict[str, Any]) -> RuleBlueprint:
        """Parse a single rule from JSON"""
        return RuleBlueprint(
            rule_id=data['rule_id'],
            rule_name=data['name'],
            gate=RuleGate(data.get('gate', 'financial_computation')),
            section_ref=data['section_ref'],
            benefit_category=data['benefit_category'],
            execution_type=ExecutionType(data.get('execution_type', 'deterministic')),
            priority=data.get('execution_priority', 50),
            depends_on=data.get('depends_on', []),
            variant_applicability=data.get('variant_applicability', ['Classic', 'Select', 'Elite']),
            tool_required=data.get('tool_required'),
            formula=data.get('formula'),
            source_page=data.get('source_page'),
            preconditions=data.get('preconditions', []),
            notes=data.get('notes', []),
            not_applicable_to=data.get('not_applicable_to', [])
        )
    
    def add_rule(self, rule: RuleBlueprint):
        """Add a rule to the memory store"""
        self.rules[rule.rule_id] = rule
        self.rules_by_gate[rule.gate].append(rule)
        self.rules_by_priority.append(rule)
        
        # Re-sort by priority
        self.rules_by_priority.sort(key=lambda r: r.priority)
    
    def get_rule(self, rule_id: str) -> Optional[RuleBlueprint]:
        """Retrieve a specific rule"""
        return self.rules.get(rule_id)
    
    def get_rules_for_gate(self, gate: RuleGate) -> List[RuleBlueprint]:
        """Get all rules for a specific gate"""
        return self.rules_by_gate.get(gate, [])
    
    def filter_rules(
        self,
        gate: Optional[RuleGate] = None,
        variant: Optional[str] = None,
        benefit_bucket: Optional[str] = None
    ) -> List[RuleBlueprint]:
        """Filter rules by various criteria"""
        rules = list(self.rules.values())
        
        if gate:
            rules = [r for r in rules if r.gate == gate]
        
        if variant:
            rules = [r for r in rules if variant in r.variant_applicability]
        
        if benefit_bucket:
            rules = [r for r in rules if 
                    r.benefit_bucket_filter is None or 
                    benefit_bucket in r.benefit_bucket_filter]
        
        return rules

    def get_room_copay_percent(
        self,
        variant: str,
        room_category_claimed: str
    ) -> float:
        """
        Look up room co-payment percentage from R3_TBL_005 (Annexure V).
        Returns 0.0 if no match found (no co-pay applies).

        The table encodes the canonical Annexure V schedule (policy page 62).
        Key: (variant, room_category_keyword) → copay fraction (0.0–1.0).
        Matching: lowercase substring, longest-match wins to avoid false positives
        (e.g. 'private' matching inside 'semi-private').

        Source: R3_TBL_005 in rules extraction JSON.
        """
        # Try dynamic lookup first
        tbl = self.tables.get("R3_TBL_005")
        if tbl:
            rows = tbl.get("rows", [])
            room_lower = (room_category_claimed or "").lower()
            best_match = 0.0
            best_len = -1
            
            for row in rows:
                row_variant = row.get("variant")
                if row_variant != variant:
                    continue
                
                keyword = row.get("room_category", "").lower()
                percent = float(row.get("copay_percent", 0.0))
                # Normalize copay percent to fraction if it was loaded as an integer (e.g. 20 -> 0.20)
                if percent > 1.0:
                    percent /= 100.0
                
                # Check for "except" clause: "all room categories except deluxe and suite"
                if "except" in keyword:
                    parts = keyword.split("except")
                    exceptions_str = parts[1].strip()
                    # Split exception keywords
                    exceptions = [e.strip() for e in exceptions_str.replace("and", ",").split(",") if e.strip()]
                    
                    # If room matches "all room categories" and does NOT contain any exceptions
                    if not any(exc in room_lower for exc in exceptions):
                        if len(keyword) > best_len:
                            best_match = percent
                            best_len = len(keyword)
                elif keyword == "all room categories":
                    if len(keyword) > best_len:
                        best_match = percent
                        best_len = len(keyword)
                elif keyword in room_lower:
                    if len(keyword) > best_len:
                        best_match = percent
                        best_len = len(keyword)
            return best_match

        # Canonical Annexure V table fallback
        _ANNEXURE_V: Dict[str, Dict[str, float]] = {
            "Classic": {
                "general ward":  0.00,
                "twin sharing":  0.20,
                "semi private":  0.10,
                "deluxe":        0.40,
                "suite":         0.50,
                "private":       0.40,
            },
            "Select": {
                "general ward":  0.00,
                "twin sharing":  0.00,
                "semi private":  0.00,
                "deluxe":        0.20,
                "suite":         0.40,
                "private":       0.00,
            },
            "Elite": {
                "general ward":  0.00,
                "twin sharing":  0.00,
                "semi private":  0.00,
                "deluxe":        0.00,
                "suite":         0.20,
                "private":       0.00,
            },
        }

        variant_table = _ANNEXURE_V.get(variant, {})
        room_lower = (room_category_claimed or "").lower()

        # Longest-match wins — prevents 'private' from matching 'semi private'
        best_match = 0.0
        best_len = -1
        for keyword, percent in variant_table.items():
            if keyword in room_lower and len(keyword) > best_len:
                best_match = percent
                best_len = len(keyword)

        return best_match

    def _assign_dependencies_dynamically(self):
        """
        Dynamically assign depends_on list to rules based on logical gate hierarchy
        and explicit financial calculator constraints.
        """
        loaded_rule_ids = set(self.rules.keys())
        
        # Helper to get all rule IDs for a gate
        def get_ids_for_gate(gate: RuleGate) -> List[str]:
            return [r.rule_id for r in self.rules_by_gate[gate]]

        policy_ids = get_ids_for_gate(RuleGate.POLICY_VALIDATION)
        member_ids = get_ids_for_gate(RuleGate.MEMBER_VALIDATION)
        waiting_ids = get_ids_for_gate(RuleGate.WAITING_PERIOD_VALIDATION)
        exclusion_ids = get_ids_for_gate(RuleGate.EXCLUSION_VALIDATION)
        coverage_ids = get_ids_for_gate(RuleGate.COVERAGE_VALIDATION)
        financial_ids = get_ids_for_gate(RuleGate.FINANCIAL_COMPUTATION)

        for rule_id, rule in self.rules.items():
            depends = list(rule.depends_on)
            
            # 1. Gate order dependencies
            if rule.gate == RuleGate.MEMBER_VALIDATION:
                depends.extend(policy_ids)
            
            elif rule.gate in [RuleGate.WAITING_PERIOD_VALIDATION, RuleGate.EXCLUSION_VALIDATION]:
                depends.extend(policy_ids)
                depends.extend(member_ids)
                
            elif rule.gate == RuleGate.COVERAGE_VALIDATION:
                depends.extend(policy_ids)
                depends.extend(member_ids)
                depends.extend(waiting_ids)
                depends.extend(exclusion_ids)
                
            elif rule.gate == RuleGate.FINANCIAL_COMPUTATION:
                depends.extend(policy_ids)
                depends.extend(member_ids)
                depends.extend(waiting_ids)
                depends.extend(exclusion_ids)
                depends.extend(coverage_ids)
                
                # Intra-gate financial dependencies
                if rule_id in ["R3_FIN_001", "R3_FIN_003"]:  # Deductible
                    depends.extend([dep for dep in ["R3_BEN_004", "R3_GEN_002"] if dep in loaded_rule_ids])
                elif rule_id == "R3_FIN_002":  # Co-payment
                    depends.extend([dep for dep in ["R3_BEN_004", "R3_GEN_002", "R3_BEN_016", "R3_BEN_017", "R3_BEN_018", "R3_BEN_019"] if dep in loaded_rule_ids])
                elif rule_id == "R3_SUM_001":  # SI waterfall
                    depends.extend([dep for dep in ["R3_FIN_001", "R3_FIN_002"] if dep in loaded_rule_ids])
                    
            elif rule.gate == RuleGate.STATE_UPDATE:
                depends.extend(policy_ids)
                depends.extend(member_ids)
                depends.extend(waiting_ids)
                depends.extend(exclusion_ids)
                depends.extend(coverage_ids)
                depends.extend(financial_ids)

            # Assign back (remove self-dependency if any, and deduplicate preserving order)
            seen = set()
            rule.depends_on = [x for x in depends if x != rule_id and not (x in seen or seen.add(x))]


# ============================================================================
# PRODUCT MEMORY CACHE — version-aware (Issue 21)
# ============================================================================

# Maps product_json_version string → ProductMemoryStore instance.
# Each distinct version that appears in ClaimContext gets its own store.
# Currently all versions resolve to the same extraction file; this dict is
# the extensibility hook for when version-specific files are introduced.
_product_memory_cache: Dict[str, ProductMemoryStore] = {}

# Default version string — MUST match ClaimContext.product_json_version default (Fix 9).
# Keeping these identical ensures all calls without an explicit version resolve to
# the same cache entry and the singleton pattern stays effective.
_DEFAULT_VERSION = "R3_v2.1_2025-01-15"

def _version_to_path(version: str) -> Optional[str]:
    """
    Map a product_json_version string to the extraction file path.

    Convention: place versioned files as
        docs/Product and Policy Rules Extraction_<version>.txt
    If no version-specific file is found, fall back to the canonical file
    (all current R3 versions share the same rule text).
    """
    versioned = _DOCS_DIR / f"Product and Policy Rules Extraction_{version}.txt"
    if versioned.exists():
        return str(versioned)
    # Fall back to the single canonical extraction file
    return None   # ProductMemoryStore.__init__ will use its own fallback list


def get_product_memory(version: str = _DEFAULT_VERSION) -> "ProductMemoryStore":
    """
    Return a ProductMemoryStore for the requested product JSON version.

    Caches one instance per version string so re-parsing is avoided.
    Pass version="" or omit the argument to get the default store.
    """
    if not version:
        version = _DEFAULT_VERSION

    if version not in _product_memory_cache:
        product_json_path = _version_to_path(version)
        _product_memory_cache[version] = ProductMemoryStore(
            product_json_path=product_json_path
        )

    return _product_memory_cache[version]
