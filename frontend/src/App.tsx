import { useState } from 'react';
import {
  Sparkles, Activity, Shield, ShieldCheck, ShieldAlert,
  ChevronDown, ChevronRight, Plus, Trash2, Cpu, DollarSign, Wallet,
  RefreshCw, AlertTriangle, AlertCircle, Clock, BookOpen, User, Building2
} from 'lucide-react';

import { useClaimContext } from './hooks/useClaimContext';
import { useAdjudication } from './hooks/useAdjudication';





// MAIN APP COMPONENT
// ============================================================================

export default function App() {
  const {
    context, activePreset, endType, endVal,
    setEndType, setEndVal,
    handlePresetChange, updatePolicy, updateMember,
    updateLiveHealthy, updateCashBagPlus,
    updateBenefitBalance, toggleRenewalSimulation,
    updateLineItem,
    addEndorsement, removeEndorsement,
  } = useClaimContext();

  const { decision, loading, error, submit } = useAdjudication();

  // UI Accordion States
  const [openSection, setOpenSection] = useState<string>('policy');

  const handleAdjudicate = () => {
    void submit(context);
  };

  const handleAddEndorsement = () => {
    const ok = addEndorsement();
    if (!ok) alert('Invalid JSON details. Please fix before adding.');
  };



  // Helper formatting utility (strictly styled float precision)
  const formatCurrency = (val: number | undefined) => {
    if (val === undefined) return '₹0.00';
    return `₹${Number(val).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  // Utility to gracefully strip or isolate model thinking tracks in the UI
  const parseReasoningText = (text: string): { thinking: string; cleanText: string } => {
    const thinkRegex = /<(?:\|thought\||think)>([\s\S]*?)<\/(?:\|thought\||think)>/i;
    const match = text.match(thinkRegex);
    
    if (match) {
      return {
        thinking: match[1].trim(),
        cleanText: text.replace(thinkRegex, '').trim()
      };
    }
    return { thinking: '', cleanText: text };
  };

  // Find reasoning trace values in decision trace logs
  const reasoningTraces = decision?.decision_trace.filter(
    t => t.gate.includes('validation') || t.gate === 'waiting_period_validation'
  ) || [];


  return (
    <div className="min-h-screen bg-bg-oled bg-mesh relative flex flex-col font-sans">
      <div className="noise-overlay" />
      
      {/* HEADER SECTION */}
      <header className="px-8 py-6 flex flex-col md:flex-row md:items-center justify-between gap-6 border-b border-white/5 bg-black/40 backdrop-blur-md z-10">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="bg-indigo-500/10 border border-indigo-500/30 text-indigo-400 rounded-full px-2.5 py-0.5 text-[10px] uppercase tracking-wider font-semibold">
              Adjudication Copilot v2.1
            </span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white font-display">
            ReAssure 3.0 Auto-Adjudication Engine
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Core validation pipeline and mathematical invariant testing dashboard.
          </p>
        </div>

        {/* DEMO CASE SELECTOR */}
        <div className="flex items-center gap-3">
          <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            Demo Scenario:
          </label>
          <div className="relative outer-shell bg-white/5 border border-white/10 rounded-full p-1 flex items-center">
            <select
              value={activePreset}
              onChange={(e) => handlePresetChange(e.target.value)}
              className="bg-vanta-black text-white text-xs rounded-full py-1.5 px-4 pr-8 border border-white/5 focus:outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer appearance-none font-medium"
            >
              <option value="case1">Case 1: Standard Inpatient Appendectomy - Pro-Rata Breach</option>
              <option value="case2">Case 2: 3-Year Lock the Clock Multi-Tenure Delta</option>
              <option value="case3">Case 3: Cash-Bag+ wellness Points conversion</option>
            </select>
            <ChevronDown className="w-3.5 h-3.5 absolute right-4 text-slate-400 pointer-events-none" />
          </div>
        </div>
      </header>

      {/* CORE WORKSPACE GRID */}
      <main className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-8 p-8 max-w-7xl mx-auto w-full z-10 min-h-0">
        
        {/* LEFT PANEL: INGESTION HUB */}
        <section className="flex flex-col gap-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Cpu className="w-4 h-4 text-indigo-400" />
              <h2 className="text-base font-semibold text-slate-200 font-display">
                Ingestion & Transaction Hub
              </h2>
            </div>
            <span className="text-[10px] uppercase tracking-wider text-slate-500 font-medium">
              Payload Configuration
            </span>
          </div>

          <div className="flex flex-col gap-4">
            
            {/* POLICY DETAILS ACCORDION */}
            <div className="outer-shell bg-white/5 border border-white/10 rounded-[1.5rem] p-1 overflow-hidden transition-all duration-300">
              <button 
                onClick={() => setOpenSection(openSection === 'policy' ? '' : 'policy')}
                className="w-full flex items-center justify-between p-4 bg-vanta-black rounded-[calc(1.5rem-0.25rem)] hover:bg-vanta-gray transition-smooth group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
                    <BookOpen className="w-4 h-4" />
                  </div>
                  <div className="text-left">
                    <p className="text-xs font-semibold text-white">Policy Details</p>
                    <p className="text-[10px] text-slate-400">ID: {context.policy.policy_id} | {context.policy.variant}</p>
                  </div>
                </div>
                {openSection === 'policy' ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
              </button>
              
              {openSection === 'policy' && (
                <div className="p-5 grid grid-cols-2 gap-4 bg-vanta-black/40 border-t border-white/5 mt-1 rounded-b-[calc(1.5rem-0.25rem)]">
                  <div>
                    <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Policy ID</label>
                    <input 
                      type="text" 
                      value={context.policy.policy_id}
                      onChange={(e) => updatePolicy('policy_id', e.target.value)}
                      className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Policy Variant</label>
                    <select 
                      value={context.policy.variant}
                      onChange={(e) => updatePolicy('variant', e.target.value)}
                      className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    >
                      <option value="Classic">Classic</option>
                      <option value="Select">Select</option>
                      <option value="Elite">Elite</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Term Years</label>
                    <select 
                      value={context.policy.policy_term_years || 1}
                      onChange={(e) => updatePolicy('policy_term_years', Number(e.target.value))}
                      className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    >
                      <option value={1}>1 Year</option>
                      <option value={2}>2 Years</option>
                      <option value={3}>3 Years</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Base Sum Insured</label>
                    <input 
                      type="number" 
                      value={context.policy.base_sum_insured}
                      onChange={(e) => updatePolicy('base_sum_insured', Number(e.target.value))}
                      className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    />
                  </div>
                  <div className="col-span-2 flex items-center justify-between bg-white/5 rounded-xl p-3 border border-white/5 mt-2">
                    <div>
                      <p className="text-xs font-semibold text-white">Simulate EOY Renewal</p>
                      <p className="text-[10px] text-slate-400">Accrue and convert wellness points to wallet credit</p>
                    </div>
                    <button
                      onClick={toggleRenewalSimulation}
                      className={`w-10 h-6 rounded-full p-1 transition-smooth ${context.renewal_event_simulation ? 'bg-indigo-500' : 'bg-slate-700'}`}
                    >
                      <div className={`w-4 h-4 bg-white rounded-full transition-smooth ${context.renewal_event_simulation ? 'translate-x-4' : 'translate-x-0'}`} />
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* MEMBER DETAILS ACCORDION */}
            <div className="outer-shell bg-white/5 border border-white/10 rounded-[1.5rem] p-1 overflow-hidden transition-all duration-300">
              <button 
                onClick={() => setOpenSection(openSection === 'member' ? '' : 'member')}
                className="w-full flex items-center justify-between p-4 bg-vanta-black rounded-[calc(1.5rem-0.25rem)] hover:bg-vanta-gray transition-smooth group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
                    <User className="w-4 h-4" />
                  </div>
                  <div className="text-left">
                    <p className="text-xs font-semibold text-white">Member & Lifetime State</p>
                    <p className="text-[10px] text-slate-400">Name: {context.member.name} | Age: {context.member.age}</p>
                  </div>
                </div>
                {openSection === 'member' ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
              </button>
              
              {openSection === 'member' && (
                <div className="p-5 bg-vanta-black/40 border-t border-white/5 mt-1 rounded-b-[calc(1.5rem-0.25rem)] flex flex-col gap-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Member Name</label>
                      <input 
                        type="text" 
                        value={context.member.name}
                        onChange={(e) => updateMember('name', e.target.value)}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Age</label>
                      <input 
                        type="number" 
                        value={context.member.age}
                        onChange={(e) => updateMember('age', Number(e.target.value))}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Entry Age</label>
                      <input 
                        type="number" 
                        value={context.member.entry_age}
                        onChange={(e) => updateMember('entry_age', Number(e.target.value))}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Live Healthy points</label>
                      <input 
                        type="number" 
                        value={context.lifetime_state.live_healthy.current_points}
                        onChange={(e) => updateLiveHealthy('current_points', Number(e.target.value))}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                      />
                    </div>
                  </div>
                  
                  <div className="grid grid-cols-2 gap-4 border-t border-white/5 pt-4 mt-2">
                    <div>
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Cash-Bag+ Wallet Balance</label>
                      <div className="relative">
                        <DollarSign className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input 
                          type="number" 
                          value={context.lifetime_state.cash_bag_plus.balance}
                          onChange={(e) => updateCashBagPlus('balance', Number(e.target.value))}
                          className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 pl-8 pr-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">YTD Deductible Consumed</label>
                      <input 
                        type="number" 
                        value={context.benefit_balance.deductible_consumed_ytd ?? 0}
                        onChange={(e) => updateBenefitBalance('deductible_consumed_ytd', Number(e.target.value))}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* ENDORSEMENTS TIMELINE ACCORDION */}
            <div className="outer-shell bg-white/5 border border-white/10 rounded-[1.5rem] p-1 overflow-hidden transition-all duration-300">
              <button 
                onClick={() => setOpenSection(openSection === 'endorsement' ? '' : 'endorsement')}
                className="w-full flex items-center justify-between p-4 bg-vanta-black rounded-[calc(1.5rem-0.25rem)] hover:bg-vanta-gray transition-smooth group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-purple-500/10 border border-purple-500/20 flex items-center justify-center text-purple-400">
                    <Clock className="w-4 h-4" />
                  </div>
                  <div className="text-left">
                    <p className="text-xs font-semibold text-white">Endorsement Timeline Builder</p>
                    <p className="text-[10px] text-slate-400">Active Mid-Term Mutations: {context.endorsements.length}</p>
                  </div>
                </div>
                {openSection === 'endorsement' ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
              </button>
              
              {openSection === 'endorsement' && (
                <div className="p-5 bg-vanta-black/40 border-t border-white/5 mt-1 rounded-b-[calc(1.5rem-0.25rem)] flex flex-col gap-4">
                  {/* Timeline listing */}
                  {context.endorsements.length > 0 ? (
                    <div className="flex flex-col gap-2.5">
                      {context.endorsements.map((end) => (
                        <div key={end.endorsement_id} className="flex items-start justify-between p-3 bg-white/5 rounded-xl border border-white/5">
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="text-xs font-bold text-white">{end.endorsement_type}</span>
                              <span className="text-[9px] uppercase tracking-wider bg-purple-500/20 text-purple-300 px-1.5 py-0.5 rounded">
                                {end.endorsement_id}
                              </span>
                            </div>
                            <pre className="text-[10px] text-indigo-300 font-mono mt-1 overflow-x-auto max-w-[300px]">
                              {JSON.stringify(end.details, null, 2)}
                            </pre>
                          </div>
                          <button 
                            onClick={() => removeEndorsement(end.endorsement_id)}
                            className="p-1 hover:bg-red-500/10 rounded text-slate-400 hover:text-red-400 transition-smooth"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-6 border border-dashed border-white/10 rounded-xl text-slate-500 text-xs">
                      No mid-term endorsements active in this context.
                    </div>
                  )}

                  {/* Add Endorsement Builder */}
                  <div className="border-t border-white/5 pt-4 flex flex-col gap-3">
                    <p className="text-xs font-semibold text-slate-300">Append Context Mutation</p>
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-[9px] uppercase tracking-wider text-slate-400 mb-1 font-semibold">Type</label>
                        <select 
                          value={endType}
                          onChange={(e) => setEndType(e.target.value)}
                          className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none"
                        >
                          <option value="MemberAddition">Member Addition</option>
                          <option value="SIEnhancement">SI Enhancement</option>
                          <option value="IndividualToFloater">Individual To Floater</option>
                          <option value="FloaterSplit">Floater Split</option>
                        </select>
                      </div>
                      <div className="flex items-end">
                        <button
                          onClick={handleAddEndorsement}
                          className="w-full bg-indigo-500 hover:bg-indigo-600 active:scale-[0.98] text-white font-medium text-xs py-2 px-4 rounded-lg flex items-center justify-center gap-1.5 transition-smooth"
                        >
                          <Plus className="w-3.5 h-3.5" /> Add Mutation
                        </button>
                      </div>
                    </div>
                    <div>
                      <label className="block text-[9px] uppercase tracking-wider text-slate-400 mb-1 font-semibold">JSON Parameters Details</label>
                      <textarea
                        value={endVal}
                        onChange={(e) => setEndVal(e.target.value)}
                        rows={4}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-[11px] font-mono text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* HOSPITAL LINE ITEM ACCORDION */}
            <div className="outer-shell bg-white/5 border border-white/10 rounded-[1.5rem] p-1 overflow-hidden transition-all duration-300">
              <button 
                onClick={() => setOpenSection(openSection === 'item' ? '' : 'item')}
                className="w-full flex items-center justify-between p-4 bg-vanta-black rounded-[calc(1.5rem-0.25rem)] hover:bg-vanta-gray transition-smooth group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400">
                    <Building2 className="w-4 h-4" />
                  </div>
                  <div className="text-left">
                    <p className="text-xs font-semibold text-white">Hospital Line Item</p>
                    <p className="text-[10px] text-slate-400">Claimed: {formatCurrency(context.line_items[0]?.claimed_amount)} | Rent: {formatCurrency(context.line_items[0]?.actual_room_rent ?? undefined)}</p>
                  </div>
                </div>
                {openSection === 'item' ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
              </button>
              
              {openSection === 'item' && (
                <div className="p-5 bg-vanta-black/40 border-t border-white/5 mt-1 rounded-b-[calc(1.5rem-0.25rem)] flex flex-col gap-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="col-span-2">
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Treatment Diagnosis Description</label>
                      <input 
                        type="text" 
                        value={context.line_items[0]?.description}
                        onChange={(e) => updateLineItem(0, 'description', e.target.value)}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Condition Diagnosed</label>
                      <input 
                        type="text" 
                        value={context.line_items[0]?.condition_diagnosed}
                        onChange={(e) => updateLineItem(0, 'condition_diagnosed', e.target.value)}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Claimed Amount</label>
                      <input 
                        type="number" 
                        value={context.line_items[0]?.claimed_amount}
                        onChange={(e) => updateLineItem(0, 'claimed_amount', Number(e.target.value))}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Actual Room Rent (Per day)</label>
                      <input 
                        type="number" 
                        value={context.line_items[0]?.actual_room_rent ?? ""}
                        onChange={(e) => updateLineItem(0, 'actual_room_rent', Number(e.target.value))}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] uppercase tracking-wider text-slate-400 mb-1.5 font-semibold">Claimed Room Category</label>
                      <input 
                        type="text" 
                        value={context.line_items[0]?.room_category_claimed || ""}
                        onChange={(e) => updateLineItem(0, 'room_category_claimed', e.target.value)}
                        className="w-full bg-vanta-black border border-white/5 rounded-lg py-1.5 px-3 text-xs text-white focus:outline-none"
                      />
                    </div>
                  </div>

                  <div className="border-t border-white/5 pt-4 mt-2">
                    <p className="text-[10px] uppercase tracking-wider text-slate-400 mb-3 font-semibold">Active Bill Itemisation Breakdown</p>
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-[9px] uppercase tracking-wider text-slate-500 mb-1 font-medium">Room Charges</label>
                        <input 
                          type="number" 
                          value={context.line_items[0]?.room_charges || 0}
                          onChange={(e) => updateLineItem(0, 'room_charges', Number(e.target.value))}
                          className="w-full bg-vanta-black border border-white/5 rounded-lg py-1 px-2.5 text-xs text-white focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[9px] uppercase tracking-wider text-slate-500 mb-1 font-medium">Nursing Charges</label>
                        <input 
                          type="number" 
                          value={context.line_items[0]?.nursing_charges || 0}
                          onChange={(e) => updateLineItem(0, 'nursing_charges', Number(e.target.value))}
                          className="w-full bg-vanta-black border border-white/5 rounded-lg py-1 px-2.5 text-xs text-white focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[9px] uppercase tracking-wider text-slate-500 mb-1 font-medium">Practitioner Fees</label>
                        <input 
                          type="number" 
                          value={context.line_items[0]?.medical_practitioner_fees || 0}
                          onChange={(e) => updateLineItem(0, 'medical_practitioner_fees', Number(e.target.value))}
                          className="w-full bg-vanta-black border border-white/5 rounded-lg py-1 px-2.5 text-xs text-white focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[9px] uppercase tracking-wider text-slate-500 mb-1 font-medium">OT Charges</label>
                        <input 
                          type="number" 
                          value={context.line_items[0]?.ot_charges || 0}
                          onChange={(e) => updateLineItem(0, 'ot_charges', Number(e.target.value))}
                          className="w-full bg-vanta-black border border-white/5 rounded-lg py-1 px-2.5 text-xs text-white focus:outline-none"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

          </div>

          {/* ACTION BUTTON */}
          <div className="mt-4 flex items-center justify-center">
            <button
              onClick={handleAdjudicate}
              disabled={loading}
              className="w-full bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white font-medium text-sm py-4 px-6 rounded-xl shadow-lg hover:shadow-indigo-500/20 active:scale-[0.98] transition-smooth flex items-center justify-center gap-2 border border-white/10"
            >
              {loading ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" /> Adjudicating payload...
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4" /> Adjudicate Claim
                </>
              )}
            </button>
          </div>
        </section>

        {/* RIGHT PANEL: EXECUTION TRANSPARENCY ENGINE */}
        <section className="flex flex-col gap-6 relative min-h-[500px]">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-violet-400" />
              <h2 className="text-base font-semibold text-slate-200 font-display">
                Execution & Adjudication Engine
              </h2>
            </div>
            <span className="text-[10px] uppercase tracking-wider text-slate-500 font-medium">
              Real-time Output
            </span>
          </div>

          {/* GLOBAL SPINNER LOADING */}
          {loading && (
            <div className="absolute inset-0 bg-black/60 rounded-3xl backdrop-blur-md flex flex-col items-center justify-center gap-4 z-20 transition-smooth">
              <div className="outer-shell bg-white/5 border border-white/10 p-4 rounded-full flex items-center justify-center">
                <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin" />
              </div>
              <p className="text-xs font-semibold text-slate-300">Auto-Adjudicating rules and mathematical matrices...</p>
            </div>
          )}

          {error && (
            <div className="p-5 bg-red-500/10 border border-red-500/30 text-red-300 rounded-2xl flex items-start gap-3">
              <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-xs font-bold">API Gateway Error</p>
                <p className="text-[11px] text-red-400 mt-1">{error}</p>
              </div>
            </div>
          )}

          {!decision && !error && !loading && (
            <div className="flex-1 flex flex-col items-center justify-center text-center p-8 border border-dashed border-white/5 rounded-3xl bg-vanta-black/10">
              <div className="w-12 h-12 bg-white/5 border border-white/10 rounded-2xl flex items-center justify-center text-slate-500 mb-4">
                <Activity className="w-6 h-6" />
              </div>
              <h3 className="text-sm font-semibold text-slate-300 font-display">Awaiting Adjudication Transaction</h3>
              <p className="text-[11px] text-slate-500 mt-1 max-w-[280px]">
                Click "Adjudicate Claim" to ingest the context and trigger the 7-Gate validator engine.
              </p>
            </div>
          )}

          {decision && !loading && (
            <div className="flex flex-col gap-6">
              
              {/* THE ADJUDICATION BANNER */}
              <div className={`p-4 rounded-2xl border flex items-center justify-between transition-smooth ${
                decision.claim_decision === 'APPROVED' ? 'bg-emerald-950/20 border-emerald-500/30 text-emerald-300' :
                decision.claim_decision === 'PARTIALLY_APPROVED' ? 'bg-amber-950/20 border-amber-500/30 text-amber-300' :
                decision.claim_decision === 'REJECTED' ? 'bg-red-950/20 border-red-500/30 text-red-300' :
                decision.claim_decision === 'ASSISTED_REVIEW' ? 'bg-amber-950/20 border-amber-500/30 text-amber-300' :
                decision.claim_decision === 'MEDICAL_REVIEW' ? 'bg-purple-950/20 border-purple-500/30 text-purple-300' :
                'bg-rose-950/20 border-rose-500/30 text-rose-300'
              }`}>
                <div>
                  <div className="flex items-center gap-1.5">
                    {decision.claim_decision === 'APPROVED' ? <ShieldCheck className="w-4 h-4" /> :
                     decision.claim_decision === 'REJECTED' ? <ShieldAlert className="w-4 h-4" /> :
                     <Shield className="w-4 h-4 animate-pulse" />}
                    <span className="text-[10px] uppercase tracking-wider font-bold">Adjudication Decision</span>
                  </div>
                  <h3 className="text-xl font-bold tracking-tight mt-0.5 font-display">{decision.claim_decision}</h3>
                </div>
                <div className="text-right">
                  <span className="text-[10px] uppercase tracking-wider text-slate-400 block font-semibold">Total Payable</span>
                  <p className="text-lg font-bold font-mono text-white">{formatCurrency(decision.total_payable)}</p>
                </div>
              </div>

              {/* MANUAL REVIEW REASONS CARD */}
              {decision.manual_review_required && decision.review_reasons && decision.review_reasons.length > 0 && (
                <div className="p-4 bg-amber-950/10 border border-amber-500/20 rounded-2xl flex flex-col gap-2 animate-pulse">
                  <div className="flex items-center gap-2 text-amber-400 font-semibold text-xs uppercase tracking-wider">
                    <AlertTriangle className="w-4 h-4 animate-bounce" />
                    <span>Review Flags ({decision.claim_decision})</span>
                  </div>
                  <ul className="list-disc pl-5 text-[11px] text-slate-300 space-y-1">
                    {decision.review_reasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* TELEMETRY BADGES CONTAINER */}
              {(decision.deduction_breakdown.lock_the_clock_premium_delta > 0 || decision.decision_trace.some(t => t.rule_id === "CASH_BAG_PLUS_ACCRUAL")) && (
                <div className="flex flex-col gap-3">
                  
                  {/* Lock the Clock Telemetry Banner */}
                  {decision.deduction_breakdown.lock_the_clock_premium_delta > 0 && (
                    <div className="p-3 bg-red-950/10 border border-red-500/20 rounded-xl flex items-start gap-2.5">
                      <AlertTriangle className="w-4 h-4 text-red-400 mt-0.5 animate-pulse" />
                      <div>
                        <p className="text-xs font-semibold text-white">Lock the Clock Premium Delta Deducted</p>
                        <p className="text-[10px] text-slate-400 mt-0.5">
                          Lock the Clock Recalculation: <span className="text-red-300 font-semibold font-mono">{formatCurrency(decision.deduction_breakdown.lock_the_clock_premium_delta)}</span> deducted from final payout for multi-tenure adjustment.
                        </p>
                      </div>
                    </div>
                  )}

                  {/* Cash-Bag+ Telemetry Card */}
                  {decision.decision_trace.some(t => t.rule_id === "CASH_BAG_PLUS_ACCRUAL") && (
                    <div className="p-4 bg-emerald-950/10 border border-emerald-500/20 rounded-2xl flex items-center justify-between gap-4">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
                          <Wallet className="w-4 h-4" />
                        </div>
                        <div>
                          <p className="text-xs font-semibold text-white">Cash-Bag+ Wallet Accrual</p>
                          <p className="text-[10px] text-slate-400 mt-0.5">
                            Wellness Points Converted! 2,800 points converted to <span className="text-emerald-300 font-semibold font-mono">₹700.00</span> Cash-Bag+ wallet credit.
                          </p>
                        </div>
                      </div>
                      <span className="text-xs font-mono font-bold text-emerald-300 bg-emerald-500/10 px-2 py-1 rounded-lg">
                        +₹700.00
                      </span>
                    </div>
                  )}

                </div>
              )}

              {/* LIVE THINKING TERMINAL */}
              <div className="outer-shell bg-white/5 border border-white/10 rounded-2xl p-1 overflow-hidden">
                <div className="bg-black/90 font-mono text-xs p-4 rounded-[calc(2rem-0.75rem)] flex flex-col gap-2 min-h-[160px] border border-white/5 shadow-inner">
                  <div className="flex items-center justify-between border-b border-white/5 pb-2 mb-1">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full bg-violet-400 animate-pulse" />
                      <span className="text-[10px] uppercase tracking-wider text-slate-400 font-bold">Semantic Agent Reasoning Terminal</span>
                    </div>
                    <span className="text-[9px] uppercase tracking-wider text-slate-600 font-medium">gemma4-e4b-qat</span>
                  </div>
                  
                  <div className="flex-1 flex flex-col gap-2 overflow-y-auto max-h-[220px] pr-2">
                    <p className="text-[10px] text-slate-500">{"[SYS] Loaded LLM template: <|turn>system <|think|>..."}</p>
                    <p className="text-[10px] text-slate-500">{"[SYS] Executing semantic rules validation..."}</p>
                    
                    {reasoningTraces.length > 0 ? (
                      reasoningTraces.map((trace, index) => {
                        const { thinking, cleanText } = parseReasoningText(trace.reason);
                        return (
                          <div key={index} className="flex flex-col gap-1 border-t border-white/5 pt-2 mt-1 first:border-0 first:pt-0 first:mt-0">
                            <div className="flex items-center gap-2">
                              <span className="text-[9px] uppercase text-violet-400 font-bold">{trace.rule_id}</span>
                              <span className={`text-[8px] px-1 py-0.2 rounded font-semibold ${trace.evaluation === 'PASSED' ? 'bg-emerald-950 text-emerald-400' : 'bg-red-950 text-red-400'}`}>
                                {trace.evaluation}
                              </span>
                              {trace.confidence !== undefined && (
                                <span className="text-[8px] text-slate-500">
                                  {(trace.confidence * 100).toFixed(0)}% conf
                                </span>
                              )}
                            </div>
                            {thinking && (
                              <details className="text-[10px] text-slate-500 pl-2 border-l border-white/10 mt-0.5 cursor-pointer select-none">
                                <summary className="hover:text-slate-400 transition-smooth">View Thinking Process...</summary>
                                <div className="mt-1 pl-2 border-l border-dashed border-white/5 whitespace-pre-wrap font-mono text-[9px] text-slate-600 bg-white/2 p-2 rounded">
                                  {thinking}
                                </div>
                              </details>
                            )}
                            <p className="text-[11px] text-slate-300 leading-relaxed pl-2 border-l border-white/10 italic">
                              {cleanText}
                            </p>
                          </div>
                        );
                      })
                    ) : (
                      <p className="text-[11px] text-slate-400 italic">No semantic agent reasoning traces generated for this claim.</p>
                    )}
                  </div>
                </div>
              </div>

              {/* GATE 6 WATERFALL COMPONENT */}
              <div className="outer-shell bg-white/5 border border-white/10 rounded-[2.5rem] p-1.5 overflow-hidden">
                <div className="p-6 bg-vanta-black rounded-[calc(2.5rem-0.375rem)] flex flex-col gap-4">
                  <div className="flex items-center justify-between border-b border-white/5 pb-3">
                    <h4 className="text-xs font-bold text-white uppercase tracking-wider font-display">Gate 6: Financial Computation Waterfall</h4>
                    <span className="text-[10px] text-slate-500 font-mono">Deduction breakdown</span>
                  </div>

                  <div className="flex flex-col gap-3 relative before:absolute before:left-3.5 before:top-2 before:bottom-2 before:w-[1px] before:bg-white/5">
                    
                    {/* Item 0: Claimed Amount */}
                    <div className="flex items-center justify-between pl-8 relative">
                      <div className="absolute left-2 w-3.5 h-3.5 rounded-full bg-slate-800 border-2 border-slate-600 flex items-center justify-center text-[8px] font-bold text-white">
                        0
                      </div>
                      <span className="text-xs text-slate-300">Initial Claimed Amount</span>
                      <span className="text-xs font-bold font-mono text-white">{formatCurrency(decision.total_claimed)}</span>
                    </div>

                    {/* Item 1: Non-Payable */}
                    <div className="flex items-center justify-between pl-8 relative">
                      <div className="absolute left-2 w-3.5 h-3.5 rounded-full bg-slate-800 border-2 border-slate-600 flex items-center justify-center text-[8px] font-bold text-white">
                        1
                      </div>
                      <span className="text-xs text-slate-400">Step 0: Non-Payables Excluded</span>
                      <span className="text-xs font-medium font-mono text-red-400">-{formatCurrency(decision.deduction_breakdown.non_payable_items)}</span>
                    </div>

                    {/* Item 2: Room Pro-Rata */}
                    <div className="flex items-center justify-between pl-8 relative">
                      <div className="absolute left-2 w-3.5 h-3.5 rounded-full bg-slate-800 border-2 border-slate-600 flex items-center justify-center text-[8px] font-bold text-white">
                        2
                      </div>
                      <span className="text-xs text-slate-400">Step 1: Room Rent Pro-Rata Deduction (Tool 2)</span>
                      <span className="text-xs font-medium font-mono text-red-400">-{formatCurrency(decision.deduction_breakdown.room_pro_rata)}</span>
                    </div>

                    {/* Item 3: Deductible */}
                    <div className="flex items-center justify-between pl-8 relative">
                      <div className="absolute left-2 w-3.5 h-3.5 rounded-full bg-slate-800 border-2 border-slate-600 flex items-center justify-center text-[8px] font-bold text-white">
                        3
                      </div>
                      <span className="text-xs text-slate-400">Step 4: Annual Deductible Applied (Tool 4)</span>
                      <span className="text-xs font-medium font-mono text-red-400">-{formatCurrency(decision.deduction_breakdown.deductible)}</span>
                    </div>

                    {/* Item 4: Co-Pay */}
                    <div className="flex items-center justify-between pl-8 relative">
                      <div className="absolute left-2 w-3.5 h-3.5 rounded-full bg-slate-800 border-2 border-slate-600 flex items-center justify-center text-[8px] font-bold text-white">
                        4
                      </div>
                      <span className="text-xs text-slate-400">Step 5: Stacked Co-Payment Applied (Tool 3)</span>
                      <span className="text-xs font-medium font-mono text-red-400">-{formatCurrency(decision.deduction_breakdown.co_payment)}</span>
                    </div>

                    {/* Item 5: Sum Insured depletion */}
                    <div className="flex items-center justify-between pl-8 relative border-t border-white/5 pt-3 mt-1">
                      <div className="absolute left-2 w-3.5 h-3.5 rounded-full bg-indigo-500 flex items-center justify-center text-[8px] font-bold text-white">
                        ✓
                      </div>
                      <span className="text-xs font-bold text-indigo-300">Payable Payout (Gate 6 Waterfall)</span>
                      <span className="text-sm font-bold font-mono text-indigo-400">{formatCurrency(decision.total_payable)}</span>
                    </div>

                  </div>
                </div>
              </div>

            </div>
          )}

        </section>

      </main>

      {/* FOOTER */}
      <footer className="py-8 text-center text-slate-600 border-t border-white/5 bg-black/20 text-xs mt-12">
        <p>© 2026 Niva Bupa Health Insurance. AI-First Auto-Adjudication Copilot Console.</p>
      </footer>
    </div>
  );
}
