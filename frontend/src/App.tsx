import { useState, useEffect, useRef, useCallback } from 'react';
import { useClaimContext } from './hooks/useClaimContext';
import { useAdjudication } from './hooks/useAdjudication';
import { getClaimSummary, extractDocument } from './services/adjudicationApi';
import type { DocumentExtractionResult } from './types/claims';

// TRANSLATION DICTIONARY FOR TECHNICAL CODES
const RULE_DEFINITIONS: Record<string, { title: string; description: string }> = {
  GATE_1_POLICY_STATUS: {
    title: "Policy Validity Check",
    description: "Verifies that the policy status is currently 'Active' and that premiums are up-to-date or within the active grace period."
  },
  GATE_1_DATE_RANGE: {
    title: "Policy Date Check",
    description: "Confirms that the claim event date falls strictly within the policy's start and end dates."
  },
  GATE_2_ELIGIBILITY: {
    title: "Member Eligibility Status",
    description: "Verifies that the claimant's member profile is active and eligible to receive policy benefits."
  },
  GATE_2_ADDITION_DATE: {
    title: "Member Addition Date",
    description: "Checks that the admission or treatment date is on or after the member's official date of addition to the policy."
  },
  GATE_3_VARIANT_FILTER: {
    title: "Variant Coverage Check",
    description: "Confirms that the claimed benefit is covered under the specific variant (Classic, Select, or Elite) purchased."
  },
  GATE_3_RIDER_OPT_IN: {
    title: "Optional Rider Check",
    description: "Ensures that the corresponding optional riders (like Borderless or HeadsUp) are opted in before pay-out calculation."
  },
  R3_BEN_003_DURATION: {
    title: "Inpatient Duration Check",
    description: "Enforces a minimum stay of 2 hours for standard admissions (or 24 hours for alternative treatments) to qualify."
  },
  R3_BEN_007_PRECON: {
    title: "Domiciliary Treatment Audit",
    description: "Ensures home care treatment meets the three active criteria: medical advice, continuous care, and daily charts."
  },
  GATE_4_ACCIDENT_EXEMPT: {
    title: "Accident Exemption Check",
    description: "Checks if the claim is accident-related, which waives all waiting periods immediately."
  },
  GATE_4_INITIAL_WAIT: {
    title: "Initial 30-Day Waiting Period",
    description: "Enforces the initial 30-day wait list period unless waived by continuous prior coverage."
  },
  GATE_4_SPECIFIED_DISEASE: {
    title: "Specified Disease Wait Check",
    description: "Verifies if the diagnosed condition falls under the 24-month specified disease exclusion list."
  },
  GATE_4_PED_WAIT: {
    title: "Pre-existing Disease (PED) Check",
    description: "Verifies if the diagnosis overlaps with declared Pre-existing Diseases and checks the 36-month wait criteria."
  },
  GATE_4_PORTABILITY: {
    title: "Portability Credit Waiver",
    description: "Applies waiting period credits earned from previously ported insurance policies."
  },
  GATE_5_DETERMINISTIC_EXCL: {
    title: "Policy Exclusion Screen",
    description: "Filters out procedures (such as dental or cosmetic surgery) that are explicitly excluded under the policy terms."
  },
  GATE_5_SEMANTIC_AGENT: {
    title: "Clinical AI Semantic Screen",
    description: "Leverages the Clinical LLM to analyze unstructured diagnoses and identify cosmetic or diagnostic-only exclusions."
  },
  GATE_6_ROOM_RENT: {
    title: "Room Rent Eligibility Check",
    description: "Evaluates actual room rent charges against policy entitlement caps to detect pro-rata breaches."
  },
  GATE_6_PROLONGED_HOSP: {
    title: "Prolonged Hospitalization Audit",
    description: "Applies co-payment penalties if hospitalization duration exceeds the 168-hour continuous limit."
  },
  GATE_6_FINANCIAL_WATERFALL: {
    title: "Sum Insured Waterfall",
    description: "Apportions admissible claims sequentially across Base Sum Insured, Booster+, and ReAssure Forever pools."
  },
  GATE_7_STATE_PERSISTENCE: {
    title: "Ledger Update Sync",
    description: "Deducts payouts from active benefit limits and updates lifetime indicators (Forever trigger, Lock the Clock)."
  }
};

const formatBoldText = (text: string) => {
  const parts = text.split(/(\*\*.*?\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={index} className="text-on-surface font-semibold">{part.slice(2, -2)}</strong>;
    }
    return part;
  });
};

