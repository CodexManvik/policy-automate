"""
AI Planner - Phase 2
Constructs Directed Acyclic Graph (DAG) from rule dependencies
Performs topological sort to create linear Execution Plan
"""

from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass, field
from collections import deque, defaultdict
import heapq

from schemas import ClaimContext, LineItemData
from product_memory import ProductMemoryStore, RuleBlueprint, RuleGate, get_product_memory


@dataclass
class ExecutionStep:
    """Single step in the execution plan"""
    step_number: int
    rule_id: str
    rule_name: str
    gate: RuleGate
    priority: int
    reason: str
    execution_type: str
    tool_required: Optional[str] = None
    semantic_prompt: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    """Complete execution plan for a claim line item"""
    plan_id: str
    claim_id: str
    line_item_id: str
    variant: str
    benefit_bucket: str
    
    execution_steps: List[ExecutionStep] = field(default_factory=list)
    confidence: float = 1.0
    planning_notes: List[str] = field(default_factory=list)
    
    # DAG metadata
    total_rules_evaluated: int = 0
    rules_filtered_out: int = 0
    dependency_depth: int = 0


class DAGBuilder:
    """
    Builds Directed Acyclic Graph from rule dependencies
    Performs topological sort for execution ordering
    """
    
    def __init__(self):
        self.adjacency_list: Dict[str, List[str]] = defaultdict(list)
        self.in_degree: Dict[str, int] = defaultdict(int)
        self.nodes: Dict[str, RuleBlueprint] = {}
    
    def add_rule(self, rule: RuleBlueprint):
        """Add a rule node to the DAG"""
        self.nodes[rule.rule_id] = rule
        
        # Initialize in-degree
        if rule.rule_id not in self.in_degree:
            self.in_degree[rule.rule_id] = 0
        
        # Add edges for dependencies
        for dependency in rule.depends_on:
            self.adjacency_list[dependency].append(rule.rule_id)
            self.in_degree[rule.rule_id] += 1
    
    def get_rule_priority_tuple(self, rule_id: str) -> Tuple[int, int, str]:
        rule = self.nodes.get(rule_id)
        if not rule:
            return (99, 999, rule_id)
        gate_order = {
            RuleGate.POLICY_VALIDATION: 1,
            RuleGate.MEMBER_VALIDATION: 2,
            RuleGate.COVERAGE_VALIDATION: 3,
            RuleGate.WAITING_PERIOD_VALIDATION: 4,
            RuleGate.EXCLUSION_VALIDATION: 5,
            RuleGate.FINANCIAL_COMPUTATION: 6,
            RuleGate.STATE_UPDATE: 7
        }
        gate_val = gate_order.get(rule.gate, 99)
        return (gate_val, rule.priority, rule.rule_id)

    def topological_sort(self) -> List[RuleBlueprint]:
        """
        Perform lexicographical topological sort using a Priority Queue (Kahn's algorithm).
        Returns rules strictly in gate and priority sequence.
        """
        # Rebuild clean adjacency list and in-degree maps to ignore outside dependencies
        self.adjacency_list = defaultdict(list)
        self.in_degree = {rule_id: 0 for rule_id in self.nodes}
        
        for rule_id, rule in self.nodes.items():
            for dep_id in rule.depends_on:
                if dep_id in self.nodes:
                    self.adjacency_list[dep_id].append(rule_id)
                    self.in_degree[rule_id] += 1
                else:
                    print(f"Warning: Ignored dependency {dep_id} of rule {rule_id} as it is not in the active rules set.")

        heap = []
        for rule_id, degree in self.in_degree.items():
            if degree == 0:
                heapq.heappush(heap, (self.get_rule_priority_tuple(rule_id), rule_id))
                
        sorted_rules = []
        
        while heap:
            _, current_id = heapq.heappop(heap)
            
            if current_id in self.nodes:
                sorted_rules.append(self.nodes[current_id])
            
            # Reduce in-degree for dependent nodes
            for neighbor in self.adjacency_list[current_id]:
                self.in_degree[neighbor] -= 1
                
                if self.in_degree[neighbor] == 0:
                    heapq.heappush(heap, (self.get_rule_priority_tuple(neighbor), neighbor))
        
        # Check for cycles
        if len(sorted_rules) < len(self.nodes):
            # Cycle detected - fallback to priority sort
            print("Warning: Cycle detected in rule dependencies. Falling back to priority sort.")
            return sorted(self.nodes.values(), key=lambda r: self.get_rule_priority_tuple(r.rule_id))
        
        return sorted_rules
    
    def calculate_depth(self) -> int:
        """Calculate maximum dependency depth"""
        depths = {}
        
        def get_depth(rule_id: str) -> int:
            if rule_id in depths:
                return depths[rule_id]
            
            if rule_id not in self.nodes:
                return 0
            
            rule = self.nodes[rule_id]
            if not rule.depends_on:
                depths[rule_id] = 0
                return 0
            
            max_dep_depth = max((get_depth(dep) for dep in rule.depends_on), default=0)
            depths[rule_id] = max_dep_depth + 1
            return depths[rule_id]
        
        return max((get_depth(rule_id) for rule_id in self.nodes.keys()), default=0)


