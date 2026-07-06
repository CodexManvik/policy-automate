"""
Graph Exporter for Claims Auto-Adjudication Engine.
Generates an interactive, premium HTML debug visualization of the adjudication path (DAG).
"""

from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from schemas import ClaimContext, ClaimDecision, DecisionTrace, LineItemDecision

def _datetime_serializer(obj: Any) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def save_adjudication_graph(claim_decision: ClaimDecision, context: ClaimContext) -> str:
    """
    Generates a self-contained, interactive HTML file visualizing the adjudication graph.
    Saves it inside the `graphs/` directory in the project root.

    Returns the bare filename (e.g. 'claim_decision_CLM-001_20260704_152030.html').
    The full disk path is NOT returned; the file is served via the /graphs/ static
    route mounted in main.py.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    graphs_dir = os.path.join(project_root, "graphs")
    os.makedirs(graphs_dir, exist_ok=True)
    
    claim_id = claim_decision.claim_id
    filename = f"claim_decision_{claim_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    filepath = os.path.join(graphs_dir, filename)
    
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    nodes_data_map: Dict[str, Dict[str, Any]] = {}
    
    # Helper to clean/prepare data for JS injection
    def get_node_group(evaluation: str) -> str:
        if evaluation in ("PASSED", "APPROVED"):
            return "rule_pass"
        elif evaluation in ("FAILED", "REJECTED", "EXCLUSION_ACTIVE"):
            return "rule_fail"
        elif evaluation in ("DEDUCTION_APPLIED", "ASSISTED_REVIEW", "PENDING_REVIEW", "MEDICAL_REVIEW"):
            return "rule_warn"
        else:
            return "rule_na"

    # 1. Claim Root Node
    root_id = "claim_root"
    nodes.append({
        "id": root_id,
        "label": f"Claim: {claim_id}\n({context.policy.variant} Variant)",
        "group": "claim",
        "level": 0
    })
    nodes_data_map[root_id] = {
        "title": f"Claim Context: {claim_id}",
        "type": "Claim Root",
        "policy_id": context.policy.policy_id,
        "variant": context.policy.variant,
        "base_sum_insured": context.policy.base_sum_insured,
        "member_name": context.member.name,
        "member_age": context.member.age,
        "base_si_remaining": context.benefit_balance.base_si_remaining,
        "booster_plus_remaining": context.benefit_balance.booster_plus_remaining,
        "assembled_at": context.context_assembled_at.isoformat() if isinstance(context.context_assembled_at, datetime) else str(context.context_assembled_at)
    }

    # Extract all traces
    # Pre-traces: policy_validation and member_validation (global, before line items)
    # Post-traces: state_update (global, after line items)
    pre_traces: List[DecisionTrace] = []
    post_traces: List[DecisionTrace] = []
    
    for trace in claim_decision.decision_trace:
        if trace.gate in ("policy_validation", "member_validation"):
            pre_traces.append(trace)
        elif trace.gate == "state_update":
            post_traces.append(trace)
            
    pre_traces.sort(key=lambda x: x.step)
    post_traces.sort(key=lambda x: x.step)
    
    # 2. Sequential Pre-Traces (Policy & Member Gates)
    last_node_id = root_id
    current_level = 1
    
    for trace in pre_traces:
        node_id = f"step_{trace.step}_{trace.rule_id}"
        group = get_node_group(trace.evaluation)
        nodes.append({
            "id": node_id,
            "label": f"{trace.rule_id}\n{trace.rule_name}",
            "group": group,
            "level": current_level
        })
        edges.append({
            "from": last_node_id,
            "to": node_id,
            "arrows": "to"
        })
        nodes_data_map[node_id] = {
            "title": f"Rule {trace.rule_id}: {trace.rule_name}",
            "type": "Global Policy/Member Validation",
            "gate": trace.gate,
            "evaluation": trace.evaluation,
            "reason": trace.reason,
            "confidence": trace.confidence,
            "inputs": trace.inputs,
            "source_section": trace.source_section,
            "source_page": trace.source_page,
            "raw_llm_response": trace.raw_llm_response,
        }
        last_node_id = node_id
        current_level += 1
        
    # Router node to split into Line Items
    router_id = "line_items_router"
    nodes.append({
        "id": router_id,
        "label": "Line Items\nBranching",
        "group": "claim",
        "level": current_level
    })
    edges.append({
        "from": last_node_id,
        "to": router_id,
        "arrows": "to"
    })
    nodes_data_map[router_id] = {
        "title": "Line Items Adjudication",
        "type": "Router",
        "description": "Splits the adjudication process to evaluate each claim line item concurrently through gates 3-6."
    }
    
    # 3. Line Item Branches
    line_item_final_nodes: List[str] = []
    line_item_start_level = current_level + 1
    max_branch_depth = 0
    
    for idx, li_decision in enumerate(claim_decision.line_items):
        li_root_id = f"li_{li_decision.line_item_id}"
        nodes.append({
            "id": li_root_id,
            "label": f"Line Item {idx + 1}:\n{li_decision.description}\n(Claimed: INR {li_decision.claimed_amount})",
            "group": "line_item",
            "level": line_item_start_level
        })
        edges.append({
            "from": router_id,
            "to": li_root_id,
            "arrows": "to"
        })
        nodes_data_map[li_root_id] = {
            "title": f"Line Item: {li_decision.description}",
            "type": "Line Item Root",
            "line_item_id": li_decision.line_item_id,
            "claimed_amount": li_decision.claimed_amount,
            "admissible_amount": li_decision.admissible_amount,
            "payable_amount": li_decision.payable_amount,
            "decision": li_decision.decision,
            "deductions": [d.model_dump() for d in li_decision.deductions]
        }
        
        # Sequentially link rule traces for this specific line item
        li_traces = sorted(li_decision.decision_trace, key=lambda x: x.step)
        li_last_node_id = li_root_id
        branch_depth = 0
        
        for li_trace in li_traces:
            node_id = f"step_{li_trace.step}_{li_trace.rule_id}_{li_decision.line_item_id}"
            group = get_node_group(li_trace.evaluation)
            nodes.append({
                "id": node_id,
                "label": f"{li_trace.rule_id}\n{li_trace.rule_name}",
                "group": group,
                "level": line_item_start_level + 1 + branch_depth
            })
            edges.append({
                "from": li_last_node_id,
                "to": node_id,
                "arrows": "to"
            })
            nodes_data_map[node_id] = {
                "title": f"Rule {li_trace.rule_id}: {li_trace.rule_name}",
                "type": f"Line Item Rule ({li_decision.description})",
                "gate": li_trace.gate,
                "evaluation": li_trace.evaluation,
                "reason": li_trace.reason,
                "confidence": li_trace.confidence,
                "inputs": li_trace.inputs,
                "source_section": li_trace.source_section,
                "source_page": li_trace.source_page,
                "raw_llm_response": li_trace.raw_llm_response,
            }
            li_last_node_id = node_id
            branch_depth += 1
            
        line_item_final_nodes.append(li_last_node_id)
        if branch_depth > max_branch_depth:
            max_branch_depth = branch_depth
            
    # 4. Join Branches back to Gate 7: State Update
    gate_7_start_level = line_item_start_level + max_branch_depth + 2
    gate_7_root_id = "gate_7_root"
    nodes.append({
        "id": gate_7_root_id,
        "label": "Gate 7:\nState Update",
        "group": "claim",
        "level": gate_7_start_level
    })
    
    for li_final_node in line_item_final_nodes:
        edges.append({
            "from": li_final_node,
            "to": gate_7_root_id,
            "arrows": "to"
        })
    nodes_data_map[gate_7_root_id] = {
        "title": "Gate 7: State Update and Persistence",
        "type": "Global Post-Processing Gate",
        "description": "Applies final cumulative policy state updates such as ReAssure Forever triggers, Booster+ renewals, Cash-Bag+ wallet calculations, etc."
    }
    
    last_node_id = gate_7_root_id
    current_level = gate_7_start_level + 1
    
    for trace in post_traces:
        node_id = f"step_{trace.step}_{trace.rule_id}"
        group = get_node_group(trace.evaluation)
        nodes.append({
            "id": node_id,
            "label": f"{trace.rule_id}\n{trace.rule_name}",
            "group": group,
            "level": current_level
        })
        edges.append({
            "from": last_node_id,
            "to": node_id,
            "arrows": "to"
        })
        nodes_data_map[node_id] = {
            "title": f"Rule {trace.rule_id}: {trace.rule_name}",
            "type": "State Update Rule",
            "gate": trace.gate,
            "evaluation": trace.evaluation,
            "reason": trace.reason,
            "confidence": trace.confidence,
            "inputs": trace.inputs,
            "source_section": trace.source_section,
            "source_page": trace.source_page,
            "raw_llm_response": trace.raw_llm_response,
        }
        last_node_id = node_id
        current_level += 1

    # 5. Final Adjudication Decision Node
    final_node_id = "final_decision_node"
    final_decision_group = "rule_pass" if claim_decision.claim_decision in ("APPROVED", "PARTIALLY_APPROVED") else "rule_fail"
    if claim_decision.claim_decision in ("ASSISTED_REVIEW", "PENDING_REVIEW", "MEDICAL_REVIEW"):
        final_decision_group = "rule_warn"
        
    nodes.append({
        "id": final_node_id,
        "label": f"Final Decision: {claim_decision.claim_decision}\nPayable: INR {claim_decision.total_payable}",
        "group": final_decision_group,
        "level": current_level
    })
    edges.append({
        "from": last_node_id,
        "to": final_node_id,
        "arrows": "to"
    })
    nodes_data_map[final_node_id] = {
        "title": f"Adjudication Decision: {claim_decision.claim_decision}",
        "type": "Final Decision Composer",
        "claim_id": claim_decision.claim_id,
        "total_claimed": claim_decision.total_claimed,
        "total_admissible": claim_decision.total_admissible,
        "total_payable": claim_decision.total_payable,
        "total_deductions": claim_decision.total_deductions,
        "deduction_breakdown": claim_decision.deduction_breakdown.model_dump(),
        "waterfall": claim_decision.si_waterfall_breakdown.model_dump(),
        "manual_review_required": claim_decision.manual_review_required,
        "review_reasons": claim_decision.review_reasons,
        "confidence_score": claim_decision.confidence_score
    }

    # Load HTML template containing Vis.js and details view
    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Claims Auto-Adjudication Debug Graph - {claim_id}</title>
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: 'Outfit', sans-serif;
            background-color: #0b0f19;
            color: #f8fafc;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        header {{
            background: linear-gradient(135deg, #1e293b, #0f172a);
            border-bottom: 1px solid #334155;
            padding: 15px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06);
            z-index: 10;
        }}
        .brand-title {{
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.5px;
            background: linear-gradient(to right, #60a5fa, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .summary-stats {{
            display: flex;
            gap: 20px;
        }}
        .stat-card {{
            background: rgba(30, 41, 59, 0.5);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 6px 14px;
            text-align: center;
            backdrop-filter: blur(10px);
        }}
        .stat-label {{
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #94a3b8;
        }}
        .stat-value {{
            font-size: 14px;
            font-weight: 600;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .badge-approved {{ background-color: rgba(16, 185, 129, 0.2); border: 1px solid #10b981; color: #34d399; }}
        .badge-rejected {{ background-color: rgba(239, 68, 68, 0.2); border: 1px solid #ef4444; color: #f87171; }}
        .badge-review {{ background-color: rgba(245, 158, 11, 0.2); border: 1px solid #f59e0b; color: #fbbf24; }}

        main {{
            display: flex;
            flex: 1;
            position: relative;
            overflow: hidden;
        }}
        #network {{
            flex: 1;
            height: 100%;
            background-color: #090d16;
        }}
        #sidebar {{
            width: 450px;
            background: rgba(15, 23, 42, 0.95);
            border-left: 1px solid #334155;
            height: 100%;
            display: flex;
            flex-direction: column;
            box-shadow: -10px 0 20px -10px rgba(0,0,0,0.5);
            z-index: 5;
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            overflow-y: auto;
        }}
        .sidebar-header {{
            padding: 20px;
            border-bottom: 1px solid #334155;
            background: #1e293b;
        }}
        .sidebar-content {{
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }}
        .card {{
            background: rgba(30, 41, 59, 0.4);
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 15px;
        }}
        .card-title {{
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #94a3b8;
            margin-bottom: 10px;
            border-bottom: 1px solid rgba(51, 65, 85, 0.5);
            padding-bottom: 5px;
            font-weight: 600;
        }}
        .detail-row {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 14px;
        }}
        .detail-row:last-child {{
            margin-bottom: 0;
        }}
        .detail-key {{
            color: #94a3b8;
        }}
        .detail-val {{
            font-weight: 500;
            text-align: right;
            max-width: 60%;
            word-break: break-all;
        }}
        .reason-box {{
            line-height: 1.5;
            font-size: 14px;
            background: rgba(15, 23, 42, 0.6);
            border-left: 3px solid #60a5fa;
            padding: 10px;
            border-radius: 0 8px 8px 0;
            white-space: pre-wrap;
        }}
        .reason-box.failed {{ border-left-color: #ef4444; }}
        .reason-box.passed {{ border-left-color: #10b981; }}
        .reason-box.warning {{ border-left-color: #f59e0b; }}
        
        pre {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            background-color: #090d16;
            padding: 10px;
            border-radius: 6px;
            overflow-x: auto;
            border: 1px solid #1e293b;
            color: #38bdf8;
            max-height: 250px;
        }}
        .think-block {{
            margin: 8px 0;
            border: 1px solid rgba(245, 158, 11, 0.3);
            border-radius: 8px;
            overflow: hidden;
            background: rgba(120, 53, 15, 0.12);
        }}
        .think-summary {{
            cursor: pointer;
            padding: 9px 12px;
            font-size: 12px;
            font-weight: 600;
            color: #fbbf24;
            display: flex;
            align-items: center;
            gap: 6px;
            user-select: none;
            list-style: none;
        }}
        .think-summary::-webkit-details-marker {{ display: none; }}
        .think-summary::before {{
            content: "▶";
            font-size: 9px;
            transition: transform 0.2s;
        }}
        details[open] .think-summary::before {{ transform: rotate(90deg); }}
        .think-pre {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            background: rgba(9, 13, 22, 0.9);
            padding: 10px 14px;
            margin: 0;
            border-top: 1px solid rgba(245, 158, 11, 0.2);
            color: #fde68a;
            white-space: pre-wrap;
            word-break: break-word;
            max-height: 400px;
            overflow-y: auto;
            border-radius: 0;
        }}
        .toolbar {{
            position: absolute;
            top: 20px;
            left: 20px;
            background: rgba(15, 23, 42, 0.85);
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 10px 15px;
            backdrop-filter: blur(10px);
            z-index: 4;
            display: flex;
            gap: 15px;
            align-items: center;
        }}
        .toolbar label {{
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 6px;
            cursor: pointer;
        }}
        .toolbar input[type="checkbox"] {{
            accent-color: #3b82f6;
            cursor: pointer;
        }}
    </style>
</head>
<body>
    <header>
        <div>
            <div class="brand-title">Claims Adjudication Debug Graph</div>
            <div style="font-size: 12px; color: #94a3b8; margin-top: 2px;">Claim ID: {claim_id}</div>
        </div>
        <div class="summary-stats">
            <div class="stat-card">
                <div class="stat-label">Claimed</div>
                <div class="stat-value">INR {claim_decision.total_claimed:.2f}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Payable</div>
                <div class="stat-value" style="color: #34d399;">INR {claim_decision.total_payable:.2f}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Deductions</div>
                <div class="stat-value" style="color: #f87171;">INR {claim_decision.total_deductions:.2f}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Decision</div>
                <div class="stat-value">
                    <span class="badge badge-{'approved' if claim_decision.claim_decision in ('APPROVED', 'PARTIALLY_APPROVED') else 'rejected' if claim_decision.claim_decision == 'REJECTED' else 'review'}">
                        {claim_decision.claim_decision}
                    </span>
                </div>
            </div>
        </div>
    </header>

    <main>
        <div class="toolbar">
            <label>
                <input type="checkbox" id="hide-na" checked> Hide Not-Applicable Rules
            </label>
            <div style="color: #475569;">|</div>
            <div style="font-size: 11px; color: #64748b;">Double click node to center. Drag/Zoom canvas.</div>
        </div>
        <div id="network"></div>
        <div id="sidebar">
            <div class="sidebar-header">
                <h2 id="sd-title" style="font-size: 18px; font-weight: 600;">Adjudication Flow</h2>
                <p id="sd-type" style="font-size: 12px; color: #94a3b8; text-transform: uppercase; margin-top: 2px;">Select a node to inspect</p>
            </div>
            <div class="sidebar-content" id="sd-content">
                <div class="card">
                    <p style="color: #94a3b8; font-size: 14px;">Select any node on the graph network to display its corresponding inputs, rule configurations, semantic logs, and detailed computation metrics here.</p>
                </div>
            </div>
        </div>
    </main>

    <script type="text/javascript">
        const rawNodes = {json.dumps(nodes)};
        const rawEdges = {json.dumps(edges)};
        const nodesData = {json.dumps(nodes_data_map, default=_datetime_serializer)};

        const hideNaCheckbox = document.getElementById("hide-na");
        
        let nodesDataset = new vis.DataSet(rawNodes);
        let edgesDataset = new vis.DataSet(rawEdges);

        const container = document.getElementById('network');
        const data = {{
            nodes: nodesDataset,
            edges: edgesDataset
        }};
        
        const options = {{
            layout: {{
                hierarchical: {{
                    direction: 'LR',
                    sortMethod: 'directed',
                    nodeSpacing: 150,
                    levelSeparation: 260
                }}
            }},
            physics: {{
                hierarchicalRepulsion: {{
                    nodeSpacing: 180
                }}
            }},
            edges: {{
                smooth: {{
                    type: 'cubicBezier',
                    forceDirection: 'horizontal',
                    roundness: 0.4
                }},
                arrows: {{
                    to: {{ enabled: true, scaleFactor: 1.0 }}
                }}
            }},
            groups: {{
                claim: {{
                    shape: 'box',
                    margin: 15,
                    color: {{ background: '#1e293b', border: '#3b82f6' }},
                    font: {{ color: '#f8fafc', size: 14, face: 'Outfit', bold: true }}
                }},
                line_item: {{
                    shape: 'box',
                    margin: 12,
                    color: {{ background: '#1e1b4b', border: '#6366f1' }},
                    font: {{ color: '#f8fafc', size: 12, face: 'Outfit', bold: true }}
                }},
                rule_pass: {{
                    shape: 'box',
                    margin: 10,
                    color: {{ background: '#064e3b', border: '#10b981' }},
                    font: {{ color: '#ecfdf5', size: 11, face: 'Outfit' }}
                }},
                rule_fail: {{
                    shape: 'box',
                    margin: 10,
                    color: {{ background: '#7f1d1d', border: '#ef4444' }},
                    font: {{ color: '#fef2f2', size: 11, face: 'Outfit' }}
                }},
                rule_warn: {{
                    shape: 'box',
                    margin: 10,
                    color: {{ background: '#78350f', border: '#f59e0b' }},
                    font: {{ color: '#fffbeb', size: 11, face: 'Outfit' }}
                }},
                rule_na: {{
                    shape: 'box',
                    margin: 10,
                    color: {{ background: '#374151', border: '#6b7280' }},
                    font: {{ color: '#9ca3af', size: 11, face: 'Outfit' }}
                }}
            }}
        }};

        const network = new vis.Network(container, data, options);

        function updateFilter() {{
            const hideNa = hideNaCheckbox.checked;
            const filteredNodes = [];
            const activeNodeIds = new Set();
            
            rawNodes.forEach(node => {{
                if (hideNa && node.group === "rule_na") {{
                    // Skip
                }} else {{
                    filteredNodes.push(node);
                    activeNodeIds.add(node.id);
                }}
            }});
            
            nodesDataset.clear();
            nodesDataset.add(filteredNodes);
            
            const filteredEdges = rawEdges.filter(edge => 
                activeNodeIds.has(edge.from) && activeNodeIds.has(edge.to)
            );
            edgesDataset.clear();
            edgesDataset.add(filteredEdges);
        }}

        hideNaCheckbox.addEventListener("change", updateFilter);
        // Apply filter initially
        updateFilter();

        // Details display helper
        function formatJson(obj) {{
            return JSON.stringify(obj, null, 2);
        }}

        function displayNodeDetails(data) {{
            const titleEl = document.getElementById("sd-title");
            const typeEl = document.getElementById("sd-type");
            const contentEl = document.getElementById("sd-content");
            
            titleEl.textContent = data.title || "Detail View";
            typeEl.textContent = data.type || "";
            
            let html = "";
            
            // Render specific layouts based on type
            if (data.type === "Claim Root") {{
                html += `
                    <div class="card">
                        <div class="card-title">Member Details</div>
                        <div class="detail-row"><span class="detail-key">Name</span><span class="detail-val">${{data.member_name}}</span></div>
                        <div class="detail-row"><span class="detail-key">Age</span><span class="detail-val">${{data.member_age}} years</span></div>
                    </div>
                    <div class="card">
                        <div class="card-title">Policy Terms</div>
                        <div class="detail-row"><span class="detail-key">Policy ID</span><span class="detail-val">${{data.policy_id}}</span></div>
                        <div class="detail-row"><span class="detail-key">Variant</span><span class="detail-val">${{data.variant}}</span></div>
                        <div class="detail-row"><span class="detail-key">Base Sum Insured</span><span class="detail-val">INR ${{data.base_sum_insured.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Assembled At</span><span class="detail-val" style="font-size:11px;">${{new Date(data.assembled_at).toLocaleString()}}</span></div>
                    </div>
                    <div class="card">
                        <div class="card-title">Financial Balances</div>
                        <div class="detail-row"><span class="detail-key">Base SI Remaining</span><span class="detail-val">INR ${{data.base_si_remaining.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Booster+ Wallet</span><span class="detail-val">INR ${{data.booster_plus_remaining.toLocaleString()}}</span></div>
                    </div>
                `;
            }} else if (data.type === "Line Item Root") {{
                let deductionsHtml = "";
                if (data.deductions && data.deductions.length > 0) {{
                    deductionsHtml = data.deductions.map(d => `
                        <div style="border-bottom:1px solid rgba(239, 68, 68, 0.2); padding: 5px 0; margin-bottom:5px;">
                            <div class="detail-row" style="font-weight:600; color:#f87171;">
                                <span>${{d.deduction_type.toUpperCase()}} (${{d.rule_id}})</span>
                                <span>- INR ${{d.amount.toFixed(2)}}</span>
                            </div>
                            <p style="font-size:12px; color:#94a3b8; margin-top:2px;">${{d.reason}}</p>
                        </div>
                    `).join("");
                }} else {{
                    deductionsHtml = `<p style="font-size:13px; color:#64748b;">No deductions applied.</p>`;
                }}

                html += `
                    <div class="card">
                        <div class="card-title">Line Item Totals</div>
                        <div class="detail-row"><span class="detail-key">Claimed</span><span class="detail-val">INR ${{data.claimed_amount.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Admissible</span><span class="detail-val">INR ${{data.admissible_amount.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Payable</span><span class="detail-val" style="color:#34d399; font-weight:600;">INR ${{data.payable_amount.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Status</span><span class="detail-val">${{data.decision}}</span></div>
                    </div>
                    <div class="card">
                        <div class="card-title">Deductions Detail</div>
                        ${{deductionsHtml}}
                    </div>
                `;
            }} else if (data.type.includes("Rule")) {{
                const statusClass = data.evaluation === "PASSED" ? "passed" : 
                                    (data.evaluation.includes("FAIL") || data.evaluation.includes("EXCL")) ? "failed" : "warning";
                
                // Extract <think> block from raw LLM response if present
                let thinkHtml = "";
                if (data.raw_llm_response) {{
                    const raw = data.raw_llm_response;
                    let thinkText = null;
                    // Match <think>...</think> or <|think|>...</|think|>
                    const m1 = raw.match(/<think>([\\s\\S]*?)<\\/think>/i);
                    const m2 = raw.match(/<\\|think\\|>([\\s\\S]*?)<\\/\\|think\\|>/i);
                    if (m1) thinkText = m1[1].trim();
                    else if (m2) thinkText = m2[1].trim();
                    if (thinkText) {{
                        const escaped = thinkText
                            .replace(/&/g, "&amp;")
                            .replace(/</g, "&lt;")
                            .replace(/>/g, "&gt;");
                        thinkHtml = `
                            <details class="think-block">
                                <summary class="think-summary">
                                    <span style="font-size:13px;">&#x1F9E0;</span>
                                    AI Reasoning Chain (Raw Think Block)
                                </summary>
                                <pre class="think-pre">${{escaped}}</pre>
                            </details>
                        `;
                    }}
                }}
                
                html += `
                    <div class="card">
                        <div class="card-title">Evaluation Summary</div>
                        <div class="detail-row"><span class="detail-key">Gate</span><span class="detail-val">${{data.gate}}</span></div>
                        <div class="detail-row"><span class="detail-key">Evaluation</span><span class="detail-val" style="font-weight:600;">${{data.evaluation}}</span></div>
                        <div class="detail-row"><span class="detail-key">AI Confidence</span><span class="detail-val">${{data.confidence.toFixed(2)}}</span></div>
                        ${{data.source_section ? `<div class="detail-row"><span class="detail-key">Policy Source</span><span class="detail-val">Section ${{data.source_section}} (Page ${{data.source_page}})</span></div>` : ""}}
                    </div>
                    <div class="card">
                        <div class="card-title">Reasoning Trace</div>
                        <div class="reason-box ${{statusClass}}">${{data.reason}}</div>
                    </div>
                    ${{thinkHtml}}
                    <div class="card">
                        <div class="card-title">Rule Inputs</div>
                        <pre><code>${{formatJson(data.inputs)}}</code></pre>
                    </div>
                `;
            }} else if (data.type === "Final Decision Composer") {{
                let reasonHtml = "";
                if (data.review_reasons && data.review_reasons.length > 0) {{
                    reasonHtml = `
                        <div class="card" style="border: 1px solid #fbbf24; background: rgba(245, 158, 11, 0.05);">
                            <div class="card-title" style="color:#fbbf24; border-bottom: 1px solid rgba(245, 158, 11, 0.2);">Manual Review Flags</div>
                            <ul style="padding-left:15px; font-size:13px; color:#fbbf24; line-height:1.5;">
                                ${{data.review_reasons.map(r => `<li>${{r}}</li>`).join("")}}
                            </ul>
                        </div>
                    `;
                }}

                html += `
                    ${{reasonHtml}}
                    <div class="card">
                        <div class="card-title">Financial Breakdown</div>
                        <div class="detail-row"><span class="detail-key">Total Claimed</span><span class="detail-val">INR ${{data.total_claimed.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Total Admissible</span><span class="detail-val">INR ${{data.total_admissible.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Total Deductions</span><span class="detail-val" style="color:#f87171;">INR ${{data.total_deductions.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Total Payable</span><span class="detail-val" style="color:#34d399; font-weight:600; font-size:16px;">INR ${{data.total_payable.toLocaleString()}}</span></div>
                    </div>
                    <div class="card">
                        <div class="card-title">Deductions Breakdown</div>
                        <div class="detail-row"><span class="detail-key">Room Rent Pro-rata</span><span class="detail-val">INR ${{data.deduction_breakdown.room_pro_rata.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Co-Payment</span><span class="detail-val">INR ${{data.deduction_breakdown.co_payment.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Deductibles</span><span class="detail-val">INR ${{data.deduction_breakdown.deductible.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Non-Payable items</span><span class="detail-val">INR ${{data.deduction_breakdown.non_payable_items.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Sublimits Cap</span><span class="detail-val">INR ${{data.deduction_breakdown.sublimits.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Special Penalties</span><span class="detail-val">INR ${{data.deduction_breakdown.penalties.toLocaleString()}}</span></div>
                    </div>
                    <div class="card">
                        <div class="card-title">Sum Insured Consumption Waterfall</div>
                        <div class="detail-row"><span class="detail-key">Paid from Base SI</span><span class="detail-val">INR ${{data.waterfall.amount_from_base_si.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Paid from Booster+</span><span class="detail-val">INR ${{data.waterfall.amount_from_booster.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Paid from ReAssure Forever</span><span class="detail-val">INR ${{data.waterfall.amount_from_forever.toLocaleString()}}</span></div>
                        <div class="detail-row"><span class="detail-key">Shortfall (Uncovered)</span><span class="detail-val" style="color:#f87171; font-weight:500;">INR ${{data.waterfall.shortfall.toLocaleString()}}</span></div>
                    </div>
                `;
            }} else {{
                html += `
                    <div class="card">
                        <div class="card-title">General Info</div>
                        <div class="detail-row"><span class="detail-key">Title</span><span class="detail-val">${{data.title || ""}}</span></div>
                        <p style="font-size:14px; color:#94a3b8; margin-top:10px; line-height:1.5;">${{data.description || ""}}</p>
                    </div>
                `;
            }}
            
            contentEl.innerHTML = html;
        }}

        network.on("click", function (params) {{
            if (params.nodes.length > 0) {{
                const nodeId = params.nodes[0];
                const nodeVal = nodesData[nodeId];
                if (nodeVal) {{
                    displayNodeDetails(nodeVal);
                }}
            }}
        }});
        
        // Auto select final node initially
        setTimeout(() => {{
            network.selectNodes(["final_decision_node"]);
            const val = nodesData["final_decision_node"];
            if (val) {{
                displayNodeDetails(val);
            }}
        }}, 500);
    </script>
</body>
</html>
"""
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_template)
        
    return filename