export default function App() {
  const {
    context, activePreset, endType, endVal, syncing, syncError,
    setEndType, setEndVal,
    handlePresetChange, updatePolicy, updateMember,
    updateLiveHealthy, updateCashBagPlus,
    updateBenefitBalance, toggleRenewalSimulation,
    updateLineItem,
    addEndorsement, removeEndorsement, syncFromDb,
  } = useClaimContext();

  const { decision, loading: adjLoading, error: adjError, submitStream } = useAdjudication();

  // AI Summary State
  const [aiSummary, setAiSummary] = useState<string | null>(null);
  const [aiSummaryLoading, setAiSummaryLoading] = useState<boolean>(false);

  // Sidebar & Layout State
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(false);
  const [memberSearchId, setMemberSearchId] = useState<string>('MEM-9921');

  // Accordion Toggle States
  const [policyOpen, setPolicyOpen] = useState<boolean>(true);
  const [memberOpen, setMemberOpen] = useState<boolean>(false);
  const [endorsementsOpen, setEndorsementsOpen] = useState<boolean>(false);
  const [lineItemsOpen, setLineItemsOpen] = useState<boolean>(true);

  // Simulated Scanning & Telemetry State
  const [scanning, setScanning] = useState<boolean>(false);
  const [visibleTraces, setVisibleTraces] = useState<any[]>([]);
  const scanIntervalRef = useRef<any>(null);

  // -----------------------------------------------------------------------
  // Multi-document upload state
  // -----------------------------------------------------------------------
  interface UploadedDoc {
    id: string;
    filename: string;
    fileSize: number;
    fileType: string;
    status: 'uploading' | 'done' | 'error';
    progress: number;
    result: DocumentExtractionResult | null;
    error: string | null;
    previewUrl: string | null;  // object URL for images
  }

  const [uploadedDocs, setUploadedDocs] = useState<UploadedDoc[]>([]);
  const [docDragOver, setDocDragOver] = useState<boolean>(false);
  const [activePreviewId, setActivePreviewId] = useState<string | null>(null);
  const docInputRef = useRef<HTMLInputElement>(null);

  // Helper to patch a single doc entry by id
  const patchDoc = useCallback((id: string, patch: Partial<UploadedDoc>) => {
    setUploadedDocs(prev => prev.map(d => d.id === id ? { ...d, ...patch } : d));
  }, []);

  // Handle real document upload + LLM extraction — supports queuing multiple files
  const handleDocumentUpload = useCallback(async (files: FileList | File[]) => {
    const allowed = ['application/pdf', 'image/png', 'image/jpeg', 'image/tiff', 'image/webp', 'image/bmp'];
    const fileArray = Array.from(files);

    for (const file of fileArray) {
      if (!allowed.includes(file.type) && !file.name.match(/\.(pdf|png|jpg|jpeg|tiff?|webp|bmp)$/i)) {
        const errId = crypto.randomUUID();
        setUploadedDocs(prev => [...prev, {
          id: errId, filename: file.name, fileSize: file.size, fileType: file.type,
          status: 'error', progress: 0, result: null,
          error: `Unsupported file type. Use PDF, PNG, JPG, or TIFF.`,
          previewUrl: null,
        }]);
        continue;
      }

      const docId = crypto.randomUUID();
      const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : null;

      // Register doc immediately in list so user sees it right away
      setUploadedDocs(prev => [...prev, {
        id: docId, filename: file.name, fileSize: file.size, fileType: file.type,
        status: 'uploading', progress: 0, result: null, error: null, previewUrl,
      }]);

      // Per-file progress ticker
      const ticker = setInterval(() => {
        patchDoc(docId, { progress: 0 }); // placeholder — overwritten below
        setUploadedDocs(prev => prev.map(d =>
          d.id === docId && d.progress < 85 ? { ...d, progress: d.progress + 10 } : d
        ));
      }, 400);

      try {
        const result = await extractDocument(file);
        clearInterval(ticker);
        patchDoc(docId, { status: 'done', progress: 100, result });

        // Auto-apply non-null fields to line item 0 (last uploaded doc wins)
        const e = result.extracted;
        if (e.discharge_summary)    updateLineItem(0, 'discharge_summary', e.discharge_summary);
        if (e.condition_diagnosed)  updateLineItem(0, 'condition_diagnosed', e.condition_diagnosed);
        if (e.admission_date)       updateLineItem(0, 'admission_date', e.admission_date);
        if (e.discharge_date)       updateLineItem(0, 'discharge_date', e.discharge_date);
        if (e.hospitalization_hours != null) updateLineItem(0, 'hospitalization_hours', e.hospitalization_hours);
        if (e.claimed_amount != null)        updateLineItem(0, 'claimed_amount', e.claimed_amount);
        if (e.room_charges != null)          updateLineItem(0, 'room_charges', e.room_charges);
        if (e.nursing_charges != null)       updateLineItem(0, 'nursing_charges', e.nursing_charges);
        if (e.medical_practitioner_fees != null) updateLineItem(0, 'medical_practitioner_fees', e.medical_practitioner_fees);
        if (e.ot_charges != null)            updateLineItem(0, 'ot_charges', e.ot_charges);
      } catch (err: any) {
        clearInterval(ticker);
        patchDoc(docId, {
          status: 'error', progress: 0,
          error: err?.message ?? 'Extraction failed. Verify the backend LLM is running.',
        });
      }
    }
  }, [updateLineItem, patchDoc]);

  const removeDoc = useCallback((id: string) => {
    setUploadedDocs(prev => {
      const doc = prev.find(d => d.id === id);
      if (doc?.previewUrl) URL.revokeObjectURL(doc.previewUrl);
      return prev.filter(d => d.id !== id);
    });
    if (activePreviewId === id) setActivePreviewId(null);
  }, [activePreviewId]);

  // Keep a local decision copy so we control the timing of when it displays (only after scanning completes)
  const [localDecision, setLocalDecision] = useState<any>(null);


  // Keep search ID in sync when changing presets
  useEffect(() => {
    setMemberSearchId(context.member.member_id);
  }, [context.member.member_id]);

  // Set local decision and trigger background summary when the streaming adjudication completes
  useEffect(() => {
    if (decision) {
      setScanning(false);
      setLocalDecision(decision);

      // Trigger background summary generation if partially approved or rejected
      if (decision.claim_decision === 'REJECTED' || decision.claim_decision === 'PARTIALLY_APPROVED') {
        setAiSummaryLoading(true);
        getClaimSummary(decision)
          .then((res) => {
            setAiSummary(res.summary);
          })
          .catch((err) => {
            console.error("AI summary error:", err);
            setAiSummary("Failed to generate AI adjudication summary. Please verify that your local LLM is active.");
          })
          .finally(() => {
            setAiSummaryLoading(false);
          });
      }
    }
  }, [decision]);


  const handleInitiateAdjudication = () => {
    setLocalDecision(null);
    setVisibleTraces([]);
    setScanning(true);
    setAiSummary(null);
    setAiSummaryLoading(false);
    void submitStream(context, (newTrace) => {
      setVisibleTraces((prev) => [...prev, newTrace]);
    });
  };

  // Clear local decision when initiating a new sync/preset change
  const handlePresetSelect = (name: string) => {
    setLocalDecision(null);
    setVisibleTraces([]);
    setScanning(false);
    handlePresetChange(name);
  };

  const handleSyncSearch = () => {
    setLocalDecision(null);
    setVisibleTraces([]);
    setScanning(false);
    void syncFromDb(memberSearchId);
  };

  // Cleanup timers
  useEffect(() => {
    return () => {
      if (scanIntervalRef.current) clearInterval(scanIntervalRef.current);
    };
  }, []);

  const formatCurrency = (val: number | undefined) =>
    val === undefined
      ? '₹0'
      : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(val);

  // Financial segments calculations
  const claimedAmount = localDecision?.total_claimed || 0;
  const deductibleAmt = 
    (localDecision?.deduction_breakdown?.deductible || 0) +
    (localDecision?.deduction_breakdown?.room_pro_rata || 0) +
    (localDecision?.deduction_breakdown?.non_payable_items || 0) +
    (localDecision?.deduction_breakdown?.lock_the_clock_premium_delta || 0);
  const copayAmt = localDecision?.deduction_breakdown?.co_payment || 0;
  const payableAmt = localDecision?.total_payable || 0;

  const totalSegmentScale = claimedAmount || 1;
  const payablePercent = Math.max(0, Math.min(100, (payableAmt / totalSegmentScale) * 100));
  const deductiblePercent = Math.max(0, Math.min(100, (deductibleAmt / totalSegmentScale) * 100));
  const copayPercent = Math.max(0, Math.min(100, (copayAmt / totalSegmentScale) * 100));

  return (
    <div className="min-h-screen bg-surface-dim font-sans text-on-surface antialiased flex flex-col">
      {/* Top Navigation */}
      <header className="flex justify-between items-center w-full px-8 h-20 bg-white/70 backdrop-blur-xl border-b border-outline-variant/30 fixed top-0 z-50 shadow-soft">
        <div className="flex items-center gap-4">
          <button 
            className="p-2 hover:bg-surface-container-high rounded-full transition-colors text-on-surface-variant cursor-pointer"
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          >
            <span className="material-symbols-outlined">menu</span>
          </button>
          <div className="flex items-center gap-2 ml-2">
            <span className="material-symbols-outlined filled text-primary text-3xl">health_and_safety</span>
            <span className="font-display text-xl font-bold tracking-tight">
              ReAssure <span className="text-primary font-medium">Copilot</span>
            </span>
          </div>
        </div>
        <div className="flex items-center gap-6">
          <div className="flex items-center bg-surface-container/50 rounded-full px-2 py-1 border border-outline-variant/40">
            <button className="p-2 text-on-surface-variant hover:text-primary transition-colors material-symbols-outlined text-[22px] cursor-pointer">search</button>
            <button className="p-2 text-on-surface-variant hover:text-primary transition-colors material-symbols-outlined text-[22px] cursor-pointer">notifications</button>
            <button className="p-2 text-on-surface-variant hover:text-primary transition-colors material-symbols-outlined text-[22px] cursor-pointer">help</button>
          </div>
          <div className="h-10 w-10 rounded-full overflow-hidden border border-primary/20 shadow-sm ring-4 ring-primary/5">
            <img 
              alt="User profile" 
              className="w-full h-full object-cover" 
              src="https://lh3.googleusercontent.com/aida-public/AB6AXuAo3jxEY86JRwmi6i58_fVqPIU8bffPVLL8LF_iAbEKO6vCaeMf2FSBq-nAZ6MkNaUa8q5wa3h5khHHG9GUVNpIqKouhEEFrW-l_KC5UEgqTLz3jCi4iEjXN9vpeDdJBddPI8d9lJZHzdg3ZnMuF_Mwnpnv2BTT_eGu9KHAonRRXZjs4-X2Jbv8acajT4_7eTo6k9BVfO27E86TCTq7udYH89fjgay0UpDpLDsB9X0oa5dkqk0DgU1aKJIgsQjCeSKyWj9iuvK6BA"
            />
          </div>
        </div>
      </header>

      {/* Sidebar */}
      <aside 
        id="sidebar"
        className={`flex flex-col h-screen py-6 bg-white/80 backdrop-blur-md border-r border-outline-variant/30 fixed left-0 top-20 z-40 shadow-soft transition-all duration-300 ${sidebarCollapsed ? 'w-20' : 'w-64'}`}
      >
        <nav className="flex-1 px-3 space-y-1 mt-2">
          <button className="flex items-center w-full px-4 py-3 gap-4 bg-primary/5 text-primary font-semibold rounded-2xl transition-all cursor-pointer">
            <span className="material-symbols-outlined filled">dashboard</span>
            {!sidebarCollapsed && <span className="font-body-md whitespace-nowrap">Ingestion Hub</span>}
          </button>
          <button className="flex items-center w-full px-4 py-3 gap-4 text-on-surface-variant hover:text-primary hover:bg-surface-container-high rounded-2xl transition-all cursor-pointer">
            <span className="material-symbols-outlined">folder_shared</span>
            {!sidebarCollapsed && <span className="font-body-md whitespace-nowrap">Claim History</span>}
          </button>
          <button className="flex items-center w-full px-4 py-3 gap-4 text-on-surface-variant hover:text-primary hover:bg-surface-container-high rounded-2xl transition-all cursor-pointer">
            <span className="material-symbols-outlined">analytics</span>
            {!sidebarCollapsed && <span className="font-body-md whitespace-nowrap">Performance</span>}
          </button>
        </nav>
        <div className="px-3 pb-32 space-y-1">
          <button className="flex items-center gap-4 w-full px-4 py-3 text-on-surface-variant hover:text-primary rounded-2xl hover:bg-surface-container-high transition-colors cursor-pointer">
            <span className="material-symbols-outlined">settings</span>
            {!sidebarCollapsed && <span className="font-body-md whitespace-nowrap">Configuration</span>}
          </button>
          <button className="flex items-center gap-4 w-full px-4 py-3 text-on-surface-variant hover:text-error rounded-2xl hover:bg-error-container/20 transition-colors cursor-pointer">
            <span className="material-symbols-outlined">logout</span>
            {!sidebarCollapsed && <span className="sidebar-text font-body-md whitespace-nowrap">Sign Out</span>}
          </button>
        </div>
      </aside>

      {/* Main Workspace */}
      <main 
        id="main-content"
        className={`mt-20 p-8 h-[calc(100vh-80px)] overflow-y-auto custom-scrollbar transition-all duration-300 ${sidebarCollapsed ? 'ml-20' : 'ml-64'}`}
      >
        <div className="max-w-[1400px] mx-auto">
          {/* Greeting Header */}
          <header className="mb-8">
            <h1 className="font-display text-3xl font-bold tracking-tight">Good morning, Mr. Manvik</h1>
            <p className="text-on-surface-variant text-sm mt-1">Ready to adjudicate today's claims?</p>
          </header>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
            
            {/* LEFT COLUMN: Ingestion Hub */}
            <section className="lg:col-span-5 flex flex-col gap-6">
              
              {/* Preset Selector */}
              <div className="bg-white p-6 rounded-2xl shadow-card border border-outline-variant/30">
                <h3 className="font-display text-xs text-on-surface-variant tracking-wider uppercase mb-3 font-semibold">CLAIM SELECTION</h3>
                <div className="relative group">
                  <select 
                    value={activePreset}
                    onChange={(e) => handlePresetSelect(e.target.value)}
                    className="w-full bg-surface-container-low border border-outline-variant/50 text-on-surface px-4 py-4 rounded-xl focus:ring-2 focus:ring-primary/50 outline-none appearance-none font-body-md transition-shadow group-hover:shadow-sm cursor-pointer"
                  >
                    <option value="case1">MEM-9921 — Case 1: Room Rent Pro-Rata Breach</option>
                    <option value="case2">MEM-8822 — Case 2: Lapsed Policy / Premium Unpaid</option>
                    <option value="case3">MEM-7723 — Case 3: Wellness Points Conversion</option>
                    <option value="case4">MEM-6624 — Case 4: ReAssure Forever Waterfall</option>
                    <option value="case5">MEM-5525 — Case 5: Clean Approval (Cholecystectomy)</option>
                    <option value="case6">MEM-4426 — Case 6: Multi-Line (Surgery + Physio + Cash)</option>
                    <option value="case7">MEM-3327 — Case 7: Deductible Option Applied</option>
                    <option value="case8">MEM-2228 — Case 8: Suite Upgrade & Co-payment</option>
                    <option value="case9">MEM-1129 — Case 9: Porting Credit Waiting Period Waiver</option>
                    <option value="case10">MEM-1010 — Case 10: Tiered Network Penalty</option>
                  </select>
                  <span className="material-symbols-outlined absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none text-on-surface-variant">unfold_more</span>
                </div>
              </div>

              {/* Patient Verification (Sync Search) */}
              <div className="bg-white p-6 rounded-2xl shadow-card border border-outline-variant/30">
                <h3 className="font-display text-xs text-on-surface-variant tracking-wider uppercase mb-3 font-semibold">PATIENT VERIFICATION</h3>
                <div className="flex gap-4">
                  <div className="relative flex-1 group">
                    <input 
                      type="text"
                      value={memberSearchId}
                      onChange={(e) => setMemberSearchId(e.target.value)}
                      placeholder="Enter Member ID (e.g. MEM-9921)"
                      className="w-full bg-surface-container-low border border-outline-variant/50 text-on-surface px-4 py-4 pl-12 rounded-xl focus:ring-2 focus:ring-primary/50 outline-none font-body-md transition-shadow group-hover:shadow-sm"
                    />
                    <span className="material-symbols-outlined absolute left-4 top-1/2 -translate-y-1/2 text-on-surface-variant">search</span>
                  </div>
                  <button 
                    onClick={handleSyncSearch}
                    disabled={syncing}
                    className="px-6 py-4 bg-white border border-outline-variant/50 text-primary font-semibold hover:border-primary/50 hover:bg-primary/5 transition-all rounded-xl shadow-sm cursor-pointer disabled:opacity-50"
                  >
                    {syncing ? 'SYNCING...' : 'SYNC'}
                  </button>
                </div>
                {syncError && <p className="text-error text-xs mt-2 font-medium">{syncError}</p>}
              </div>

              {/* Accordion Forms */}
              <div className="space-y-4">
                
                {/* Accordion 1: Policy Governance */}
                <div className="bg-white border border-outline-variant/30 rounded-2xl overflow-hidden shadow-card transition-all">
                  <button 
                    className="w-full px-6 py-5 flex justify-between items-center hover:bg-surface-container/50 transition-colors cursor-pointer"
                    onClick={() => setPolicyOpen(!policyOpen)}
                  >
                    <span className="font-display text-lg font-medium flex items-center gap-3 text-on-surface">
                      <span className="material-symbols-outlined text-primary">verified_user</span>
                      Policy Governance
                    </span>
                    <span className="material-symbols-outlined text-on-surface-variant">
                      {policyOpen ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {policyOpen && (
                    <div className="px-6 pb-6 grid grid-cols-2 gap-4 border-t border-outline-variant/20 pt-4">
                      <div className="space-y-2">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">POLICY ID</label>
                        <input 
                          type="text" 
                          value={context.policy.policy_id}
                          onChange={(e) => updatePolicy('policy_id', e.target.value)}
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">VARIANT</label>
                        <select 
                          value={context.policy.variant}
                          onChange={(e) => updatePolicy('variant', e.target.value)}
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none cursor-pointer"
                        >
                          <option value="Classic">Classic</option>
                          <option value="Select">Select</option>
                          <option value="Elite">Elite</option>
                        </select>
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">SUM INSURED</label>
                        <input 
                          type="number" 
                          value={context.policy.base_sum_insured}
                          onChange={(e) => updatePolicy('base_sum_insured', Number(e.target.value))}
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">ROOM ENTITLED</label>
                        <input 
                          type="text" 
                          value={context.policy.room_category_entitled}
                          onChange={(e) => updatePolicy('room_category_entitled', e.target.value)}
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                        />
                      </div>
                      
                      {/* Renewal simulations */}
                      <div className="col-span-2 flex items-center justify-between bg-surface-container/50 border border-outline-variant/30 rounded-xl p-4 mt-2">
                        <div>
                          <p className="text-xs font-semibold">Simulate End-of-Year Renewal</p>
                          <p className="text-[10px] text-on-surface-variant">Accrue / convert wellness points to wallet credits</p>
                        </div>
                        <button
                          onClick={toggleRenewalSimulation}
                          className={`w-10 h-6 rounded-full p-1 transition-smooth cursor-pointer ${context.renewal_event_simulation ? 'bg-primary' : 'bg-slate-300'}`}
                        >
                          <div className={`w-4 h-4 bg-white rounded-full transition-smooth ${context.renewal_event_simulation ? 'translate-x-4' : 'translate-x-0'}`} />
                        </button>
                      </div>
                    </div>
                  )}
                </div>

                {/* Accordion 2: Member & Lifetime State */}
                <div className="bg-white border border-outline-variant/30 rounded-2xl overflow-hidden shadow-card transition-all">
                  <button 
                    className="w-full px-6 py-5 flex justify-between items-center hover:bg-surface-container/50 transition-colors cursor-pointer"
                    onClick={() => setMemberOpen(!memberOpen)}
                  >
                    <span className="font-display text-lg font-medium flex items-center gap-3 text-on-surface">
                      <span className="material-symbols-outlined text-primary">person</span>
                      Member & Lifetime State
                    </span>
                    <span className="material-symbols-outlined text-on-surface-variant">
                      {memberOpen ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {memberOpen && (
                    <div className="px-6 pb-6 grid grid-cols-2 gap-4 border-t border-outline-variant/20 pt-4">
                      <div className="space-y-2">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">NAME</label>
                        <input 
                          type="text" 
                          value={context.member.name}
                          onChange={(e) => updateMember('name', e.target.value)}
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">AGE</label>
                        <input 
                          type="number" 
                          value={context.member.age}
                          onChange={(e) => updateMember('age', Number(e.target.value))}
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">WELLNESS POINTS</label>
                        <input 
                          type="number" 
                          value={context.lifetime_state.live_healthy.current_points}
                          onChange={(e) => updateLiveHealthy('current_points', Number(e.target.value))}
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">CASH BAG WALLET</label>
                        <input 
                          type="number" 
                          value={context.lifetime_state.cash_bag_plus.balance}
                          onChange={(e) => updateCashBagPlus('balance', Number(e.target.value))}
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">YTD DEDUCTIBLE CONSUMED</label>
                        <input 
                          type="number" 
                          value={context.benefit_balance.deductible_consumed_ytd ?? 0}
                          onChange={(e) => updateBenefitBalance('deductible_consumed_ytd', Number(e.target.value))}
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                        />
                      </div>
                    </div>
                  )}
                </div>

                {/* Accordion 3: Endorsements Timeline */}
                <div className="bg-white border border-outline-variant/30 rounded-2xl overflow-hidden shadow-card transition-all">
                  <button 
                    className="w-full px-6 py-5 flex justify-between items-center hover:bg-surface-container/50 transition-colors cursor-pointer"
                    onClick={() => setEndorsementsOpen(!endorsementsOpen)}
                  >
                    <span className="font-display text-lg font-medium flex items-center gap-3 text-on-surface">
                      <span className="material-symbols-outlined text-primary">history</span>
                      Endorsements ({context.endorsements.length})
                    </span>
                    <span className="material-symbols-outlined text-on-surface-variant">
                      {endorsementsOpen ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {endorsementsOpen && (
                    <div className="px-6 pb-6 space-y-4 border-t border-outline-variant/20 pt-4">
                      {context.endorsements.length > 0 ? (
                        <div className="space-y-2">
                          {context.endorsements.map((end) => (
                            <div key={end.endorsement_id} className="flex justify-between items-start p-3 bg-surface-container rounded-xl border border-outline-variant/30">
                              <div>
                                <p className="text-xs font-bold">{end.endorsement_type}</p>
                                <p className="text-[10px] text-on-surface-variant mt-0.5">Effective: {new Date(end.effective_date).toLocaleDateString()}</p>
                                <pre className="text-[9px] font-mono text-primary mt-1">{JSON.stringify(end.details, null, 2)}</pre>
                              </div>
                              <button 
                                onClick={() => removeEndorsement(end.endorsement_id)}
                                className="text-error hover:bg-error-container/20 p-1 rounded cursor-pointer"
                              >
                                <span className="material-symbols-outlined text-sm">delete</span>
                              </button>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-xs text-on-surface-variant text-center py-4">No active endorsements in this context.</p>
                      )}
                      
                      {/* Add Endorsement Builder */}
                      <div className="border-t border-outline-variant/20 pt-4 flex flex-col gap-3">
                        <p className="text-xs font-semibold">Add Mid-term Endorsement</p>
                        <div className="grid grid-cols-2 gap-4">
                          <div>
                            <label className="text-[9px] text-on-surface-variant font-semibold">TYPE</label>
                            <select 
                              value={endType}
                              onChange={(e) => setEndType(e.target.value)}
                              className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none cursor-pointer"
                            >
                              <option value="MemberAddition">Member Addition</option>
                              <option value="SIEnhancement">SI Enhancement</option>
                              <option value="IndividualToFloater">Individual To Floater</option>
                              <option value="FloaterSplit">Floater Split</option>
                            </select>
                          </div>
                          <div className="flex items-end">
                            <button 
                              onClick={() => {
                                const ok = addEndorsement();
                                if (!ok) alert('Invalid JSON. Correct details before adding.');
                              }}
                              className="w-full bg-primary hover:brightness-105 active:scale-[0.98] text-white py-2 rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center justify-center gap-1.5 shadow-sm"
                            >
                              <span className="material-symbols-outlined text-sm">add</span> Add Mutation
                            </button>
                          </div>
                        </div>
                        <div>
                          <label className="text-[9px] text-on-surface-variant font-semibold">PARAMETERS DETAILS (JSON)</label>
                          <textarea 
                            value={endVal}
                            onChange={(e) => setEndVal(e.target.value)}
                            rows={3}
                            className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs font-mono focus:ring-2 focus:ring-primary/50 outline-none"
                          />
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Accordion 4: Claim Line Items */}
                <div className="bg-white border border-outline-variant/30 rounded-2xl overflow-hidden shadow-card transition-all">
                  <button 
                    className="w-full px-6 py-5 flex justify-between items-center hover:bg-surface-container/50 transition-colors cursor-pointer"
                    onClick={() => setLineItemsOpen(!lineItemsOpen)}
                  >
                    <span className="font-display text-lg font-medium flex items-center gap-3 text-on-surface">
                      <span className="material-symbols-outlined text-primary">receipt_long</span>
                      Claim Line Items
                    </span>
                    <span className="material-symbols-outlined text-on-surface-variant">
                      {lineItemsOpen ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {lineItemsOpen && (
                    <div className="px-6 pb-6 space-y-4 border-t border-outline-variant/20 pt-4">
                      <div className="space-y-2">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">TREATMENT/DIAGNOSIS DESCRIPTION</label>
                        <input 
                          type="text" 
                          value={context.line_items[0]?.description}
                          onChange={(e) => updateLineItem(0, 'description', e.target.value)}
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                        />
                      </div>
                      <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                          <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">CLAIMED AMOUNT (₹)</label>
                          <input 
                            type="number" 
                            value={context.line_items[0]?.claimed_amount}
                            onChange={(e) => updateLineItem(0, 'claimed_amount', Number(e.target.value))}
                            className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                          />
                        </div>
                        <div className="space-y-2">
                          <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">ACTUAL ROOM RENT (₹/DAY)</label>
                          <input 
                            type="number" 
                            value={context.line_items[0]?.actual_room_rent ?? ''}
                            onChange={(e) => updateLineItem(0, 'actual_room_rent', Number(e.target.value))}
                            className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                          />
                        </div>
                      </div>

                      {/* Bill Itemisation */}
                      <div className="border-t border-outline-variant/20 pt-4 mt-2">
                        <p className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold mb-3">Bill Itemisation Breakdown (INR)</p>
                        <div className="grid grid-cols-2 gap-4">
                          <div className="space-y-1">
                            <label className="text-[9px] text-on-surface-variant">Room Charges</label>
                            <input 
                              type="number" 
                              value={context.line_items[0]?.room_charges || 0}
                              onChange={(e) => updateLineItem(0, 'room_charges', Number(e.target.value))}
                              className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-1.5 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                            />
                          </div>
                          <div className="space-y-1">
                            <label className="text-[9px] text-on-surface-variant">Nursing Charges</label>
                            <input 
                              type="number" 
                              value={context.line_items[0]?.nursing_charges || 0}
                              onChange={(e) => updateLineItem(0, 'nursing_charges', Number(e.target.value))}
                              className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-1.5 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                            />
                          </div>
                          <div className="space-y-1">
                            <label className="text-[9px] text-on-surface-variant">Practitioner Fees</label>
                            <input 
                              type="number" 
                              value={context.line_items[0]?.medical_practitioner_fees || 0}
                              onChange={(e) => updateLineItem(0, 'medical_practitioner_fees', Number(e.target.value))}
                              className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-1.5 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                            />
                          </div>
                          <div className="space-y-1">
                            <label className="text-[9px] text-on-surface-variant">OT Charges</label>
                            <input 
                              type="number" 
                              value={context.line_items[0]?.ot_charges || 0}
                              onChange={(e) => updateLineItem(0, 'ot_charges', Number(e.target.value))}
                              className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-1.5 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none"
                            />
                          </div>
                        </div>
                      </div>

                      <div className="space-y-2 border-t border-outline-variant/20 pt-4">
                        <label className="text-[10px] text-on-surface-variant tracking-wider uppercase font-semibold">DISCHARGE SUMMARY TEXT</label>
                        <textarea 
                          value={context.line_items[0]?.discharge_summary || ''}
                          onChange={(e) => updateLineItem(0, 'discharge_summary', e.target.value)}
                          rows={3}
                          placeholder="Enter patient discharge summary text for AI exclusion check..."
                          className="w-full bg-surface-container-low border border-outline-variant/50 rounded-xl py-2 px-3 text-xs focus:ring-2 focus:ring-primary/50 outline-none resize-none"
                        />
                      </div>
                    </div>
                  )}
                </div>

              </div>

              {/* Document Upload Zone */}
              <div className="bg-white border border-outline-variant/30 rounded-2xl overflow-hidden shadow-card">
                {/* Hidden multi-file input */}
                <input
                  ref={docInputRef}
                  type="file"
                  multiple
                  accept=".pdf,.png,.jpg,.jpeg,.tiff,.tif,.webp,.bmp"
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files?.length) void handleDocumentUpload(e.target.files);
                    e.target.value = '';
                  }}
                />

                {/* Drop zone */}
                <div
                  onClick={() => docInputRef.current?.click()}
                  onDragOver={(e) => { e.preventDefault(); setDocDragOver(true); }}
                  onDragLeave={() => setDocDragOver(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDocDragOver(false);
                    if (e.dataTransfer.files.length) void handleDocumentUpload(e.dataTransfer.files);
                  }}
                  className={`relative flex flex-col items-center gap-3 p-5 cursor-pointer transition-all
                    ${uploadedDocs.length > 0 ? 'border-b border-outline-variant/20' : ''}
                    ${docDragOver ? 'bg-primary/8 ring-2 ring-primary/30 ring-inset' : 'hover:bg-surface-container/40'}`}
                >
                  <div className="w-10 h-10 bg-primary/5 rounded-xl flex items-center justify-center">
                    <span className="material-symbols-outlined text-primary text-2xl">upload_file</span>
                  </div>
                  <div className="text-center">
                    <p className="font-display font-bold text-sm text-on-surface">Clinical Document Upload</p>
                    <p className="text-on-surface-variant text-xs mt-0.5">
                      {docDragOver ? 'Release to add files' : 'Drop files or click to browse'}
                    </p>
                    <p className="text-[10px] text-on-surface-variant/50 mt-1">PDF · PNG · JPG · TIFF — multiple files supported</p>
                  </div>
                </div>

                {/* Uploaded files list */}
                {uploadedDocs.length > 0 && (
                  <div className="divide-y divide-outline-variant/15">
                    {uploadedDocs.map(doc => (
                      <div key={doc.id} className="relative">
                        <div
                          onClick={() => doc.status === 'done' && setActivePreviewId(activePreviewId === doc.id ? null : doc.id)}
                          className={`flex items-center gap-3 px-4 py-3 transition-colors
                            ${doc.status === 'done' ? 'cursor-pointer hover:bg-surface-container/50' : 'cursor-default'}`}
                        >
                          {/* File type icon */}
                          <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0
                            ${doc.status === 'error' ? 'bg-red-50' : doc.status === 'uploading' ? 'bg-amber-50' : 'bg-primary/5'}`}>
                            <span className={`material-symbols-outlined text-[18px]
                              ${doc.status === 'error' ? 'text-red-500' : doc.status === 'uploading' ? 'text-amber-500' : 'text-primary'}`}>
                              {doc.status === 'error' ? 'error' : doc.fileType === 'application/pdf' ? 'picture_as_pdf' : 'image'}
                            </span>
                          </div>

                          {/* File info */}
                          <div className="flex-1 min-w-0">
                            <p className="text-xs font-medium text-on-surface truncate">{doc.filename}</p>
                            {doc.status === 'uploading' && (
                              <div className="mt-1.5 h-1 bg-surface-container-high rounded-full overflow-hidden w-full">
                                <div
                                  className="h-full bg-gradient-primary rounded-full transition-all duration-300"
                                  style={{ width: `${doc.progress}%` }}
                                />
                              </div>
                            )}
                            {doc.status === 'done' && doc.result && (
                              <p className="text-[10px] text-on-surface-variant mt-0.5 truncate">
                                {doc.result.extracted.condition_diagnosed ?? 'Extraction complete'}
                                {doc.result.extracted.claimed_amount != null &&
                                  ` · ₹${doc.result.extracted.claimed_amount.toLocaleString()}`}
                              </p>
                            )}
                            {doc.status === 'error' && (
                              <p className="text-[10px] text-red-600 mt-0.5 truncate">{doc.error}</p>
                            )}
                          </div>

                          {/* Status badge */}
                          {doc.status === 'done' && (
                            <span className="text-[9px] font-bold px-2 py-0.5 rounded-full bg-primary/8 text-primary border border-primary/15 uppercase shrink-0">
                              {activePreviewId === doc.id ? 'HIDE' : 'VIEW'}
                            </span>
                          )}
                          {doc.status === 'uploading' && (
                            <span className="text-[9px] text-amber-600 font-bold uppercase shrink-0 animate-pulse">PARSING</span>
                          )}

                          {/* Remove button */}
                          <button
                            onClick={(e) => { e.stopPropagation(); removeDoc(doc.id); }}
                            className="w-6 h-6 rounded-full hover:bg-red-50 flex items-center justify-center transition-colors shrink-0 ml-1"
                            title="Remove file"
                          >
                            <span className="material-symbols-outlined text-[14px] text-on-surface-variant hover:text-red-500 transition-colors">close</span>
                          </button>
                        </div>

                        {/* Inline preview / detail drawer */}
                        {activePreviewId === doc.id && doc.result && (
                          <div className="bg-surface-container/40 border-t border-outline-variant/20 px-4 py-4 space-y-3">
                            {/* Image thumbnail */}
                            {doc.previewUrl && (
                              <img
                                src={doc.previewUrl}
                                alt={doc.filename}
                                className="w-full max-h-40 object-contain rounded-xl border border-outline-variant/30 bg-white"
                              />
                            )}

                            {/* Extracted fields grid */}
                            <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-[10px]">
                              <span className="text-on-surface-variant col-span-2 font-semibold uppercase tracking-wider text-[9px]">Extracted Fields</span>
                              {doc.result.extracted.condition_diagnosed && (<>
                                <span className="text-on-surface-variant">Diagnosis</span>
                                <span className="text-on-surface font-medium">{doc.result.extracted.condition_diagnosed}</span>
                              </>)}
                              {doc.result.extracted.admission_date && (<>
                                <span className="text-on-surface-variant">Admission</span>
                                <span className="text-on-surface font-medium">{new Date(doc.result.extracted.admission_date).toLocaleDateString()}</span>
                              </>)}
                              {doc.result.extracted.discharge_date && (<>
                                <span className="text-on-surface-variant">Discharge</span>
                                <span className="text-on-surface font-medium">{new Date(doc.result.extracted.discharge_date).toLocaleDateString()}</span>
                              </>)}
                              {doc.result.extracted.hospitalization_hours != null && (<>
                                <span className="text-on-surface-variant">Duration</span>
                                <span className="text-on-surface font-medium">{doc.result.extracted.hospitalization_hours}h</span>
                              </>)}
                              {doc.result.extracted.claimed_amount != null && (<>
                                <span className="text-on-surface-variant">Total Bill</span>
                                <span className="text-on-surface font-medium">₹{doc.result.extracted.claimed_amount.toLocaleString()}</span>
                              </>)}
                              {doc.result.extracted.room_charges != null && (<>
                                <span className="text-on-surface-variant">Room</span>
                                <span className="text-on-surface font-medium">₹{doc.result.extracted.room_charges.toLocaleString()}</span>
                              </>)}
                              {doc.result.extracted.nursing_charges != null && (<>
                                <span className="text-on-surface-variant">Nursing</span>
                                <span className="text-on-surface font-medium">₹{doc.result.extracted.nursing_charges.toLocaleString()}</span>
                              </>)}
                              {doc.result.extracted.medical_practitioner_fees != null && (<>
                                <span className="text-on-surface-variant">Doctor Fees</span>
                                <span className="text-on-surface font-medium">₹{doc.result.extracted.medical_practitioner_fees.toLocaleString()}</span>
                              </>)}
                              {doc.result.extracted.ot_charges != null && (<>
                                <span className="text-on-surface-variant">OT Charges</span>
                                <span className="text-on-surface font-medium">₹{doc.result.extracted.ot_charges.toLocaleString()}</span>
                              </>)}
                              <span className="text-on-surface-variant">Raw text</span>
                              <span className="text-on-surface font-medium">{doc.result.raw_text_length.toLocaleString()} chars</span>
                            </div>

                            {/* Discharge summary */}
                            {doc.result.extracted.discharge_summary && (
                              <div className="border-t border-outline-variant/20 pt-3">
                                <p className="text-[9px] text-on-surface-variant uppercase tracking-wider font-semibold mb-1.5">Discharge Summary</p>
                                <p className="text-[10px] text-on-surface-variant leading-relaxed">
                                  {doc.result.extracted.discharge_summary}
                                </p>
                              </div>
                            )}

                            <p className="text-[9px] text-on-surface-variant/50 pt-1">Fields applied to Claim Line Items — edit them above if needed.</p>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Main Submit Action */}
              <button 
                onClick={handleInitiateAdjudication}
                disabled={adjLoading || scanning}
                className="w-full px-6 py-5 bg-gradient-primary text-white font-bold rounded-2xl shadow-glow hover:brightness-105 hover:-translate-y-0.5 active:translate-y-0 transition-all duration-300 flex items-center justify-center gap-3 relative z-10 cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
              >
                <span className="material-symbols-outlined filled">security_update_good</span>
                <span className="tracking-wide uppercase text-sm">
                  {scanning ? 'SCANNING RUNTIMES...' : adjLoading ? 'PROCESSING...' : 'Initiate Adjudication'}
                </span>
              </button>

            </section>

            {/* RIGHT COLUMN: Telemetry Engine & Calculations */}
            <section className="lg:col-span-7 flex flex-col gap-6 h-full max-h-[1050px]">
              
              {/* Telemetry Control Header */}
              <div className="flex justify-between items-center bg-white p-6 rounded-2xl border border-outline-variant/30 shadow-card relative overflow-hidden">
                <div className="absolute -right-20 -top-20 w-64 h-64 bg-primary/5 rounded-full blur-3xl pointer-events-none" />
                <div className="flex items-center gap-4 relative z-10">
                  <div className="w-14 h-14 rounded-2xl bg-primary/5 flex items-center justify-center border border-primary/10 shadow-inner">
                    <span className="material-symbols-outlined text-primary text-3xl">biotech</span>
                  </div>
                  <div>
                    <h2 className="font-display text-on-surface text-lg font-bold">Live Scan Engine</h2>
                    <p className="text-on-surface-variant text-xs mt-0.5">Continuous policy validation engine V3.0</p>
                  </div>
                </div>
              </div>

              {/* Checklist & Verification Console */}
              <div className="flex-1 bg-white border border-outline-variant/30 rounded-2xl shadow-card relative flex flex-col min-h-[350px]">
                <div className="px-6 pt-5 pb-3 border-b border-outline-variant/20 flex items-center justify-between">
                  <div className="text-on-surface-variant text-[11px] font-semibold tracking-wider uppercase flex gap-2 items-center">
                    <span className="material-symbols-outlined text-[16px]">memory</span>
                    CORE LOGS: REASSURE_ENGINE_3.0
                  </div>
                  <span className={`w-2 h-2 rounded-full ${scanning ? 'bg-amber-400 animate-ping' : 'bg-primary'}`} />
                </div>
                
                <div className="flex-1 overflow-y-auto custom-scrollbar p-6 space-y-3">
                  {adjError && (
                    <div className="p-4 bg-error/5 border border-error/20 text-error rounded-xl flex items-start gap-2.5 text-xs mb-3 font-medium">
                      <span className="material-symbols-outlined text-[20px]">error</span>
                      <div>
                        <p className="font-bold">Adjudication API Error</p>
                        <p className="text-[10px] text-error mt-0.5">{adjError}</p>
                      </div>
                    </div>
                  )}
                  {visibleTraces.length > 0 ? (
                    <div className="space-y-3">
                       {visibleTraces.map((trace, idx) => {
                        if (!trace || !trace.rule_id) return null;
                        const def = RULE_DEFINITIONS[trace.rule_id] || { title: trace.rule_name || trace.rule_id, description: trace.reason };
                        const isSuccess = trace.evaluation === 'PASSED' || trace.evaluation === 'NOT_APPLICABLE' || trace.passed === true;
                        
                        return (
                          <div 
                            key={idx} 
                            className="scan-item active flex justify-between items-center p-4 bg-surface-container-low rounded-2xl border border-outline-variant/30 shadow-sm transition-all"
                          >
                            <div className="flex items-center gap-3">
                              <span className={`material-symbols-outlined filled text-[22px] ${isSuccess ? 'text-primary' : 'text-error'}`}>
                                {isSuccess ? 'check_circle' : 'cancel'}
                              </span>
                              <div>
                                <span className="font-display text-sm font-semibold text-on-surface">{def.title}</span>
                                <p className="text-[10px] text-on-surface-variant mt-0.5">{trace.reason}</p>
                              </div>
                            </div>
                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-lg border uppercase ${isSuccess ? 'text-primary bg-primary/5 border-primary/10' : 'text-error bg-error/5 border-error/10'}`}>
                              {isSuccess ? 'VERIFIED' : 'FAILED'}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full opacity-35 text-center py-12">
                      <span className="material-symbols-outlined text-5xl mb-3 text-on-surface-variant">clinical_notes</span>
                      <p className="text-sm font-medium">Engine idle. Awaiting command parameters.</p>
                    </div>
                  )}
                </div>

                {/* Final Decision Banner (Slides up once scanning completes and localDecision is ready) */}
                {localDecision && !scanning && (
                  <div className="mx-6 mb-6 p-5 bg-surface-container-high rounded-2xl flex flex-col items-center gap-3 decision-reveal border border-outline-variant/50">
                    <div className="flex items-center gap-3">
                      <span className={`material-symbols-outlined filled text-4xl ${localDecision.claim_decision === 'REJECTED' ? 'text-error' : 'text-primary'}`}>
                        {localDecision.claim_decision === 'REJECTED' ? 'cancel' : 'verified'}
                      </span>
                      <h1 className="text-xl font-display font-bold tracking-tight">
                        CLAIM {localDecision.claim_decision}
                      </h1>
                    </div>
                    
                    <div className="grid grid-cols-3 gap-3 w-full text-xs text-center mt-2">
                      <div className="bg-white p-2.5 rounded-xl border border-outline-variant">
                        <span className="block text-on-surface-variant text-[9px] font-bold uppercase tracking-wider mb-0.5">PAYOUT</span>
                        <span className="font-bold text-on-surface">{formatCurrency(localDecision.total_payable)}</span>
                      </div>
                      <div className="bg-white p-2.5 rounded-xl border border-outline-variant">
                        <span className="block text-on-surface-variant text-[9px] font-bold uppercase tracking-wider mb-0.5">STATUS</span>
                        <span className={`font-bold uppercase ${localDecision.manual_review_required ? 'text-amber-500' : 'text-primary'}`}>
                          {localDecision.manual_review_required ? 'REFERRED' : 'AUTO_PAY'}
                        </span>
                      </div>
                      <div className="bg-white p-2.5 rounded-xl border border-outline-variant">
                        <span className="block text-on-surface-variant text-[9px] font-bold uppercase tracking-wider mb-0.5">LATENCY</span>
                        <span className="font-bold text-on-surface">320ms</span>
                      </div>
                    </div>

                    {/* Manual Review reasons */}
                    {localDecision.manual_review_required && localDecision.review_reasons && localDecision.review_reasons.length > 0 && (
                      <div className="w-full mt-2 p-3 bg-amber-500/5 border border-amber-500/20 rounded-xl text-left">
                        <p className="text-[10px] text-amber-500 font-bold uppercase mb-1">Human Intervention Flags:</p>
                        <ul className="list-disc pl-4 text-[10px] text-on-surface-variant space-y-0.5">
                          {localDecision.review_reasons.map((r: string, idx: number) => (
                            <li key={idx}>{r}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

              </div>

              {/* Financial Waterfall - Fixed at bottom */}
              <div className="bg-white p-6 border border-outline-variant/30 rounded-2xl shadow-card space-y-4 mt-auto">
                <div className="flex justify-between items-center">
                  <h3 className="text-[10px] text-on-surface-variant font-bold uppercase tracking-wider">FINANCIAL WATERFALL — INR (₹)</h3>
                  <span className="text-[10px] bg-surface-container-high px-2.5 py-0.5 rounded-full font-semibold text-on-surface-variant">
                    REAL-TIME
                  </span>
                </div>
                
                <div className="space-y-4">
                  {/* Visual Bar Segment Chart */}
                  <div className="relative h-8 w-full bg-surface-container-high rounded-xl flex overflow-hidden border border-outline-variant/20">
                    <div 
                      className="waterfall-segment h-full bg-gradient-primary" 
                      style={{ width: scanning ? '0%' : `${payablePercent}%` }}
                    />
                    <div 
                      className="waterfall-segment h-full bg-[#ffb74d]" 
                      style={{ width: scanning ? '0%' : `${deductiblePercent}%` }}
                    />
                    <div 
                      className="waterfall-segment h-full bg-[#4db6ac]" 
                      style={{ width: scanning ? '0%' : `${copayPercent}%` }}
                    />
                  </div>

                  {/* Detailed Grid Values */}
                  <div className="grid grid-cols-4 gap-2">
                    <div className="flex flex-col gap-0.5 border-l-2 border-primary/40 pl-2">
                      <span className="text-[9px] text-on-surface-variant font-semibold">CLAIMED</span>
                      <span className="font-display text-base text-on-surface font-bold">
                        {formatCurrency(claimedAmount || context.line_items[0]?.claimed_amount)}
                      </span>
                    </div>
                    <div className="flex flex-col gap-0.5 border-l-2 border-[#ffb74d]/40 pl-2">
                      <span className="text-[9px] text-on-surface-variant font-semibold">EXCLUSIONS</span>
                      <span className="font-display text-base text-on-surface font-bold">
                        {formatCurrency(deductibleAmt)}
                      </span>
                    </div>
                    <div className="flex flex-col gap-0.5 border-l-2 border-[#4db6ac]/40 pl-2">
                      <span className="text-[9px] text-on-surface-variant font-semibold">COPAY</span>
                      <span className="font-display text-base text-on-surface font-bold">
                        {formatCurrency(copayAmt)}
                      </span>
                    </div>
                    <div className="flex flex-col gap-0.5 border-l-2 border-primary pl-2">
                      <span className="text-[9px] text-primary font-bold">PAYABLE</span>
                      <span className="font-display text-base text-primary font-bold">
                        {formatCurrency(payableAmt)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* AI Adjudication Explanation Summary */}
              {(aiSummaryLoading || aiSummary) && (
                <div className="bg-white p-6 border border-outline-variant/30 rounded-2xl shadow-card space-y-4">
                  <div className="flex justify-between items-center">
                    <h3 className="text-[10px] text-primary font-bold uppercase tracking-wider flex items-center gap-1.5 font-semibold">
                      <span className="material-symbols-outlined text-sm">psychology</span>
                      AI Decision Explanation
                    </h3>
                    {aiSummaryLoading && (
                      <span className="text-[9px] text-primary/70 animate-pulse font-semibold">
                        Summarizing...
                      </span>
                    )}
                  </div>

                  {aiSummaryLoading ? (
                    <div className="flex flex-col items-center justify-center py-6 space-y-3">
                      <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin"></div>
                      <p className="text-xs text-on-surface-variant font-medium animate-pulse">
                        Analyzing decision traces and calculating financial outcomes...
                      </p>
                    </div>
                  ) : (
                    <div className="text-xs text-on-surface-variant leading-relaxed space-y-2 prose prose-sm max-w-none text-left">
                      {aiSummary && aiSummary.split('\n').map((line, idx) => {
                        let trimmed = line.trim();
                        if (trimmed.length === 0) return <div key={idx} className="h-2" />;
                        if (trimmed.startsWith('###')) {
                          return <h4 key={idx} className="font-display text-xs font-bold text-on-surface mt-3 mb-1">{trimmed.replace('###', '').trim()}</h4>;
                        }
                        if (trimmed.startsWith('##')) {
                          return <h3 key={idx} className="font-display text-sm font-bold text-on-surface mt-4 mb-2">{trimmed.replace('##', '').trim()}</h3>;
                        }
                        if (trimmed.startsWith('-') || trimmed.startsWith('*')) {
                          return (
                            <div key={idx} className="flex gap-2 items-start pl-2">
                              <span className="text-primary font-bold">•</span>
                              <span>{formatBoldText(trimmed.substring(1).trim())}</span>
                            </div>
                          );
                        }
                        if (trimmed.match(/^\d+\./)) {
                          const numMatch = trimmed.match(/^\d+\./);
                          const prefix = numMatch ? numMatch[0] : '';
                          return (
                            <div key={idx} className="flex gap-2 items-start pl-2">
                              <span className="text-primary font-bold">{prefix}</span>
                              <span>{formatBoldText(trimmed.replace(/^\d+\./, '').trim())}</span>
                            </div>
                          );
                        }
                        return <p key={idx} className="mb-1">{formatBoldText(trimmed)}</p>;
                      })}
                    </div>
                  )}
                </div>
              )}

            </section>

          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="py-6 text-center text-on-surface-variant border-t border-outline-variant/30 text-xs mt-12 bg-white/50">
        <p>© 2026 Niva Bupa Health Insurance. AI-First Auto-Adjudication Copilot Console.</p>
      </footer>
    </div>
  );
}