class AIPlanner:
    """
    AI-Powered Execution Planner
    
    Responsibilities:
    1. Filter applicable rules based on claim context
    2. Construct DAG from rule dependencies
    3. Perform topological sort
    4. Generate linear Execution Plan
    """
    
    def __init__(self, product_memory: Optional[ProductMemoryStore] = None):
        self.product_memory = product_memory or get_product_memory()
        self.plan_counter = 0
    
    def create_execution_plan(
        self,
        claim_context: ClaimContext,
        line_item: LineItemData
    ) -> ExecutionPlan:
        """
        Main entry point: Create execution plan for a line item
        
        Args:
            claim_context: Full claim context
            line_item: Specific line item to adjudicate
        
        Returns:
            ExecutionPlan with topologically sorted steps
        """
        self.plan_counter += 1
        plan_id = f"PLAN-{claim_context.claim_id}-{line_item.line_item_id}-{self.plan_counter}"
        
        # Step 1: Filter applicable rules
        applicable_rules = self._filter_applicable_rules(claim_context, line_item)
        
        # Step 2: Build DAG
        dag = self._build_dag(applicable_rules)
        
        # Step 3: Topological sort
        sorted_rules = dag.topological_sort()
        
        # Step 4: Create execution steps
        execution_steps = self._create_execution_steps(sorted_rules, claim_context, line_item)
        
        # Step 5: Compose execution plan
        plan = ExecutionPlan(
            plan_id=plan_id,
            claim_id=claim_context.claim_id,
            line_item_id=line_item.line_item_id,
            variant=claim_context.policy.variant,
            benefit_bucket=line_item.benefit_bucket,
            execution_steps=execution_steps,
            total_rules_evaluated=len(self.product_memory.rules),
            rules_filtered_out=len(self.product_memory.rules) - len(applicable_rules),
            dependency_depth=dag.calculate_depth()
        )
        
        return plan
    
    def _filter_applicable_rules(
        self,
        claim_context: ClaimContext,
        line_item: LineItemData
    ) -> List[RuleBlueprint]:
        """
        Filter rules based on claim context with detailed logging

        Filtering criteria:
        1. Variant applicability
        2. Benefit bucket matching
        3. Optional benefit opted flags
        4. Precondition checks
        """
        all_rules = list(self.product_memory.rules.values())
        applicable = []
        filtered_out: Dict[str, str] = {}

        for rule in all_rules:
            # Check variant
            if claim_context.policy.variant not in rule.variant_applicability:
                filtered_out[rule.rule_id] = f"variant mismatch (rule:{rule.variant_applicability} vs policy:{claim_context.policy.variant})"
                continue

            # Check benefit bucket filter using applicable_buckets (robust case-insensitive check)
            buckets_filter = rule.applicable_buckets or rule.benefit_bucket_filter
            if buckets_filter:
                normalized_filter = [b.lower().replace('&', 'and').strip() for b in buckets_filter]
                normalized_bucket = line_item.benefit_bucket.lower().replace('&', 'and').strip()
                if normalized_bucket not in normalized_filter:
                    filtered_out[rule.rule_id] = f"bucket mismatch (rule:{buckets_filter} vs item:{line_item.benefit_bucket})"
                    continue

            # Check optional benefit preconditions
            if not self._check_optional_benefit_opted(rule, claim_context):
                filtered_out[rule.rule_id] = "optional benefit not opted"
                continue

            # Check basic preconditions
            if not self._evaluate_preconditions(rule, claim_context, line_item):
                filtered_out[rule.rule_id] = "precondition check failed"
                continue

            applicable.append(rule)

        return applicable
    
    def _check_optional_benefit_opted(
        self,
        rule: RuleBlueprint,
        claim_context: ClaimContext
    ) -> bool:
        """Check if optional benefit is opted"""
        if rule.benefit_category != "optional":
            return True
        
        # Check specific optional benefits
        if "HeadsUp" in rule.rule_name and not claim_context.policy.heads_up_opted:
            return False
        
        if "Tiered Network" in rule.rule_name and not claim_context.policy.tiered_network_opted:
            return False
        
        if "Deductible" in rule.rule_name and (
            not claim_context.policy.annual_aggregate_deductible or 
            claim_context.policy.annual_aggregate_deductible == 0
        ):
            return False
        
        if "Co-Payment" in rule.rule_name and (
            not claim_context.policy.co_payment_percent or 
            claim_context.policy.co_payment_percent == 0
        ):
            return False
        
        return True
    
    def _evaluate_preconditions(
        self,
        rule: RuleBlueprint,
        claim_context: ClaimContext,
        line_item: LineItemData
    ) -> bool:
        """
        Evaluate rule preconditions
        Simple string-based evaluation for Phase 2
        """
        if not rule.preconditions:
            return True
        
        for precondition in rule.preconditions:
            # Simple keyword matching for common preconditions
            if "accident_related == false" in precondition:
                if line_item.accident_related:
                    return False
            
            if "accident_related == true" in precondition:
                if not line_item.accident_related:
                    return False
            
            if "hospitalization_hours >=" in precondition:
                if not line_item.hospitalization_hours:
                    return False
                
                # Extract threshold
                try:
                    threshold = float(precondition.split(">=")[1].strip())
                    if line_item.hospitalization_hours < threshold:
                        return False
                except:
                    pass
            
            if "copay" in precondition.lower() or "deductible" in precondition.lower():
                # Already checked in optional benefit logic
                continue
        
        return True
    
    def _build_dag(self, rules: List[RuleBlueprint]) -> DAGBuilder:
        """
        Build DAG from filtered rules
        
        Filters out dependencies that reference rules not in the applicable set
        to avoid false cycle detection
        """
        dag = DAGBuilder()
        
        # Get set of rule IDs that are actually included
        rule_ids = {rule.rule_id for rule in rules}
        
        for rule in rules:
            # Filter dependencies to only include rules in this set
            valid_depends_on = [dep for dep in rule.depends_on if dep in rule_ids]
            
            # Create a copy with filtered dependencies
            filtered_rule = RuleBlueprint(
                rule_id=rule.rule_id,
                rule_name=rule.rule_name,
                gate=rule.gate,
                section_ref=rule.section_ref,
                benefit_category=rule.benefit_category,
                priority=rule.priority,
                execution_type=rule.execution_type,
                depends_on=valid_depends_on,
                formula=rule.formula,
                tool_required=rule.tool_required,
                variant_applicability=rule.variant_applicability,
                benefit_bucket_filter=rule.benefit_bucket_filter,
                preconditions=rule.preconditions,
                semantic_prompt_template=rule.semantic_prompt_template,
                source_page=rule.source_page,
                auto_adjudicable=rule.auto_adjudicable,
                confidence_weight=rule.confidence_weight,
                notes=rule.notes,
                exclusions_within_benefit=rule.exclusions_within_benefit
            )
            
            dag.add_rule(filtered_rule)
        
        return dag
    
    def _create_execution_steps(
        self,
        sorted_rules: List[RuleBlueprint],
        claim_context: ClaimContext,
        line_item: LineItemData
    ) -> List[ExecutionStep]:
        """Convert sorted rules into execution steps"""
        steps = []
        
        for idx, rule in enumerate(sorted_rules, start=1):
            # Prepare semantic prompt if needed
            semantic_prompt = None
            if rule.semantic_prompt_template:
                semantic_prompt = self._prepare_semantic_prompt(
                    rule.semantic_prompt_template,
                    claim_context,
                    line_item
                )
            
            step = ExecutionStep(
                step_number=idx,
                rule_id=rule.rule_id,
                rule_name=rule.rule_name,
                gate=rule.gate,
                priority=rule.priority,
                reason=f"{rule.rule_name} applies to {line_item.benefit_bucket}",
                execution_type=rule.execution_type.value,
                tool_required=rule.tool_required,
                semantic_prompt=semantic_prompt,
                depends_on=rule.depends_on
            )
            
            steps.append(step)
        
        return steps
    
    def _prepare_semantic_prompt(
        self,
        template: str,
        claim_context: ClaimContext,
        line_item: LineItemData
    ) -> str:
        """Prepare semantic reasoning prompt from template"""
        # Simple placeholder replacement
        prompt = template
        
        # Replace common placeholders
        replacements = {
            "{condition}": line_item.condition_diagnosed,
            "{treatment_description}": line_item.description,
            "{diagnosis}": line_item.condition_diagnosed,
            "{hospitalization_hours}": str(line_item.hospitalization_hours or 0),
            "{treatment_type}": line_item.treatment_type,
            "{admission_reason}": line_item.description,
            "{procedures}": line_item.description,
            "{icd_codes}": "[]",  # Would come from medical records
            "{doctor_notes}": "",  # Would come from claim documents
            "{discharge_summary}": line_item.discharge_summary or "",
            "{medical_history}": str(claim_context.member.ped_declarations),
            "{disease_list}": "Cataract, Hernia, Stones, PCOD, Hysterectomy, Hemorrhoids, Fistula, Varicose Veins"
        }
        
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, value)
        
        return prompt
